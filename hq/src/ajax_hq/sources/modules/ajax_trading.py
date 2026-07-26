"""Optional project module: the Ajax trading system.

The first pluggable project module. HQ is a general workspace dashboard, so this
reads the trading system's state only if it is present — a workspace without it
loses one division and nothing else.

Deliberately reads the SQLite file directly rather than importing ``ajax``: HQ
must work when the trading package is not installed, and a read-only query over a
stable schema is a smaller dependency than the package itself.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

RELATIVE_DB = Path("data_cache") / "ajax_trades.db"
RELATIVE_REPORTS = Path("reports")


def database_path(repo: Path) -> Path:
    return repo / RELATIVE_DB


def available(repo: Path) -> bool:
    return database_path(repo).is_file()


def _query(db: Path, sql: str) -> list[dict]:
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql).fetchall()]
    except sqlite3.Error:
        # A schema that has moved on is not an error worth crashing the page for.
        return []
    finally:
        connection.close()


def summarize(repo: Path) -> dict[str, str]:
    """Headline figures for the Asset Management division.

    Win rate is never returned without its trade count — the same discipline the
    trading system's own CLI enforces, restated here because this is a second
    surface showing the same number.
    """
    db = database_path(repo)
    if not db.is_file():
        return {}

    out: dict[str, str] = {}

    open_rows = _query(db, "SELECT COUNT(*) AS n FROM trades WHERE status = 'open'")
    closed = _query(
        db,
        "SELECT COUNT(*) AS n, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins, "
        "SUM(pnl) AS total FROM trades WHERE status = 'closed'",
    )
    runs = _query(db, "SELECT COUNT(*) AS n, MAX(started_at) AS last FROM runs")
    skips = _query(
        db,
        "SELECT skip_reason, COUNT(*) AS n FROM skipped_signals "
        "GROUP BY skip_reason ORDER BY n DESC LIMIT 1",
    )

    out["Open positions"] = str(open_rows[0]["n"]) if open_rows else "0"

    closed_n = int(closed[0]["n"] or 0) if closed else 0
    if closed_n == 0:
        out["Track record"] = "no closed trades"
    else:
        wins = int(closed[0]["wins"] or 0)
        # Always paired with the sample size.
        out["Win rate"] = f"{wins / closed_n:.0%} ({wins}/{closed_n})"
        total = closed[0]["total"]
        if total is not None:
            out["Realized P&L"] = f"${float(total):,.2f}"
        if closed_n < 20:
            out["Sample"] = f"{closed_n}/20 — insufficient"

    if runs and runs[0]["n"]:
        out["Agent runs"] = str(runs[0]["n"])
    if skips:
        out["Top skip reason"] = f"{skips[0]['skip_reason']} ({skips[0]['n']})"

    return out


def last_activity(repo: Path) -> datetime | None:
    db = database_path(repo)
    if not db.is_file():
        return None
    rows = _query(db, "SELECT MAX(started_at) AS last FROM runs")
    if not rows or not rows[0]["last"]:
        return None
    try:
        return datetime.fromisoformat(str(rows[0]["last"]))
    except ValueError:
        return None


def backtest_reports(repo: Path) -> list[dict[str, str]]:
    """Backtest reports on disk, newest first."""
    directory = repo / RELATIVE_REPORTS
    if not directory.is_dir():
        return []

    found = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append(
            {
                "name": path.name,
                "path": str(path),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "size": str(stat.st_size),
            }
        )
    return found[:10]
