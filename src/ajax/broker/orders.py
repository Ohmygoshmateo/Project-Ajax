"""Single-leg option order submission and position reconciliation.

Only long calls and long puts are submitted — Alpaca options level 2, which paper
accounts hold by default. Multi-leg spreads are deliberately out of scope.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from ajax.broker.contracts import parse_occ_symbol
from ajax.options.greeks import Right

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    order_id: str | None
    symbol: str
    qty: int
    submitted_price: float | None
    error: str | None = None


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    underlying: str
    right: Right | None
    strike: float | None
    expiry: date | None
    qty: int
    avg_entry_price: float
    market_value: float | None
    unrealized_pl: float | None


def submit_buy_to_open(
    client,  # noqa: ANN001
    occ_symbol: str,
    qty: int,
    *,
    limit_price: float | None = None,
) -> OrderResult:
    """Buy to open. Uses a limit order when a price is supplied.

    A limit is strongly preferred: option spreads run several percent, and a
    market order into a wide book gives up more than the edge the signal claims.
    """
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

    if qty < 1:
        return OrderResult(False, None, occ_symbol, qty, None, "quantity must be >= 1")

    try:
        if limit_price is not None and limit_price > 0:
            request = LimitOrderRequest(
                symbol=occ_symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=round(float(limit_price), 2),
            )
        else:
            request = MarketOrderRequest(
                symbol=occ_symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
        order = client.submit_order(request)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a result
        log.error("buy order failed for %s: %s", occ_symbol, exc)
        return OrderResult(False, None, occ_symbol, qty, limit_price, str(exc))

    return OrderResult(True, str(getattr(order, "id", "")), occ_symbol, qty, limit_price)


def submit_sell_to_close(
    client,  # noqa: ANN001
    occ_symbol: str,
    qty: int,
    *,
    limit_price: float | None = None,
) -> OrderResult:
    """Sell to close an existing long option position."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

    if qty < 1:
        return OrderResult(False, None, occ_symbol, qty, None, "quantity must be >= 1")

    try:
        if limit_price is not None and limit_price > 0:
            request = LimitOrderRequest(
                symbol=occ_symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                limit_price=round(float(limit_price), 2),
            )
        else:
            request = MarketOrderRequest(
                symbol=occ_symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
        order = client.submit_order(request)
    except Exception as exc:  # noqa: BLE001
        log.error("sell order failed for %s: %s", occ_symbol, exc)
        return OrderResult(False, None, occ_symbol, qty, limit_price, str(exc))

    return OrderResult(True, str(getattr(order, "id", "")), occ_symbol, qty, limit_price)


def list_option_positions(client) -> list[BrokerPosition]:  # noqa: ANN001
    """Open option positions, parsed into the internal model."""
    try:
        raw = client.get_all_positions() or []
    except Exception as exc:  # noqa: BLE001
        log.error("could not fetch positions: %s", exc)
        return []

    out: list[BrokerPosition] = []
    for position in raw:
        symbol = str(getattr(position, "symbol", ""))
        parsed = parse_occ_symbol(symbol)
        if parsed is None:
            continue  # an equity position, not an option
        out.append(
            BrokerPosition(
                symbol=symbol,
                underlying=parsed.underlying,
                right=parsed.right,
                strike=parsed.strike,
                expiry=parsed.expiry,
                qty=int(float(getattr(position, "qty", 0) or 0)),
                avg_entry_price=float(getattr(position, "avg_entry_price", 0.0) or 0.0),
                market_value=_opt_float(getattr(position, "market_value", None)),
                unrealized_pl=_opt_float(getattr(position, "unrealized_pl", None)),
            )
        )
    return out


def _opt_float(value) -> float | None:  # noqa: ANN001
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
