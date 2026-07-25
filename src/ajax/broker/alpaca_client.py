"""Alpaca trading clients.

**Safety-critical module.** This file exposes exactly one client factory to the
rest of the application: :func:`get_paper_trading_client`. The scheduled agent
imports that name directly and never branches on a trading-mode flag, so the
automation is *structurally* incapable of routing an order to a live endpoint —
not merely configured not to.

A live client factory exists, but it is private, requires an explicit
acknowledgement token, and is called from exactly one place
(``ajax.live.gate``). Holding paper credentials can never place a real trade.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from ajax.config import REPO_ROOT, load_paper_credentials

log = logging.getLogger(__name__)

_LIVE_ACK_TOKEN = "I_HAVE_REVIEWED_THE_PAPER_TRACK_RECORD_AND_ACCEPT_REAL_MONEY_RISK"


class CredentialsMissing(RuntimeError):
    """Required API credentials were not found in the environment."""


class LiveTradingRefused(RuntimeError):
    """A live client was requested without the deliberate manual acknowledgement."""


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    buying_power: float
    options_level: int | None
    is_paper: bool


def get_paper_trading_client():  # noqa: ANN201 - vendor type
    """The one client the scanner, agent, and scheduler are allowed to use."""
    from alpaca.trading.client import TradingClient

    creds = load_paper_credentials()
    if not creds.has_paper:
        raise CredentialsMissing(
            "ALPACA_PAPER_API_KEY / ALPACA_PAPER_SECRET_KEY are not set. "
            "Copy .env.example to .env and fill in your paper keys "
            "(free at https://alpaca.markets)."
        )
    return TradingClient(creds.paper_key, creds.paper_secret, paper=True)


def _get_live_trading_client(acknowledgement: str):  # noqa: ANN201
    """Private. Real money. Only ``ajax.live.gate`` may call this.

    Deliberately takes an acknowledgement token rather than reading a config
    flag, so no amount of YAML editing can reach a live endpoint.
    """
    if acknowledgement != _LIVE_ACK_TOKEN:
        raise LiveTradingRefused(
            "live trading client requires the explicit acknowledgement token; "
            "run `ajax enable-live` instead of calling this directly"
        )

    from alpaca.trading.client import TradingClient
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
    key = os.getenv("ALPACA_LIVE_API_KEY")
    secret = os.getenv("ALPACA_LIVE_SECRET_KEY")
    if not (key and secret):
        raise CredentialsMissing("ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY are not set")

    log.warning("constructing a LIVE trading client — real money is at risk")
    return TradingClient(key, secret, paper=False)


def account_snapshot(client=None) -> AccountSnapshot:  # noqa: ANN001
    """Equity, buying power, and options level for the paper account."""
    client = client or get_paper_trading_client()
    account = client.get_account()

    level = getattr(account, "options_trading_level", None)
    try:
        level = int(level) if level is not None else None
    except (TypeError, ValueError):
        level = None

    return AccountSnapshot(
        equity=float(getattr(account, "equity", 0.0) or 0.0),
        buying_power=float(getattr(account, "buying_power", 0.0) or 0.0),
        options_level=level,
        is_paper=True,
    )
