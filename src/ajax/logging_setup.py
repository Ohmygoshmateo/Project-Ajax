"""Logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler


def configure(verbose: bool = False, log_dir: Path | None = None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [
        RichHandler(rich_tracebacks=True, show_path=False, show_time=False)
    ]

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "ajax.log")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        handlers.append(file_handler)

    logging.basicConfig(level=level, format="%(message)s", handlers=handlers, force=True)
    # These are chatty at INFO and drown out anything useful.
    for noisy in ("urllib3", "peewee", "yfinance", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
