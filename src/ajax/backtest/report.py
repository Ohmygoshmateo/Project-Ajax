"""Backtest reporting, including the non-suppressible caveat block.

Every report states what it approximated. That is not boilerplate: this backtest
is optimistic by construction, and a number presented without its caveats would
be actively misleading about how much money the strategy can be expected to make.
There is deliberately no flag to turn the caveats off.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ajax.backtest.engine import BacktestResult, trades_to_frame
from ajax.backtest.metrics import Metrics
from ajax.config import Config


def build_caveats(result: BacktestResult, cfg: Config) -> list[str]:
    """The caveats that apply to this specific run."""
    caveats: list[str] = []

    counts = result.price_source_counts
    if not counts:
        # A single-source run (not the chained stack) records no counts, so fall
        # back to the trades themselves. Never leave the provenance unstated —
        # this caveat matters most in exactly the case where everything was
        # modelled.
        counts = {}
        for trade in result.trades:
            counts[trade.entry_source] = counts.get(trade.entry_source, 0) + 1

    modelled = counts.get("black_scholes", 0)
    real = counts.get("alpaca_bars", 0)
    total = modelled + real

    if total and modelled and real:
        caveats.append(
            f"{modelled / total:.0%} of prices were MODEL-DERIVED (Black-Scholes), not traded "
            f"prices ({real} real bars, {modelled} modelled)."
        )
    elif modelled:
        caveats.append(
            "ALL prices were MODEL-DERIVED (Black-Scholes). No real traded option price "
            "entered this backtest."
        )
    elif real:
        caveats.append(f"All {real} prices came from real historical option bars.")
    elif result.trades:
        caveats.append("Price provenance could not be determined for this run.")

    if modelled:
        caveats.append(
            f"Modelled entries used realized volatility plus a {cfg.backtest.vol_haircut_points:.1f} "
            f"point haircut. Implied volatility exceeds subsequently realized volatility roughly "
            f"85% of the time, so without this haircut the options would be priced too cheaply. "
            f"The haircut is an approximation, not a correction."
        )

    caveats.append(
        f"Bid-ask spreads are MODELLED at {cfg.backtest.spread_pct_of_mid.default:.0%} of mid "
        f"({cfg.backtest.spread_pct_of_mid.wide_dte:.0%} under 28 DTE), not actual historical "
        f"NBBO, which is unavailable at free tiers. Buys fill at the modelled ask, sells at the bid."
    )
    caveats.append(
        f"Commissions charged at ${cfg.account.commission_per_contract:.2f} per contract per leg."
    )
    caveats.append(
        "Contract DELTAS are always modelled. Real historical greeks do not exist at any "
        "accessible price, so strike selection is approximate even where pricing used real bars."
    )
    caveats.append(f"Index membership: {result.constituent_reconstruction}.")
    caveats.append(
        "News is NOT factored into these results. Historical news is not retrievable for free, "
        "so it is a live/paper-trading feature only."
    )
    caveats.append(
        "TAKEN TOGETHER, THESE RESULTS ARE AN UPPER BOUND on what the strategy could have "
        "achieved. Treat them as a screen for obviously-broken logic, not as a forecast."
    )

    caveats.extend(result.warnings)
    return caveats


def render_console(result: BacktestResult, metrics: Metrics, cfg: Config) -> None:
    console = Console()

    console.print()
    console.rule("[bold]Backtest results")
    console.print(f"Window: [cyan]{result.start}[/] to [cyan]{result.end}[/]  "
                  f"({result.signal_days} signal days)")

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim")
    table.add_column()

    table.add_row("Win rate", f"[bold]{metrics.win_rate_display}[/]")
    table.add_row("Total P&L", _money(metrics.total_pnl))
    table.add_row("Avg P&L / trade", _money(metrics.avg_pnl))
    table.add_row("Avg return / trade", _pct(metrics.avg_return_pct))
    table.add_row("Avg win / avg loss", f"{_money(metrics.avg_win)} / {_money(metrics.avg_loss)}")
    table.add_row("Profit factor", _num(metrics.profit_factor))
    table.add_row("Expectancy", _money(metrics.expectancy))
    table.add_row("Max drawdown", f"{_money(metrics.max_drawdown)} "
                                  f"({_pct((metrics.max_drawdown_pct or 0) * 100)})")
    table.add_row("Best / worst", f"{_money(metrics.best_trade)} / {_money(metrics.worst_trade)}")
    console.print(table)

    if metrics.sample_warning:
        console.print(f"\n[yellow]⚠ {metrics.sample_warning}[/]")

    if metrics.by_source:
        console.print("\n[bold]By price source[/]")
        src = Table("source", "trades", "win rate", "total P&L", box=None)
        for name, stats in metrics.by_source.items():
            src.add_row(
                name,
                str(stats["trades"]),
                f"{stats['win_rate']:.0%} ({stats['wins']}/{stats['trades']})",
                _money(stats["total_pnl"]),
            )
        console.print(src)

    if result.skips:
        console.print("\n[bold]Skipped signals[/]")
        skip = Table("reason", "count", box=None)
        for reason, count in sorted(result.skips.items(), key=lambda kv: -kv[1]):
            skip.add_row(reason, str(count))
        console.print(skip)

    console.print()
    console.rule("[bold red]How to read these numbers")
    for caveat in build_caveats(result, cfg):
        console.print(f"  [yellow]•[/] {caveat}")
    console.print()


def write_markdown(result: BacktestResult, metrics: Metrics, cfg: Config, path: Path) -> Path:
    lines = [
        "# Ajax backtest report",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Window: {result.start} to {result.end}",
        f"- Signal days evaluated: {result.signal_days}",
        f"- Delta band: {cfg.options.delta_min:.2f}-{cfg.options.delta_max:.2f}",
        f"- DTE target: {cfg.options.dte_target_min}-{cfg.options.dte_target_max} "
        f"(hard floor {cfg.options.dte_hard_floor})",
        f"- Hold: {cfg.backtest.hold_trading_days} trading days",
        f"- Account: ${cfg.account.equity:,.0f} at {cfg.account.risk_pct_per_trade:.0%} risk/trade",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Win rate | **{metrics.win_rate_display}** |",
        f"| Total P&L | {_money(metrics.total_pnl)} |",
        f"| Avg P&L / trade | {_money(metrics.avg_pnl)} |",
        f"| Avg return / trade | {_pct(metrics.avg_return_pct)} |",
        f"| Avg win | {_money(metrics.avg_win)} |",
        f"| Avg loss | {_money(metrics.avg_loss)} |",
        f"| Profit factor | {_num(metrics.profit_factor)} |",
        f"| Expectancy | {_money(metrics.expectancy)} |",
        f"| Max drawdown | {_money(metrics.max_drawdown)} |",
        f"| Best / worst trade | {_money(metrics.best_trade)} / {_money(metrics.worst_trade)} |",
        "",
    ]

    if metrics.sample_warning:
        lines += [f"> ⚠️ {metrics.sample_warning}", ""]

    if metrics.by_source:
        lines += ["## By price source", "", "| Source | Trades | Win rate | Total P&L |",
                  "| --- | --- | --- | --- |"]
        for name, stats in metrics.by_source.items():
            lines.append(
                f"| {name} | {stats['trades']} | {stats['win_rate']:.0%} "
                f"({stats['wins']}/{stats['trades']}) | {_money(stats['total_pnl'])} |"
            )
        lines.append("")

    if result.skips:
        lines += ["## Skipped signals", "", "| Reason | Count |", "| --- | --- |"]
        for reason, count in sorted(result.skips.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {reason} | {count} |")
        lines.append("")

    lines += ["## How to read these numbers", ""]
    lines += [f"- {c}" for c in build_caveats(result, cfg)]
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return path


def write_csv(result: BacktestResult, cfg: Config, path: Path) -> Path:
    """Trade-level CSV, with the caveats carried in a comment header."""
    frame = trades_to_frame(result.trades)
    path.parent.mkdir(parents=True, exist_ok=True)

    header = ["# Ajax backtest trades — RESULTS ARE AN UPPER BOUND, see caveats below"]
    header += [f"# {c}" for c in build_caveats(result, cfg)]

    with open(path, "w", newline="") as fh:
        fh.write("\n".join(header) + "\n")
        if frame.empty:
            fh.write("no trades\n")
        else:
            frame.to_csv(fh, index=False)
    return path


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
