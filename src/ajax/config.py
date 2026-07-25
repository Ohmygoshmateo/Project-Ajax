"""Configuration loading: YAML defaults, optional local override, .env credentials.

Strategy parameters live in YAML so they are diffable and tunable. Credentials
live in .env and are never written to YAML.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "default.yaml"
LOCAL_OVERRIDE = REPO_ROOT / "config" / "local_override.yaml"
TUNED_CONFIG = REPO_ROOT / "config" / "tuned.yaml"


class AccountConfig(BaseModel):
    equity: float = 5000.0
    risk_pct_per_trade: float = 0.15
    max_concurrent_positions: int = 2
    commission_per_contract: float = 0.65

    @field_validator("max_concurrent_positions")
    @classmethod
    def _slots_in_range(cls, v: int) -> int:
        if not 1 <= v <= 2:
            raise ValueError("max_concurrent_positions must be 1 or 2")
        return v

    @field_validator("risk_pct_per_trade")
    @classmethod
    def _risk_sane(cls, v: float) -> float:
        if not 0 < v <= 0.5:
            raise ValueError("risk_pct_per_trade must be in (0, 0.5]")
        return v


class UniverseConfig(BaseModel):
    min_expected_tickers: int = 450
    max_expected_tickers: int = 520
    max_churn_per_refresh: int = 10
    benchmark: str = "SPY"


class SignalLookbacks(BaseModel):
    relative_strength_days: int = 21
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    roc_short: int = 10
    roc_long: int = 20
    volume_window: int = 20
    sma_fast: int = 20
    sma_slow: int = 50


class SignalConfig(BaseModel):
    weights: dict[str, float] = Field(default_factory=dict)
    lookbacks: SignalLookbacks = Field(default_factory=SignalLookbacks)
    rsi_sweet_low: float = 50.0
    rsi_sweet_high: float = 70.0
    rsi_overbought: float = 80.0
    top_n: int = 5
    entry_score_threshold: float = 0.75
    require_trend_gate: bool = True

    @property
    def warmup_days(self) -> int:
        """Bars of history needed before any indicator is valid."""
        lb = self.lookbacks
        return max(lb.macd_slow + lb.macd_signal, lb.sma_slow, lb.roc_long, lb.rsi_period) + 10


class OptionsConfig(BaseModel):
    shortlist_size: int = 20
    max_shortlist: int = 50
    dte_target_min: int = 30
    dte_target_max: int = 45
    dte_hard_floor: int = 21
    delta_min: float = 0.55
    delta_max: float = 0.70
    min_open_interest: int = 100
    min_volume: int = 0
    max_relative_spread: float = 0.15
    max_premium_per_contract: float = 7.50

    @field_validator("dte_hard_floor")
    @classmethod
    def _floor_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("dte_hard_floor must be >= 1")
        return v


class SpreadModel(BaseModel):
    default: float = 0.06
    wide_dte: float = 0.09


class BacktestConfig(BaseModel):
    months: int = 6
    hold_trading_days: int = 5
    risk_free_rate: float = 0.04
    vol_haircut_points: float = 3.5
    realized_vol_window: int = 21
    price_source: str = "auto"
    spread_pct_of_mid: SpreadModel = Field(default_factory=SpreadModel)


class GraduationConfig(BaseModel):
    min_win_rate: float = 0.80
    consecutive_weeks: int = 2
    min_closed_trades: int = 20
    mode: str = "strict_both_weeks"


class PathsConfig(BaseModel):
    data_cache: str = "data_cache"
    reports: str = "reports"
    logs: str = "logs"

    def resolve(self, which: str) -> Path:
        p = REPO_ROOT / getattr(self, which)
        p.mkdir(parents=True, exist_ok=True)
        return p


class Config(BaseModel):
    trading_mode: str = "paper"
    account: AccountConfig = Field(default_factory=AccountConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    signals: SignalConfig = Field(default_factory=SignalConfig)
    options: OptionsConfig = Field(default_factory=OptionsConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    graduation: GraduationConfig = Field(default_factory=GraduationConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)

    def max_position_dollars(self, equity: float | None = None) -> float:
        eq = self.account.equity if equity is None else equity
        return eq * self.account.risk_pct_per_trade


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def set_by_path(data: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    """Set ``data['a']['b'] = value`` from a dotted key ``"a.b"``. Returns a copy.

    Used by the tuner to apply grid points without hand-writing nested dicts.
    """
    out = dict(data)
    cursor = out
    parts = dotted.split(".")
    for part in parts[:-1]:
        cursor[part] = dict(cursor.get(part, {}))
        cursor = cursor[part]
    cursor[parts[-1]] = value
    return out


def load_raw_config(extra_override: dict[str, Any] | None = None) -> dict[str, Any]:
    with open(DEFAULT_CONFIG) as fh:
        raw = yaml.safe_load(fh) or {}
    for path in (TUNED_CONFIG, LOCAL_OVERRIDE):
        if path.exists():
            with open(path) as fh:
                raw = _deep_merge(raw, yaml.safe_load(fh) or {})
    if extra_override:
        raw = _deep_merge(raw, extra_override)
    return raw


def load_config(extra_override: dict[str, Any] | None = None) -> Config:
    return Config.model_validate(load_raw_config(extra_override))


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()


class Credentials(BaseModel):
    paper_key: str | None = None
    paper_secret: str | None = None
    live_key: str | None = None
    live_secret: str | None = None

    @property
    def has_paper(self) -> bool:
        return bool(self.paper_key and self.paper_secret)

    @property
    def has_live(self) -> bool:
        return bool(self.live_key and self.live_secret)


def load_paper_credentials() -> Credentials:
    """Load ONLY paper credentials. Live keys are deliberately not read here."""
    load_dotenv(REPO_ROOT / ".env", override=False)
    return Credentials(
        paper_key=os.getenv("ALPACA_PAPER_API_KEY") or None,
        paper_secret=os.getenv("ALPACA_PAPER_SECRET_KEY") or None,
    )
