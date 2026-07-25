"""Capability probe — what does this account's data plan actually provide?

Two facts determine real architecture and neither is documented reliably:

1. whether Alpaca's chain returns populated greeks on the free indicative feed,
2. how far back historical option bars actually go.

Rather than assuming either way, this probes the user's real account once and
writes the answers to ``data_cache/capabilities.json``. Downstream modules read
that file to pick a path. Running it is the first step of setup.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from ajax.config import Config

log = logging.getLogger(__name__)

PROBE_UNDERLYING = "SPY"


@dataclass
class Capabilities:
    probed_at: str | None = None
    credentials_present: bool = False
    chain_reachable: bool = False
    chain_contract_count: int = 0
    greeks_populated: bool = False
    iv_populated: bool = False
    quotes_populated: bool = False
    option_bars_reachable: bool = False
    option_bars_earliest: str | None = None
    contract_discovery_works: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def live_selection_path(self) -> str:
        if self.greeks_populated:
            return "alpaca (native greeks)"
        if self.iv_populated:
            return "black-scholes from alpaca IV"
        return "black-scholes from realized vol (weakest provenance)"

    @property
    def backtest_price_path(self) -> str:
        return "alpaca bars where available, black-scholes fallback" if self.option_bars_reachable \
            else "black-scholes only"


def capabilities_path(cfg: Config) -> Path:
    return cfg.paths.resolve("data_cache") / "capabilities.json"


def load(cfg: Config) -> Capabilities:
    path = capabilities_path(cfg)
    if not path.exists():
        return Capabilities()
    try:
        with open(path) as fh:
            return Capabilities(**json.load(fh))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read capabilities file: %s", exc)
        return Capabilities()


def save(cfg: Config, caps: Capabilities) -> Path:
    path = capabilities_path(cfg)
    path.write_text(json.dumps(asdict(caps), indent=2) + "\n")
    return path


def probe(cfg: Config) -> Capabilities:
    """Run the probe against the live paper account."""
    caps = Capabilities(probed_at=datetime.now().isoformat(timespec="seconds"))

    from ajax.config import load_paper_credentials

    creds = load_paper_credentials()
    caps.credentials_present = creds.has_paper
    if not creds.has_paper:
        caps.notes.append(
            "No paper credentials found. Copy .env.example to .env and add your Alpaca "
            "paper keys, then re-run `ajax doctor`."
        )
        return caps

    _probe_chain(cfg, caps)
    _probe_bars(cfg, caps)
    _probe_discovery(cfg, caps)
    return caps


def _probe_chain(cfg: Config, caps: Capabilities) -> None:
    from ajax.data.alpaca_options import AlpacaDataUnavailable, fetch_chain
    from ajax.options.greeks import Right

    try:
        quotes = fetch_chain(PROBE_UNDERLYING, cfg, Right.CALL)
    except AlpacaDataUnavailable as exc:
        caps.notes.append(f"Chain request failed: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        caps.notes.append(f"Chain request raised {type(exc).__name__}: {exc}")
        return

    caps.chain_reachable = True
    caps.chain_contract_count = len(quotes)
    caps.greeks_populated = any(q.delta is not None for q in quotes)
    caps.iv_populated = any(q.iv is not None for q in quotes)
    caps.quotes_populated = any(q.bid is not None and q.ask is not None for q in quotes)

    if not caps.greeks_populated:
        caps.notes.append(
            "Chain returned no greeks — this is the free indicative feed behaving as suspected. "
            "Deltas will be modelled from implied volatility (or realized vol) and stamped as "
            "such. Live selection still works; provenance is just weaker."
        )
    if not caps.quotes_populated:
        caps.notes.append(
            "Chain returned no bid/ask. Liquidity filtering and mid pricing will not work "
            "properly — check whether the market is open and whether your data plan includes "
            "option quotes."
        )


def _probe_bars(cfg: Config, caps: Capabilities) -> None:
    """Binary-search backwards for the earliest available option bar."""
    from ajax.broker.contracts import build_occ_symbol, third_friday
    from ajax.data.alpaca_options import fetch_option_bars
    from ajax.options.greeks import Right

    today = date.today()

    # A near-the-money SPY call a couple of months out is about as liquid as
    # options get, so absence of data means absence of history, not illiquidity.
    probe_expiry = third_friday(
        today.year + (1 if today.month + 2 > 12 else 0), ((today.month + 1) % 12) + 1
    )
    try:
        symbol = build_occ_symbol(PROBE_UNDERLYING, probe_expiry, Right.CALL, 500.0)
    except ValueError:
        caps.notes.append("Could not construct a probe contract symbol.")
        return

    recent = fetch_option_bars(
        [symbol], cfg, today - timedelta(days=30), today, use_cache=False
    )
    caps.option_bars_reachable = bool(recent)

    if not recent:
        caps.notes.append(
            "No option bars returned for the probe contract. Either the strike is far from "
            "the money, or historical option bars are not included in this data plan. The "
            "backtest will fall back to Black-Scholes reconstruction, which is optimistic — "
            "see docs/LIMITATIONS.md."
        )
        return

    # Step back by months until data disappears.
    earliest = today
    for months_back in range(1, 37):
        window_end = today - timedelta(days=30 * months_back)
        window_start = window_end - timedelta(days=30)
        found = fetch_option_bars([symbol], cfg, window_start, window_end, use_cache=False)
        if not found:
            break
        earliest = window_start

    caps.option_bars_earliest = earliest.isoformat()
    caps.notes.append(
        f"Option bars reach back to at least {earliest.isoformat()} for the probe contract. "
        f"Coverage varies by contract — the backtest reports per-run coverage."
    )


def _probe_discovery(cfg: Config, caps: Capabilities) -> None:
    from ajax.data.alpaca_options import list_contracts
    from ajax.options.greeks import Right

    today = date.today()
    try:
        symbols = list_contracts(
            PROBE_UNDERLYING, cfg, today, today + timedelta(days=90), Right.CALL
        )
    except Exception as exc:  # noqa: BLE001
        caps.notes.append(f"Contract discovery raised {type(exc).__name__}: {exc}")
        return

    caps.contract_discovery_works = bool(symbols)
    if not symbols:
        caps.notes.append(
            "Contract discovery returned nothing; the backtest will enumerate standard "
            "monthly expirations synthetically instead."
        )
