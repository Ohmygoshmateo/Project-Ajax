"""Ajax command line interface."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ajax import capabilities as caps_mod
from ajax.config import TUNED_CONFIG, get_config, load_config
from ajax.logging_setup import configure

app = typer.Typer(
    help="S&P 500 options swing-trade scanner, backtester, and paper-trading agent.",
    no_args_is_help=True,
    add_completion=False,
)
agent_app = typer.Typer(help="Scheduled paper-trading agent.", no_args_is_help=True)
universe_app = typer.Typer(help="S&P 500 constituent list.", no_args_is_help=True)
app.add_typer(agent_app, name="agent")
app.add_typer(universe_app, name="universe")

console = Console()


def _trade_log(cfg):  # noqa: ANN001
    from ajax.agent.trade_log import TradeLog

    return TradeLog(cfg.paths.resolve("data_cache") / "ajax_trades.db")


def _prices_or_exit(cfg, tickers: list[str] | None = None):  # noqa: ANN001
    """Load price history, or fail with a sentence instead of a traceback.

    The provider is rate-limited and free, so this is an ordinary Tuesday rather
    than an exceptional condition: an unhandled stack trace here would read as a
    broken install when it usually means "wait a minute and try again".
    """
    from ajax.agent.runner import load_prices_for_scan

    try:
        return load_prices_for_scan(cfg, tickers=tickers) if tickers else load_prices_for_scan(cfg)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Could not load price data:[/] {exc}")
        console.print(
            "[dim]Usually rate limiting or no network. Retry shortly; `ajax doctor` "
            "reports what your data plan provides.[/]"
        )
        raise typer.Exit(1) from exc


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging.")) -> None:
    cfg = get_config()
    configure(verbose=verbose, log_dir=cfg.paths.resolve("logs"))


# --------------------------------------------------------------------- doctor


@app.command()
def doctor() -> None:
    """Probe what your Alpaca data plan actually provides. Run this first."""
    cfg = get_config()
    console.print("[bold]Probing your Alpaca paper account…[/]\n")

    caps = caps_mod.probe(cfg)
    path = caps_mod.save(cfg, caps)

    table = Table(show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Credentials present", _yn(caps.credentials_present))
    table.add_row("Chain reachable", _yn(caps.chain_reachable))
    table.add_row("Contracts returned", str(caps.chain_contract_count))
    table.add_row("Greeks populated", _yn(caps.greeks_populated))
    table.add_row("IV populated", _yn(caps.iv_populated))
    table.add_row("Bid/ask populated", _yn(caps.quotes_populated))
    table.add_row("Option bars reachable", _yn(caps.option_bars_reachable))
    table.add_row("Earliest option bar", caps.option_bars_earliest or "unknown")
    table.add_row("Contract discovery", _yn(caps.contract_discovery_works))
    console.print(table)

    console.print(f"\n[bold]Live selection will use:[/] {caps.live_selection_path}")
    console.print(f"[bold]Backtest pricing will use:[/] {caps.backtest_price_path}")

    if caps.notes:
        console.print("\n[bold]Notes[/]")
        for note in caps.notes:
            console.print(f"  [yellow]•[/] {note}")

    console.print(f"\n[dim]Written to {path}[/]")


# ------------------------------------------------------------------- universe


@universe_app.command("show")
def universe_show() -> None:
    """Show the cached S&P 500 list."""
    from ajax.data import universe as universe_mod

    try:
        universe = universe_mod.load_cached()
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    console.print(
        f"[bold]{len(universe)}[/] tickers  "
        f"(as of [cyan]{universe.as_of}[/], source [cyan]{universe.source}[/])"
    )
    console.print(", ".join(universe.tickers[:25]) + " …")


@universe_app.command("refresh")
def universe_refresh() -> None:
    """Refresh from Wikipedia, keeping the cached list if sanity checks fail."""
    from ajax.data import universe as universe_mod

    cfg = get_config()
    try:
        universe = universe_mod.refresh(cfg.universe)
    except universe_mod.UniverseRefreshRejected as exc:
        console.print(f"[red]Refresh rejected:[/] {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Refresh failed:[/] {exc}")
        console.print("[dim]The cached list is unchanged.[/]")
        raise typer.Exit(1) from exc

    console.print(f"[green]Accepted[/] — {len(universe)} tickers as of {universe.as_of}")


# ----------------------------------------------------------------------- scan


@app.command()
def scan(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Never places orders."),
    shortlist: int = typer.Option(None, help="Override shortlist size."),
    no_chains: bool = typer.Option(False, help="Rank only; skip all chain requests."),
) -> None:
    """Rank the universe and show today's candidates with suggested contracts."""
    from ajax.agent.runner import build_chain_source
    from ajax.agent.runner import scan as run_scan

    overrides = {"options": {"shortlist_size": shortlist}} if shortlist else None
    cfg = load_config(overrides) if overrides else get_config()

    console.print("[dim]Fetching batched price history for the universe…[/]")
    prices = _prices_or_exit(cfg)

    source = None if no_chains else build_chain_source(cfg, prices)
    outcome = run_scan(cfg, prices, chain_source=source)

    if not outcome.candidates:
        console.print("[yellow]No candidates scored.[/]")
        for warning in outcome.warnings:
            console.print(f"  [yellow]•[/] {warning}")
        raise typer.Exit(0)

    table = Table(title=f"Candidates as of {outcome.as_of}")
    table.add_column("Ticker")
    table.add_column("Signal")
    table.add_column("Score", justify="right")
    table.add_column("Trend", justify="right")
    table.add_column("Close", justify="right")
    table.add_column("Note", overflow="fold")

    for candidate in outcome.candidates:
        colour = {"BUY_CALL": "green", "BUY_PUT": "red"}.get(candidate.label.value, "dim")
        table.add_row(
            candidate.ticker,
            f"[{colour}]{candidate.label.value}[/]",
            f"{candidate.composite:+.2f}",
            str(candidate.trend),
            f"{candidate.close:,.2f}" if candidate.close else "n/a",
            candidate.reason,
        )
    console.print(table)

    if outcome.selections:
        from ajax.options.selector import SelectionResult, describe_selection

        console.print("\n[bold]Selected contracts[/]")
        for candidate, contract in outcome.selections:
            console.print(
                "  "
                + describe_selection(
                    SelectionResult.choose(contract, 0), candidate.ticker, contract.right
                )
            )

    if outcome.skips:
        console.print("\n[bold]Skipped[/]")
        for candidate, reason, detail in outcome.skips:
            suffix = f" — {detail}" if detail else ""
            console.print(f"  [yellow]{candidate.ticker}[/]: {reason}{suffix}")

    for warning in outcome.warnings:
        console.print(f"\n[yellow]⚠ {warning}[/]")

    if dry_run:
        console.print("\n[dim]Dry run — no orders were placed.[/]")


