# Project-Ajax

An S&P 500 options swing-trade scanner, backtester, and paper-trading agent.

Each trading day it ranks the index for strength and weakness, picks an
affordable contract for a ~5-trading-day swing trade, and can place the order
into an Alpaca **paper** account on a schedule. It backtests the same logic over
the trailing six months and tracks whether the paper record has earned the right
to be taken seriously.

> **This is not financial advice, and the backtest is not a prediction.** The
> backtest is optimistic by construction — read [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
> before trusting any number it prints. Real-money trading is not implemented.

---

## Quick start

```bash
pip install -r requirements.txt -r requirements-dev.txt
pip install -e .

cp .env.example .env          # add your free Alpaca paper keys
ajax doctor                   # probe what your data plan actually provides
ajax scan --dry-run           # today's candidates, no orders placed
```

`ajax doctor` comes first for a reason: two facts about your account determine
how the rest of the tool behaves, and neither is reliably documented. See
[Setup](#setup).

---

## How it works

**1. Rank the universe.** One batched price download covers all ~500 tickers.
Six components — relative strength vs SPY, MACD histogram, banded RSI, two
rate-of-change windows, and volume z-score — are z-scored *across the universe*
and blended into a composite. The top 5 become call candidates, the bottom 5
puts. A separate trend gate decides whether a candidate is actionable at all.

**2. Shortlist, then fetch chains.** Option chains cannot be batched, and one
request per S&P 500 name would be several hundred sequential calls into a rate
limiter. Only the shortlist gets chain requests.

**3. Select a contract.** Filter by expiration window (30-45 DTE target, hard
floor 21), then liquidity, then the delta band (0.55-0.70), then affordability —
and take the **cheapest survivor of the delta band**. If nothing in the band is
affordable the signal is skipped, with the reason recorded. The band is never
widened to fit the budget; see [docs/CAPITAL_CONSTRAINT.md](docs/CAPITAL_CONSTRAINT.md).

**4. Size and allocate.** Risk budget divided by contract cost, floored at one
contract. With 1-2 concurrent positions, slots go to the highest-conviction
signals, never two to one underlying.

**5. Hold five trading days, then sell.** The contract still has weeks of life
left at exit — that is the point. Holding into the final two weeks is where gamma
and theta turn nonlinear.

---

## Commands

| Command | What it does |
| --- | --- |
| `ajax doctor` | Probe greeks availability, option-bar history depth, rate-limit headroom |
| `ajax universe show` / `refresh` | Inspect or update the S&P 500 list (refresh is sanity-checked) |
| `ajax scan --dry-run` | Rank the universe, show candidates and suggested contracts |
| `ajax select --ticker AAPL --direction call` | Explain the choice for one ticker, or why it was skipped |
| `ajax feasibility` | How much of the universe you can actually afford, by risk level and delta band |
| `ajax backtest --months 6` | Simulate and write a report to `reports/` |
| `ajax tune --param-grid config/param_grid_example.yaml` | Grid search with a train/holdout split |
| `ajax agent run-once` | One paper-trading cycle: close due positions, scan, order, log news |
| `ajax agent serve` | Always-on scheduler (cron plus `run-once` is usually better) |
| `ajax status` | Open positions, track record, skip reasons |
| `ajax graduate-check` | Has the paper record met the bar? |
| `ajax enable-live` | The manual gate. Never invoked by automation |

---

## Setup

### 1. Alpaca paper account

Free at [alpaca.markets](https://alpaca.markets). Paper accounts get options
**Level 3 by default with no application**, which covers the long calls and puts
this strategy uses. (Only *live* accounts require an options application.)

Copy `.env.example` to `.env` and fill in `ALPACA_PAPER_API_KEY` and
`ALPACA_PAPER_SECRET_KEY`.

### 2. Run the probe

```bash
ajax doctor
```

Two things it establishes:

- **Do chains return greeks?** The free Basic plan serves the *indicative* feed,
  and whether greeks populate there is undocumented. If they do not, deltas are
  modelled from implied volatility and stamped with weaker provenance — visible
  in every trade record.
- **How far back do option bars go?** Real historical option prices make the
  backtest far more trustworthy than model reconstruction. Where they are
  missing, Black-Scholes fills in and the report says what fraction was modelled.

### 3. Refresh the universe

```bash
ajax universe refresh
```

The bundled ticker list is a best-effort seed, not verified against the live
index. A refresh is rejected — keeping the cached list — if it returns fewer than
450 or more than 520 tickers, or churns more than 10 names in one day.

---

## Scheduling

The agent is stateless: every run reconciles against the broker and the local
log, so a missed run is self-healing and no daemon is required.

```cron
# Weekdays at 09:32 America/New_York. Signals come from the prior close and
# orders are placed shortly after the open — the same convention the backtest
# simulates.
32 9 * * 1-5 cd /path/to/Project-Ajax && TZ=America/New_York python -m ajax agent run-once >> logs/agent.log 2>&1
```

---

## Going live

You cannot, from here, and that is deliberate.

`ajax agent run-once` and the scheduler import the paper client **directly** and
never branch on a trading-mode flag. There is no code path from automation to a
live endpoint — not a disabled one, an absent one. `tests/test_graduation.py`
asserts this structurally, so it cannot regress quietly.

`ajax graduate-check` reports whether the paper record has cleared:

- **≥80% win rate across two consecutive weeks**, and
- **at least 20 closed trades.**

The trade-count floor matters more than the percentage. At 1-2 concurrent
positions a fortnight yields ~2-4 closed trades — 3-for-4 is 75% and 4-for-4 is
100%, both essentially by luck. Below the floor the tool reports "insufficient
sample" instead of a flattering number. Expect 3-5 months to get there.

Passing unlocks nothing. `ajax enable-live` requires an explicit risk flag, a
typed confirmation phrase, a currently-passing check, and separate live
credentials — and **still does not place real trades**, because live execution is
intentionally not implemented in v1. It records the acknowledgement and tells you
what would actually be involved.

---

## Configuration

Everything tunable lives in `config/default.yaml`. To override, copy
`config/local_override.yaml.example` to `config/local_override.yaml` (gitignored)
and set only the keys you want changed.

Notable defaults, and why:

| Setting | Default | Reasoning |
| --- | --- | --- |
| `delta_min` / `delta_max` | 0.55 / 0.70 | Less extrinsic value to decay over a 5-day hold. No broker publishes a "correct" swing delta — this is convention, and it is swept by the tuner |
| `dte_target_min/max`, `dte_hard_floor` | 30 / 45, 21 | Gamma and theta go nonlinear inside the last two weeks |
| `risk_pct_per_trade` | 0.15 | Aggressive by design — at 2-5% a $5k account can barely trade the index. See [docs/CAPITAL_CONSTRAINT.md](docs/CAPITAL_CONSTRAINT.md) |
| `vol_haircut_points` | 3.5 | Offsets the volatility risk premium so modelled backtests are less optimistic |
| `min_closed_trades` | 20 | Below this a win rate is noise, not evidence |

---

## Testing

```bash
pytest -q --cov=src/ajax --cov-report=term-missing
```

The suite is **network-free** and runs in about a minute. Coverage concentrates
where correctness is load-bearing: Black-Scholes and greeks, the selector's
filter cascade, position sizing, P&L arithmetic, the graduation criterion, and
the structural guarantees around live trading.

Network-dependent code (yfinance, Alpaca) is mocked or excluded by design. Live
calls are a manual checklist:

```bash
ajax doctor                 # real credentials, real chain
ajax scan --dry-run         # real prices and chains, no orders
ajax select --ticker AAPL --direction call
ajax agent run-once --dry-run
```

---

## Project layout

```
src/ajax/
├── cli.py            config.py      capabilities.py
├── data/             universe, prices, news, Alpaca + yfinance chains, symbols
├── signals/          indicators, cross-sectional scoring, labelling
├── options/          Black-Scholes greeks, volatility, the selector
├── portfolio/        sizing, slot allocation
├── backtest/         walk-forward engine, price sources, costs, metrics, reports, tuner
├── broker/           Alpaca clients, OCC symbols, orders
├── agent/            run loop, trade log, graduation, scheduler
└── live/             the manual gate — imported by nothing in agent/
```

---

## Known limitations

Summarized above and detailed in [docs/LIMITATIONS.md](docs/LIMITATIONS.md). The
short version: the backtest is optimistic, the sample is small, news is
live-only, and a grid search will always find a winner. Read it before drawing
conclusions.
