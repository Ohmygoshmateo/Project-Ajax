"""Contract selection: the filter cascade that turns a chain into one contract.

The central design decision lives here. "Cheapest contract" and "best swing
delta" are the same dial turned opposite ways — delta is moneyness is price — so
they cannot both be optimized. The resolution is strictly ordered:

1. filter to the delta band,
2. take the cheapest *survivor* of that band.

The band is never widened to fit the budget. If nothing inside the band is
affordable the signal is **skipped** with a structured reason. Falling back to a
0.20-delta lottery ticket because the 0.60 was too expensive is a materially
different strategy, and is prohibited by construction rather than by convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from ajax.config import OptionsConfig
from ajax.options.chain_source import ContractQuote
from ajax.options.greeks import Right


class SkipReason(str, Enum):
    NO_CONTRACTS = "no_contracts"
    NO_CONTRACT_IN_DTE_WINDOW = "no_contract_in_dte_window"
    ILLIQUID = "illiquid"
    MISSING_DELTA = "missing_delta"
    NO_CONTRACT_IN_DELTA_BAND = "no_contract_in_delta_band"
    UNAFFORDABLE = "unaffordable"

    @property
    def explanation(self) -> str:
        return {
            SkipReason.NO_CONTRACTS: "provider returned no contracts for this underlying",
            SkipReason.NO_CONTRACT_IN_DTE_WINDOW: (
                "no expiration in the target window, and none at or above the hard DTE floor"
            ),
            SkipReason.ILLIQUID: "every candidate failed open-interest, bid, or spread checks",
            SkipReason.MISSING_DELTA: "no candidate carried a usable delta",
            SkipReason.NO_CONTRACT_IN_DELTA_BAND: "no liquid contract fell inside the delta band",
            SkipReason.UNAFFORDABLE: (
                "contracts exist in the delta band but all exceed the premium cap; "
                "the band is deliberately not widened to fit the budget"
            ),
        }[self]


@dataclass(frozen=True)
class SelectionResult:
    """Either a chosen contract or a reason none was chosen — never both."""

    contract: ContractQuote | None
    skip_reason: SkipReason | None
    considered: int
    detail: str = ""

    @property
    def selected(self) -> bool:
        return self.contract is not None

    @classmethod
    def skip(cls, reason: SkipReason, considered: int, detail: str = "") -> SelectionResult:
        return cls(contract=None, skip_reason=reason, considered=considered, detail=detail)

    @classmethod
    def choose(cls, contract: ContractQuote, considered: int) -> SelectionResult:
        return cls(contract=contract, skip_reason=None, considered=considered)


def _passes_liquidity(quote: ContractQuote, cfg: OptionsConfig) -> bool:
    if quote.mid is None or quote.mid <= 0:
        return False
    if quote.bid is None or quote.bid <= 0:
        return False
    if quote.open_interest is not None and quote.open_interest < cfg.min_open_interest:
        return False
    if cfg.min_volume and (quote.volume or 0) < cfg.min_volume:
        return False
    spread = quote.relative_spread
    if spread is not None and spread > cfg.max_relative_spread:
        return False
    return True


def filter_by_dte(
    quotes: list[ContractQuote], as_of: date, cfg: OptionsConfig
) -> list[ContractQuote]:
    """Contracts inside the target DTE window.

    If the target window is empty the window widens *downward* toward the hard
    floor and *upward* without limit — but never below ``dte_hard_floor``. Inside
    the last couple of weeks gamma and theta go nonlinear, which is precisely the
    regime a 5-day directional hold should not be exposed to.
    """
    in_window = [q for q in quotes if cfg.dte_target_min <= q.dte(as_of) <= cfg.dte_target_max]
    if in_window:
        return in_window
    return [q for q in quotes if q.dte(as_of) >= cfg.dte_hard_floor]


def select_contract(
    quotes: list[ContractQuote],
    as_of: date,
    cfg: OptionsConfig,
    *,
    max_premium: float | None = None,
) -> SelectionResult:
    """Run the full cascade and return the chosen contract or a skip reason.

    ``max_premium`` overrides ``cfg.max_premium_per_contract`` so the caller can
    pass a budget derived from live account equity rather than the configured
    default.
    """
    considered = len(quotes)
    if not quotes:
        return SelectionResult.skip(SkipReason.NO_CONTRACTS, considered)

    premium_cap = cfg.max_premium_per_contract if max_premium is None else max_premium

    # 1. Expiration window.
    candidates = filter_by_dte(quotes, as_of, cfg)
    if not candidates:
        return SelectionResult.skip(
            SkipReason.NO_CONTRACT_IN_DTE_WINDOW,
            considered,
            f"floor is {cfg.dte_hard_floor} DTE",
        )

    # 2. Liquidity.
    liquid = [q for q in candidates if _passes_liquidity(q, cfg)]
    if not liquid:
        return SelectionResult.skip(
            SkipReason.ILLIQUID,
            considered,
            f"needed OI>={cfg.min_open_interest}, bid>0, spread<={cfg.max_relative_spread:.0%}",
        )

    # 3. Delta band. Compared on magnitude so puts and calls are symmetric.
    with_delta = [q for q in liquid if q.abs_delta is not None]
    if not with_delta:
        return SelectionResult.skip(SkipReason.MISSING_DELTA, considered)

    in_band = [q for q in with_delta if cfg.delta_min <= q.abs_delta <= cfg.delta_max]
    if not in_band:
        closest = min(with_delta, key=lambda q: abs(q.abs_delta - _band_mid(cfg)))
        return SelectionResult.skip(
            SkipReason.NO_CONTRACT_IN_DELTA_BAND,
            considered,
            f"band {cfg.delta_min:.2f}-{cfg.delta_max:.2f}; closest available "
            f"delta was {closest.abs_delta:.2f}",
        )

    # 4. Affordability. A hard veto — the band above is never relaxed to satisfy it.
    affordable = [q for q in in_band if q.mid is not None and q.mid <= premium_cap]
    if not affordable:
        cheapest = min(in_band, key=lambda q: q.mid)
        return SelectionResult.skip(
            SkipReason.UNAFFORDABLE,
            considered,
            f"cheapest in-band contract was ${cheapest.mid:.2f}/share "
            f"(${cheapest.mid * 100:.0f}), cap is ${premium_cap:.2f}/share "
            f"(${premium_cap * 100:.0f})",
        )

    # 5. Cheapest survivor, deterministic tie-breaks.
    target_dte = (cfg.dte_target_min + cfg.dte_target_max) / 2.0
    band_mid = _band_mid(cfg)
    best = min(
        affordable,
        key=lambda q: (
            round(q.mid, 4),
            abs(q.abs_delta - band_mid),
            abs(q.dte(as_of) - target_dte),
            q.symbol,
        ),
    )
    return SelectionResult.choose(best, considered)


def _band_mid(cfg: OptionsConfig) -> float:
    return (cfg.delta_min + cfg.delta_max) / 2.0


def describe_selection(result: SelectionResult, underlying: str, right: Right) -> str:
    """One-line human summary for scan output and logs."""
    if result.selected:
        c = result.contract
        return (
            f"{underlying} {right.value.upper()} {c.strike:g} exp {c.expiry} "
            f"delta {c.abs_delta:.2f} mid ${c.mid:.2f} (${c.mid * 100:.0f}) "
            f"[{c.greeks_source}]"
        )
    detail = f" — {result.detail}" if result.detail else ""
    return f"{underlying} {right.value.upper()} SKIPPED: {result.skip_reason.value}{detail}"