@app.command()
def select(
    ticker: str = typer.Option(..., help="Underlying symbol."),
    direction: str = typer.Option("call", help="call or put."),
) -> None:
    """Show which contract would be chosen for one ticker, or why none is."""
    from ajax.agent.runner import build_chain_source
    from ajax.data import prices as price_data
    from ajax.options.greeks import Right
    from ajax.options.selector import describe_selection, select_contract
    from ajax.portfolio.sizing import effective_premium_cap

    cfg = get_config()
    right = Right.CALL if direction.lower().startswith("c") else Right.PUT

    prices = _prices_or_exit(cfg, tickers=[ticker.upper(), cfg.universe.benchmark])
    source = build_chain_source(cfg, prices)

    today = date.today()
    quotes = source.get_chain(ticker.upper(), today, right)
    cap = effective_premium_cap(cfg.account, cfg.options)
    result = select_contract(quotes, today, cfg.options, max_premium=cap)

    console.print(f"\n{describe_selection(result, ticker.upper(), right)}")
    console.print(
        f"[dim]{result.considered} contracts considered · delta band "
        f"{cfg.options.delta_min:.2f}-{cfg.options.delta_max:.2f} · DTE "
        f"{cfg.options.dte_target_min}-{cfg.options.dte_target_max} "
        f"(floor {cfg.options.dte_hard_floor}) · premium cap ${cap:.2f}/share[/]"
    )
    spot = price_data.spot_on(prices, ticker.upper(), today)
    if spot:
        console.print(f"[dim]Spot: ${spot:,.2f}[/]")


# ---------------------------------------------------------------- feasibility


