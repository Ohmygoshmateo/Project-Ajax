"""SQLite persistence for trades, skipped signals, news, and run history.

Structured and queryable rather than console-only, because the graduation check
needs an auditable trade history and because a future dashboard is a known
consumer of this data.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT NOT NULL,
    direction         TEXT NOT NULL,          -- call | put
    contract_symbol   TEXT NOT NULL,
    strike            REAL,
    expiry            TEXT,
    dte_at_entry      INTEGER,
    entry_date        TEXT NOT NULL,
    entry_underlying  REAL,
    entry_premium     REAL,
    delta_at_entry    REAL,
    iv_at_entry       REAL,
    greeks_source     TEXT,
    qty               INTEGER NOT NULL DEFAULT 1,
    risk_dollars      REAL,
    commission        REAL DEFAULT 0,
    order_id_entry    TEXT,
    planned_exit_date TEXT,
    exit_date         TEXT,
    exit_premium      REAL,
    order_id_exit     TEXT,
    pnl               REAL,
    pnl_pct           REAL,
    status            TEXT NOT NULL,          -- open | closed
    mode              TEXT NOT NULL DEFAULT 'paper',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skipped_signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER,
    as_of        TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    direction    TEXT,
    skip_reason  TEXT NOT NULL,
    detail       TEXT,
    composite    REAL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    INTEGER,
    ticker      TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    title       TEXT,
    url         TEXT,
    publisher   TEXT,
    published_at TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    as_of         TEXT,
    mode          TEXT,
    opened        INTEGER DEFAULT 0,
    closed        INTEGER DEFAULT 0,
    skipped       INTEGER DEFAULT 0,
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_exit_date ON trades(exit_date);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
"""


@dataclass
class TradeRecord:
    ticker: str
    direction: str
    contract_symbol: str
    entry_date: str
    status: str = "open"
    strike: float | None = None
    expiry: str | None = None
    dte_at_entry: int | None = None
    entry_underlying: float | None = None
    entry_premium: float | None = None
    delta_at_entry: float | None = None
    iv_at_entry: float | None = None
    greeks_source: str | None = None
    qty: int = 1
    risk_dollars: float | None = None
    commission: float = 0.0
    order_id_entry: str | None = None
    planned_exit_date: str | None = None
    exit_date: str | None = None
    exit_premium: float | None = None
    order_id_exit: str | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    mode: str = "paper"


class TradeLog:
    def __init__(self, db_path: Path | str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- writes

    def record_entry(self, trade: TradeRecord) -> int:
        payload = asdict(trade)
        payload["created_at"] = datetime.now().isoformat(timespec="seconds")
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{k}" for k in payload)
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO trades ({columns}) VALUES ({placeholders})", payload
            )
            return int(cursor.lastrowid)

    def record_exit(
        self,
        trade_id: int,
        *,
        exit_date: str,
        exit_premium: float,
        order_id_exit: str | None = None,
        commission: float = 0.0,
    ) -> None:
        """Close a trade and compute realized P&L net of both commissions."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
            if row is None:
                raise KeyError(f"no trade with id {trade_id}")

            qty = int(row["qty"] or 1)
            entry = float(row["entry_premium"] or 0.0)
            entry_commission = float(row["commission"] or 0.0)
            gross = (float(exit_premium) - entry) * 100.0 * qty
            pnl = gross - entry_commission - commission
            cost_basis = entry * 100.0 * qty
            pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else None

            conn.execute(
                """
                UPDATE trades
                   SET exit_date = ?, exit_premium = ?, order_id_exit = ?,
                       commission = ?, pnl = ?, pnl_pct = ?, status = 'closed'
                 WHERE id = ?
                """,
                (
                    exit_date,
                    float(exit_premium),
                    order_id_exit,
                    entry_commission + commission,
                    pnl,
                    pnl_pct,
                    trade_id,
                ),
            )

    def record_skip(
        self,
        *,
        as_of: str,
        ticker: str,
        direction: str | None,
        skip_reason: str,
        detail: str = "",
        composite: float | None = None,
        run_id: int | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO skipped_signals
                    (run_id, as_of, ticker, direction, skip_reason, detail, composite, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    as_of,
                    ticker,
                    direction,
                    skip_reason,
                    detail,
                    composite,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def record_news(self, ticker: str, as_of: str, items: list[Any], trade_id: int | None = None):
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            for item in items:
                conn.execute(
                    """
                    INSERT INTO news_snapshots
                        (trade_id, ticker, as_of, title, url, publisher, published_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade_id,
                        ticker,
                        as_of,
                        getattr(item, "title", None),
                        getattr(item, "url", None),
                        getattr(item, "publisher", None),
                        getattr(item, "published_at", None),
                        now,
                    ),
                )

    def start_run(self, as_of: str, mode: str = "paper") -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (started_at, as_of, mode) VALUES (?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), as_of, mode),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self, run_id: int, *, opened: int, closed: int, skipped: int, notes: str = ""
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs SET finished_at = ?, opened = ?, closed = ?, skipped = ?, notes = ?
                 WHERE id = ?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    opened,
                    closed,
                    skipped,
                    notes,
                    run_id,
                ),
            )

    # ----------------------------------------------------------------- reads

    def open_trades(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'open' ORDER BY entry_date"
            ).fetchall()
        return [dict(r) for r in rows]

    def closed_trades(self, since: date | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM trades WHERE status = 'closed'"
        params: tuple = ()
        if since is not None:
            query += " AND exit_date >= ?"
            params = (since.isoformat(),)
        query += " ORDER BY exit_date"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def open_underlyings(self) -> set[str]:
        return {t["ticker"] for t in self.open_trades()}

    def skip_reason_counts(self, since: date | None = None) -> dict[str, int]:
        query = "SELECT skip_reason, COUNT(*) AS n FROM skipped_signals"
        params: tuple = ()
        if since is not None:
            query += " WHERE as_of >= ?"
            params = (since.isoformat(),)
        query += " GROUP BY skip_reason ORDER BY n DESC"
        with self._connect() as conn:
            return {r["skip_reason"]: int(r["n"]) for r in conn.execute(query, params).fetchall()}

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def to_json(self) -> str:
        return json.dumps(
            {"open": self.open_trades(), "closed": self.closed_trades()},
            indent=2,
            default=str,
        )
