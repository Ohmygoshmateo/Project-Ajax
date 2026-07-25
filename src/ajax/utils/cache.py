"""Parquet/JSON disk caching for price history and option bars.

Network data is cached by an explicit key so repeated backtest runs and tuner
sweeps do not re-hit providers hundreds of times.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)


class DiskCache:
    def __init__(self, root: Path, namespace: str) -> None:
        self.dir = Path(root) / namespace
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str, suffix: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.dir / f"{digest}{suffix}"

    def get_frame(self, key: str, max_age: timedelta | None = None) -> pd.DataFrame | None:
        path = self._path(key, ".parquet")
        if not path.exists():
            return None
        if max_age is not None:
            age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
            if age > max_age:
                return None
        try:
            return pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001 - a corrupt cache should not be fatal
            log.warning("discarding unreadable cache entry %s: %s", path.name, exc)
            return None

    def put_frame(self, key: str, frame: pd.DataFrame) -> None:
        try:
            frame.to_parquet(self._path(key, ".parquet"))
        except Exception as exc:  # noqa: BLE001 - caching is best-effort
            log.warning("could not cache frame for %s: %s", key, exc)

    def get_json(self, key: str, max_age: timedelta | None = None) -> Any | None:
        path = self._path(key, ".json")
        if not path.exists():
            return None
        if max_age is not None:
            age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
            if age > max_age:
                return None
        try:
            with open(path) as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            log.warning("discarding unreadable cache entry %s: %s", path.name, exc)
            return None

    def put_json(self, key: str, payload: Any) -> None:
        try:
            with open(self._path(key, ".json"), "w") as fh:
                json.dump(payload, fh, default=str)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not cache json for %s: %s", key, exc)