@app.command()
def feasibility(
    tickers: str = typer.Option(None, help="Comma-separated tickers (default: today's shortlist)."),
) -> None:
    """Quantify how much of the universe is affordable at your account size.

    This is the command that answers whether the configured risk level and delta
    band can actually be traded together on this account.
    """
    from ajax.agent.runner import build_chain_source
    from ajax.agent.runner import scan as run_scan
    from ajax.options.selector import select_contract

    cfg = get_config()
    prices = _prices_or_exit(cfg)

    if tickers:
        names = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        rights = [(n, "call") for n in names]
    else:
        outcome = run_scan(cfg, prices)
        rights = [
            (c.ticker, c.label.right.value)
            for c in outcome.candidates
            if c.actionable and c.label.right
        ][: cfg.options.shortlist_size]

    if not rights:
        console.print("[yellow]No actionable candidates to test.[/]")
        raise typer.Exit(0)

    from ajax.options.greeks import Right

    source = build_chain_source(cfg, prices)
    today = date.today()

    risk_levels = [0.02, 0.05, 0.10, 0.15, 0.20]
    bands = [(0.30, 0.50), (0.45, 0.60), (0.55, 0.70)]

    chains: dict[tuple[str, str], list] = {}
    for ticker, direction in rights:
        right = Right.CALL if direction == "call" else Right.PUT
        try:
            chains[(ticker, direction)] = source.get_chain(ticker, today, right)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[dim]chain unavailable for {ticker}: {exc}[/]")
            chains[(ticker, direction)] = []

    table = Table(title=f"Affordable candidates out of {len(rights)} (account ${cfg.account.equity:,.0f})")
    table.add_column("Risk/trade")
    table.add_column("Budget", justify="right")
    for low, high in bands:
        table.add_column(f"Δ {low:.2f}-{high:.2f}", justify="right")

    for risk in risk_levels:
        budget = cfg.account.equity * risk
        cap = budget / 100.0
        row = [f"{risk:.0%}", f"${budget:,.0f}"]
        for low, high in bands:
            band_cfg = cfg.options.model_copy(update={"delta_min": low, "delta_max": high})
            affordable = 0
            for quotes in chains.values():
                if not quotes:
                    continue
                result = select_contract(quotes, today, band_cfg, max_premium=cap)
                if result.selected:
                    affordable += 1
            row.append(f"{affordable}")
        table.add_row(*row)

    console.print(table)
    console.print(
        "\n[dim]Contract cost = premium × 100. A higher delta means a more expensive "
        "contract, so raising the delta band and lowering the risk budget pull in "
        "opposite directions. Current config: "
        f"{cfg.account.risk_pct_per_trade:.0%} risk, "
        f"Δ {cfg.options.delta_min:.2f}-{cfg.options.delta_max:.2f}.[/]"
    )


# ------------------------------------------------------------------- backtest


@app.command()
def backtest(
    months: int = typer.Option(None, help="Months of history (default from config)."),
    tickers: str = typer.Option(None, help="Comma-separated subset; default is the full universe."),
    price_source: str = typer.Option(None, help="auto | bars | bs"),
    out: str = typer.Option(None, help="Report path stem."),
) -> None:
    """Backtest the strategy and write a report."""
    from ajax.backtest.engine import default_window, run_backtest
    from ajax.backtest.metrics import compute_metrics
    from ajax.backtest.price_source import build_price_source
    from ajax.backtest.report import render_console, write_csv, write_markdown
    from ajax.data import prices as price_data
    from ajax.data import universe as universe_mod

    overrides: dict = {"backtest": {}}
    if months:
        overrides["backtest"]["months"] = months
    if price_source:
        overrides["backtest"]["price_source"] = price_source
    cfg = load_config(overrides) if overrides["backtest"] else get_config()

    if tickers:
        names = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        names = universe_mod.load(cfg.universe).tickers
    if cfg.universe.benchmark not in names:
        names.append(cfg.universe.benchmark)

    start, end = default_window(cfg)
    fetch_start, _ = price_data.history_window(cfg, cfg.backtest.months, cfg.signals.warmup_days)

    console.print(f"[dim]Loading price history for {len(names)} tickers…[/]")
    try:
        frame = price_data.fetch_history(names, cfg, start=fetch_start, end=end)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Could not load price data:[/] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[dim]Simulating {start} → {end}…[/]")
    source = build_price_source(cfg)
    result = run_backtest(frame, cfg, source, start=start, end=end)
    metrics = compute_metrics(result.trades, cfg.account.equity)

    render_console(result, metrics, cfg)

    stem = out or f"backtest_{datetime.now():%Y%m%d_%H%M%S}"
    reports = cfg.paths.resolve("reports")
    md = write_markdown(result, metrics, cfg, reports / f"{stem}.md")
    csv = write_csv(result, cfg, reports / f"{stem}.csv")
    console.print(f"[dim]Wrote {md} and {csv}[/]")


