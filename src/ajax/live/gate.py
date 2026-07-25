"""The manual gate between paper and live trading.

**This module is never imported by the agent or the scheduler.** That is the
whole point: automation cannot reach a live endpoint by flipping a config value,
because the code path from the scheduler to a live client does not exist. Import
``ajax.agent.runner`` and follow it — every route ends at
``get_paper_trading_client``.

Passing the graduation check does not unlock anything. It is a report. Enabling
live trading requires a human to run a command, pass an explicit risk flag, type
a confirmation phrase, and supply separate live credentials.

Real-money order execution is **intentionally not implemented in v1**. The gate
records the acknowledgement and prints what would come next. Building execution
is a materially larger scope and should be an explicit, separate decision made
after the bar has actually been cleared — not a capability lying around waiting
for a config typo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from ajax.agent.graduation import GraduationStatus
from ajax.config import LOCAL_OVERRIDE, Config

log = logging.getLogger(__name__)

CONFIRMATION_PHRASE = "I ACCEPT REAL MONEY RISK"

RISK_BRIEFING = """\
Before enabling live trading, understand what you are turning on:

  • Every position risks 100% of its premium. A long option that expires out of
    the money is a total loss on that position, not a drawdown you ride out.
  • At the configured {risk_pct:.0%} risk per trade with up to {slots} concurrent
    positions, {max_exposure:.0%} of the account can be at risk simultaneously.
  • On a ${equity:,.0f} account that is up to ${max_dollars:,.0f} exposed at once.
  • Paper fills are optimistic. Real fills cross a real spread, and a paper track
    record systematically overstates what live trading will return.
  • A win rate measured over a few dozen trades is a small sample. Meeting the
    threshold is evidence, not proof.

This tool does not give financial advice and its backtest is not a prediction.
"""


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reasons: list[str]
    acknowledged_at: str | None = None


def evaluate_gate(status: GraduationStatus, *, risk_flag: bool, phrase: str) -> GateDecision:
    """Check every condition. All must hold — there is no override."""
    reasons: list[str] = []

    if not risk_flag:
        reasons.append("the --i-understand-the-risk flag was not supplied")

    if phrase.strip().upper() != CONFIRMATION_PHRASE:
        reasons.append("the confirmation phrase was not typed correctly")

    if not status.sample_sufficient:
        reasons.append(
            f"only {status.total_closed} closed paper trades, "
            f"{status.trades_remaining} short of the {status.min_closed_required} minimum"
        )

    if not status.passed:
        reasons.extend(f"graduation check: {r}" for r in status.reasons)

    return GateDecision(allowed=not reasons, reasons=reasons)


def risk_briefing(cfg: Config) -> str:
    account = cfg.account
    max_exposure = account.risk_pct_per_trade * account.max_concurrent_positions
    return RISK_BRIEFING.format(
        risk_pct=account.risk_pct_per_trade,
        slots=account.max_concurrent_positions,
        max_exposure=max_exposure,
        equity=account.equity,
        max_dollars=account.equity * max_exposure,
    )


def record_acknowledgement(decision: GateDecision, path: Path | None = None) -> Path:
    """Persist that the gate was cleared. Grants no capability by itself."""
    path = path or LOCAL_OVERRIDE
    existing: dict = {}
    if path.exists():
        with open(path) as fh:
            existing = yaml.safe_load(fh) or {}

    existing["live_trading_acknowledgement"] = {
        "acknowledged_at": datetime.now().isoformat(timespec="seconds"),
        "note": (
            "Recorded by `ajax enable-live`. This is an acknowledgement only. Real-money "
            "order execution is not implemented in v1 — see src/ajax/live/gate.py."
        ),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(existing, sort_keys=False))
    return path


NEXT_STEPS = """\
Acknowledgement recorded.

Real-money execution is deliberately NOT implemented in this version. Nothing
about your setup has changed and the scheduled agent still trades on paper only.

To actually go live, the following would each need to be done deliberately:

  1. Open and fund a live Alpaca account, and apply for options Level 2
     (long calls and puts). Paper accounts get Level 3 automatically; live
     accounts do not — the application is a separate, human step.
  2. Add ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY to .env.
  3. Implement live order routing, which is intentionally absent. It is a
     separate piece of work with its own review, not a flag.

Consider first: run paper for another few weeks and see whether the win rate
holds. Strategies that clear a threshold once frequently fail to clear it twice.
"""
