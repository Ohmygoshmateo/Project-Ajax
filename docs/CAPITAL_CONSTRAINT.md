# The capital constraint

There is a hard arithmetic tension between three things this strategy wants at
once. It cannot be engineered away, so it is documented instead.

## The arithmetic

An option contract covers 100 shares, so **contract cost = premium × 100**. A
$2.50 premium is a $250 position.

For a long option, the entire premium is at risk — an option that expires out of
the money is a 100% loss on that position. So the risk budget *is* the maximum
spend:

```
max premium per share = (equity × risk_pct_per_trade) / 100
```

A 0.55-0.70 delta contract at 30-45 DTE typically costs **6-10% of the
underlying's share price**. Inverting that gives the tradeable price range:

| Equity | Risk/trade | Budget | Max premium | Tradeable underlyings |
| ---: | ---: | ---: | ---: | :--- |
| $5,000 | 2% | $100 | $1.00/share | under ~$15 |
| $5,000 | 5% | $250 | $2.50/share | under ~$30 |
| $5,000 | 10% | $500 | $5.00/share | up to ~$60-80 |
| $5,000 | 15% | $750 | $7.50/share | up to ~$90-125 |
| $5,000 | 20% | $1,000 | $10.00/share | up to ~$120-165 |
| $25,000 | 5% | $1,250 | $12.50/share | up to ~$150-200 |

At conservative sizing on a $5,000 account, most of the S&P 500 is simply
unaffordable. The scan will keep ranking names you cannot trade.

## The three-way conflict

1. **"Cheapest contract"** pushes toward low delta (far out of the money).
2. **"Best swing delta"** pushes toward high delta (in the money), because a
   5-day hold should pay as little extrinsic value as possible.
3. **A small account** cannot afford high delta on most underlyings.

Any two are satisfiable. All three are not.

## How this repository resolves it

**The delta band is never relaxed to fit the budget.** The selector filters to
the delta band *first*, then takes the cheapest survivor. If nothing in the band
is affordable, the signal is **skipped** with reason `unaffordable`.

This is deliberate. Buying a 0.20-delta lottery ticket because the 0.60-delta was
too expensive is not a cheaper version of the same strategy — it is a different
strategy, with a different win rate and a different loss profile. Silently
sliding into it would make the backtest a description of something you are not
trading. `src/ajax/options/selector.py` enforces this, and
`tests/test_selector.py::TestAffordabilityNeverRelaxesTheBand` keeps it enforced.

## Current configuration

`config/default.yaml` ships with:

```yaml
account:
  equity: 5000.0
  risk_pct_per_trade: 0.15   # $750 per position
options:
  delta_min: 0.55
  delta_max: 0.70
  max_premium_per_contract: 7.50
```

**This is aggressive sizing.** At 15% per position with up to 2 concurrent
positions, **30% of the account can be at risk simultaneously**, and because max
loss is the full premium, two losing trades in a row cost roughly that much
outright. It was chosen over conservative sizing because at 2-5% the strategy has
almost nothing to trade.

## Measure it, don't trust the table above

```bash
ajax feasibility
```

This prices today's real chains and reports how many candidates are affordable at
each combination of risk level and delta band. Use the actual numbers, not the
estimates here.

## Your four options

1. **Raise risk per trade** (current choice). More of the index becomes
   tradeable; concentration risk rises correspondingly.
2. **Lower the delta band.** Cheaper contracts, more of the index available — but
   you buy mostly time value, and theta and IV crush do more damage over a 5-day
   hold. Set `options.delta_min` / `delta_max` and re-run the backtest.
3. **Restrict the universe to lower-priced names.** Keeps good trade structure
   and conservative sizing, at the cost of a much smaller candidate pool.
4. **Fund the account higher.** $15,000-25,000 makes 2-5% sizing workable across
   most of the index. This is the only option that resolves the tension rather
   than trading one problem for another.

Vertical spreads would reduce cost while keeping directional exposure, but they
require options Level 3 and multi-leg order handling, and are out of scope here.