@app.command()
def tune(
    param_grid: str = typer.Option(..., help="Path to a parameter grid YAML."),
    months: int = typer.Option(None, help="Months of history."),
    holdout_days: int = typer.Option(30, help="Days reserved for out-of-sample testing."),
    apply: bool = typer.Option(False, "--apply", help="Write config/tuned.yaml."),
) -> None:
    """Grid-search parameters, reporting in-sample and holdout results."""
    from ajax.backtest.engine import default_window
    from ajax.backtest.tuner import (
        OVERFITTING_WARNING,
        expand_grid,
        leaderboard_frame,
        load_grid,
        run_grid,
        write_tuned_config,
    )
    from ajax.data import prices as price_data
    from ajax.data import universe as universe_mod

    cfg = load_config({"backtest": {"months": months}}) if months else get_config()
    grid = load_grid(Path(param_grid))
    combos = len(expand_grid(grid))
    console.print(f"[bold]{combos}[/] grid points to evaluate")

    names = universe_mod.load(cfg.universe).tickers
    if cfg.universe.benchmark not in names:
        names.append(cfg.universe.benchmark)
    start, end = default_window(cfg)
    fetch_start, _ = price_data.history_window(cfg, cfg.backtest.months, cfg.signals.warmup_days)

    frame = price_data.fetch_history(names, cfg, start=fetch_start, end=end)

    with console.status("Running grid…"):
        points = run_grid(frame, grid, start=start, end=end, holdout_days=holdout_days)

    board = leaderboard_frame(points)
    table = Table(title="Leaderboard (ranked by holdout expectancy)")
    for column in board.columns:
        table.add_column(str(column), justify="right", overflow="fold")
    for _, row in board.head(15).iterrows():
        table.add_row(*[_fmt(v) for v in row.tolist()])
    console.print(table)

    console.print(f"\n[yellow]⚠ {OVERFITTING_WARNING}[/]")

    if apply and points:
        path = write_tuned_config(points[0], TUNED_CONFIG)
        console.print(f"\n[green]Wrote {path}[/] — delete it to revert to defaults.")


# ---------------------------------------------------------------------- agent


@agent_app.command("run-once")
def agent_run_once(
    dry_run: bool = typer.Option(False, "--dry-run", help="Select but never submit orders."),
) -> None:
    """One paper-trading cycle: close due positions, scan, order, log news."""
    from ajax.agent.runner import build_chain_source, load_prices_for_scan, run_once

    cfg = get_config()
    log = _trade_log(cfg)

    try:
        prices = load_prices_for_scan(cfg)
        source = build_chain_source(cfg, prices)
        summary = run_once(cfg, log, dry_run=dry_run, chain_source=source)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Run failed:[/] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        f"[bold]{summary.as_of}[/] — opened {summary.opened}, closed {summary.closed}, "
        f"skipped {summary.skipped}"
        + (" [dim](dry run)[/]" if summary.dry_run else "")
    )
    for note in summary.notes:
        console.print(f"  [yellow]•[/] {note}")

    _print_status(cfg, log)


@agent_app.command("serve")
def agent_serve(
    hour: int = typer.Option(9, help="Hour to run (exchange local time)."),
    minute: int = typer.Option(32, help="Minute to run."),
) -> None:
    """Run an always-on scheduler. Cron plus `run-once` is usually better."""
    from ajax.agent.scheduler import serve

    cfg = get_config()
    console.print(f"[bold]Scheduler starting[/] — weekdays {hour:02d}:{minute:02d} America/New_York")
    console.print("[dim]Ctrl-C to stop.[/]")
    serve(cfg, hour=hour, minute=minute)


