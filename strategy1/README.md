# strategy1 — Regime + Relative Strength Rotation

This package is the implementation track for the project's first
candidate strategy after the 2026-05 reset. **Spec:**
[`docs/STRATEGY_1_REGIME_RS_SPEC.md`](../docs/STRATEGY_1_REGIME_RS_SPEC.md).

## Status

| Phase | Status |
|---|---|
| Data layer (this commit) | ✅ implemented |
| Regime classifier | ⏳ next |
| Relative-strength ranker | ⏳ |
| Backtester | ⏳ |
| Report generator (10 gates) | ⏳ |

No execution wiring exists. None will exist until the data quality
report passes and the backtest produces a verdict.

## Setup

```bash
pip install -r ../requirements.txt
```

## Usage

### 1. Fetch historical daily candles

Pulls daily OHLCV for the candidate universe (30 USD spot pairs on
Coinbase Exchange, defined in `data.py` as `CANDIDATE_PRODUCTS`).
Default 1500 days ≈ 4 years of history.

```bash
python -m strategy1.data --days 1500 --out data_cache/
```

Notes:
- Uses Coinbase Exchange's public REST API. No API key required.
- Roughly 30 requests × 5 pages × ~0.3s = ~45s of network time.
- Output: one CSV per product in `data_cache/<PRODUCT>.csv`.
- `data_cache/` is gitignored — do not commit cached candles.
- Products not yet listed on Coinbase are skipped, not errored.

### 2. Run the data quality report

```bash
python -m strategy1.quality --in data_cache/
```

Produces a per-product table with bar count, gap ratio, longest
stale-close run, and outlier count. Exits nonzero if any product
fails a hard gate (gap ratio > 2% or stale-close run ≥ 3 days).

**Do not proceed to the backtester until every product needed for
the universe passes.** The whole point of the reset is to avoid
building strategies on dirty data.

### 3. Point-in-time universe (library use, not yet a CLI)

```python
from pathlib import Path
from strategy1.universe import load_all, top_n_at

all_data = load_all(Path("data_cache"))
top5 = top_n_at("2024-01-01", all_data, n=5, lookback_days=90)
# → [('BTC-USD', dollar_volume), ('ETH-USD', ...), ...]
```

This is the function the (yet-to-be-built) backtester calls at
each rebalance date. It enforces:
- Minimum 120 days of history on the as-of date (no listing pumps).
- Minimum 90-day rolling dollar-volume sample.
- A bar must exist on the as-of date itself (no stale prices).

Anything that would have biased the backtest toward today's
survivors is excluded by construction.

## What this package will not do

- Place orders.
- Hold credentials.
- Make decisions about capital allocation.
- Tune strategy parameters before a v0 result exists.

These are deferred until each prior phase has produced a verdict.
