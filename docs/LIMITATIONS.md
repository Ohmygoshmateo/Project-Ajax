# Limitations

Read this before trusting any number this tool produces.

## The backtest is optimistic by construction

Every approximation below pushes results in the same direction: **too good**.
They compound. The backtest is a screen for obviously-broken strategy logic, not
a forecast of returns.

### 1. Implied volatility history does not exist at a free tier

No free source publishes per-ticker historical implied volatility. When the
backtest falls back to Black-Scholes reconstruction it prices options from
*realized* volatility instead.

This matters more than it sounds. **Implied volatility exceeds subsequently
realized volatility roughly 85% of the time, by about 2-4 volatility points** —
the volatility risk premium. Pricing entries at bare realized vol means buying
every option cheaper than it really traded, which flatters a long-options
strategy specifically.

The mitigation is `backtest.vol_haircut_points` (default `3.5`), which adds
volatility points to entry pricing. It is a blunt average, not a correction: the
premium varies by ticker, regime, and tenor. Set it to `0.0` and compare runs to
see how much of the result rests on this single number.

Where Alpaca historical option bars are available they are used instead, and
those are real traded prices. Every report shows the split.

### 2. Bid-ask spreads are modelled, not historical

Historical NBBO for options is not available at a free tier. Spreads are
modelled as a fixed fraction of mid (6% default, 9% under 28 DTE). Real spreads
vary with liquidity, volatility, and time of day, and are frequently worse than
this for the contracts a small account can afford.

Buys fill at the modelled ask and sells at the modelled bid — never at mid.

### 3. Greeks are always modelled in the backtest

Real historical greeks do not exist at any accessible price, so strike selection
in the backtest always uses a Black-Scholes delta computed from realized
volatility plus the haircut. Even when *pricing* uses real bars, the *contract
choice* is approximate. A real chain would have offered a different strike.

### 4. Survivorship bias

The backtest reconstructs index membership from `src/ajax/data/sp500_changes.csv`
where it can. That file ships nearly empty, so in practice most runs apply
**today's** S&P 500 membership to historical dates. Companies that were removed
from the index — typically after poor performance — are missing from the sample.
Every report states which mode applied.

To reduce this, populate `sp500_changes.csv` with real add/remove history.

### 5. No early assignment, no IV crush, no earnings modelling

Long options are not assigned early, so that is not modelled. But **implied
volatility crush around earnings is not modelled either**, and it is a real and
frequently fatal cost for a directional option buyer: the stock can move your way
and the option still loses money. The backtest will not show you that happening.

### 6. Fills are assumed

Every order fills, at the modelled price, in full. In reality a limit order at a
poor price does not fill, and a market order into a thin book fills worse than
modelled.

## The scan and paper trader

### News is live-only

`Ticker.news` returns only recent articles. News from six months ago is not
retrievable, so the backtest ignores news entirely. It is a live/paper monitoring
feature and nothing more. Do not read backtest results as though a news filter
was applied — none was.

### The yfinance news schema is not contractual

The shape of `Ticker.news` has changed across yfinance versions. Parsing is
defensive and skips items it cannot read, so a schema change degrades headlines
silently rather than crashing a run. If news stops appearing, that is why.

### Chain data depends on your Alpaca plan

The free Basic plan serves the **indicative** feed, not OPRA. Whether greeks are
populated there is undocumented. Run `ajax doctor` to find out what your account
actually returns. If greeks are absent, deltas are modelled from implied
volatility (or realized volatility) and stamped `bs_from_alpaca_iv` /
`bs_from_realized_vol` — visible in the trade log so you can tell which trades
rested on weaker data.

### The universe list drifts

`src/ajax/data/sp500_cached.json` ships as a best-effort seed snapshot. It is not
verified against the live index. Run `ajax universe refresh` (needs network
access to Wikipedia) to replace it with a sanity-checked current list.

## Statistical limitations

### The sample is small, structurally

With 1-2 concurrent positions and a 5-trading-day hold, the strategy produces
roughly **1-2 trades per week**. Reaching 20 closed trades takes about 3-5 months
of paper trading. Any win rate computed before then is noise — going 3-for-4 is
75% and 4-for-4 is 100%, both by luck.

This is why `ajax graduate-check` refuses to report a headline win rate below the
trade-count floor.

### `ajax tune` will find a winner whether or not one exists

A grid search over six months with few trades per configuration always produces a
leaderboard. Ranking is by *holdout* expectancy rather than in-sample win rate to
blunt this, but a one-month holdout is itself small. Treat the top row as a
hypothesis to test forward, not as a tuned strategy.

## What this tool is not

- It is **not financial advice**.
- The backtest is **not a prediction**.
- A passing graduation check is **evidence, not proof**, and unlocks nothing on
  its own.
- Real-money order execution is **not implemented**. See `src/ajax/live/gate.py`.