@app.command()
def status() -> None:
    """Open positions, recent runs, and progress toward graduation."""
    cfg = get_config()
    _print_status(cfg, _trade_log(cfg))


@app.command("graduate-check")
def graduate_check() -> None:
    """Has the paper track record met the bar for considering live trading?"""
    from ajax.agent.graduation import evaluate

    cfg = get_config()
    log = _trade_log(cfg)
    result = evaluate(log.closed_trades(), cfg.graduation)

    colour = "green" if result.passed else "yellow"
    console.print(f"\n[bold {colour}]{result.headline()}[/]\n")

    for window in result.windows:
        console.print(f"  {window.describe()}")

    if result.reasons:
        console.print("\n[bold]Not yet, because:[/]")
        for reason in result.reasons:
            console.print(f"  [yellow]•[/] {reason}")

    console.print(
        f"\n[dim]Criterion: ≥{cfg.graduation.min_win_rate:.0%} win rate across "
        f"{cfg.graduation.consecutive_weeks} consecutive weeks AND at least "
        f"{cfg.graduation.min_closed_trades} closed trades.[/]"
    )
    console.print(
        "[dim]Passing this check unlocks nothing on its own — it is a report. "
        "Going live is a separate manual decision.[/]"
    )


@app.command("enable-live")
def enable_live(
    i_understand_the_risk: bool = typer.Option(
        False, "--i-understand-the-risk", help="Required. Acknowledges real-money risk."
    ),
) -> None:
    """Manual gate for live trading. Never invoked by automation."""
    from ajax.agent.graduation import evaluate
    from ajax.live.gate import NEXT_STEPS, evaluate_gate, record_acknowledgement, risk_briefing

    cfg = get_config()
    log = _trade_log(cfg)
    result = evaluate(log.closed_trades(), cfg.graduation)

    console.print("\n[bold red]LIVE TRADING GATE[/]\n")
    console.print(risk_briefing(cfg))
    console.print(f"\nPaper record: [bold]{result.headline()}[/]\n")

    phrase = ""
    if i_understand_the_risk and result.passed:
        phrase = typer.prompt('Type exactly: I ACCEPT REAL MONEY RISK')

    decision = evaluate_gate(result, risk_flag=i_understand_the_risk, phrase=phrase)

    if not decision.allowed:
        console.print("[bold yellow]Not enabled.[/]")
        for reason in decision.reasons:
            console.print(f"  [yellow]•[/] {reason}")
        raise typer.Exit(1)

    path = record_acknowledgement(decision)
    console.print(f"\n[dim]Acknowledgement recorded in {path}[/]")
    console.print(NEXT_STEPS)


# ------------------------------------------------------------------- helpers


def _print_status(cfg, log) -> None:  # noqa: ANN001
    from ajax.agent.graduation import evaluate

    open_trades = log.open_trades()
    console.print(
        f"\n[bold]Open positions[/] "
        f"({len(open_trades)}/{cfg.account.max_concurrent_positions} slots)"
    )
    if open_trades:
        table = Table(box=None)
        for column in ("Ticker", "Contract", "Entry", "Qty", "Premium", "Planned exit"):
            table.add_column(column)
        for trade in open_trades:
            table.add_row(
                trade["ticker"],
                trade["contract_symbol"],
                str(trade["entry_date"]),
                str(trade["qty"]),
                f"${(trade['entry_premium'] or 0):.2f}",
                str(trade["planned_exit_date"] or "—"),
            )
        console.print(table)
    else:
        console.print("  [dim]none[/]")

    result = evaluate(log.closed_trades(), cfg.graduation)
    colour = "green" if result.passed else "yellow"
    console.print(f"\n[bold]Track record[/]\n  [{colour}]{result.headline()}[/]")
    if not result.sample_sufficient:
        console.print(
            f"  [dim]{result.trades_remaining} more closed trades needed before the win "
            f"rate becomes meaningful.[/]"
        )

    counts = log.skip_reason_counts()
    if counts:
        console.print("\n[bold]Skip reasons to date[/]")
        for reason, count in counts.items():
            console.print(f"  {reason}: {count}")


def _yn(value: bool) -> str:
    return "[green]yes[/]" if value else "[red]no[/]"


def _fmt(value) -> str:  # noqa: ANN001
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


if __name__ == "__main__":  # pragma: no cover
    app()
