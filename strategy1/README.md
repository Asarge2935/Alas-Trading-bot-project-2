# strategy1 — Regime + Relative Strength Rotation

This package is the implementation track for the project's first
candidate strategy after the 2026-05 reset. **Spec:**
[`docs/STRATEGY_1_REGIME_RS_SPEC.md`](../docs/STRATEGY_1_REGIME_RS_SPEC.md).

## Status

This package now hosts two related strategies that share the data
layer and regime logic:

**Strategy 1 — RS rotation** (paused): the original multi-asset
relative-strength rotation. Data layer, regime classifier, and RS
ranker are implemented; the rotation backtester was never built
because the user redirected the project to BTC-only.

**Strategy 2 — BTC directional** (current focus): single-instrument
long/short BTC trend strategy. Spec:
[`docs/STRATEGY_2_BTC_DIRECTIONAL_SPEC.md`](../docs/STRATEGY_2_BTC_DIRECTIONAL_SPEC.md).

| Component | Status | Used by |
|---|---|---|
| Data layer (`data.py`, `quality.py`) | ✅ | both |
| Universe + RS ranker (`universe.py`) | ✅ | Strategy 1 only |
| Regime classifier (`regime.py`) | ✅ | both |
| Directional signal (`regime.directional_signal`) | ✅ | Strategy 2 |
| BTC backtester (`btc_backtest.py`) | ✅ | Strategy 2 |

> **Execution reality:** the user's Crypto.com App account is spot-only
> and cannot short. Only the `long_flat` variant is executable today.
> `short_flat` and `long_short` are backtested to *measure* whether the
> short side has edge — not because they can be traded now. See the
> spec §0 and `docs/VENUE_AUDIT_CRYPTO_COM_APP.md`.

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
stale-close run, freshness (days since last bar), and outlier count.

Hard gates (any one fails the product):
- Gap ratio > 2% inside the covered window.
- Longest stale-close run ≥ 5 days.
- Last bar > 7 days old (catches delistings and ticker rebrands like
  MATIC → POL or RNDR → RENDER, which otherwise look fine inside
  their covered window).

**Do not proceed to the backtester until every product needed for
the universe passes.** Failing tickers should be either replaced
with their rebrand successor in `data.py` or removed from the
universe.

### Known data-source limitations

- **~4-year history ceiling.** Coinbase's public candles endpoint
  serves at most about 1500 daily bars per product. That is the
  upper bound on the in-sample window from this source alone. The
  current dataset covers 2022 bear → 2023–24 recovery → 2025–26
  mixed. **It does not include the 2020–21 bull or the 2018 bear.**
  Strategy 1's spec §10 gate 7 (works in multiple regimes) cannot
  be fully tested against this data alone. Mitigations (later, not
  now): supplement with CoinGecko or CryptoCompare for older bars.
- **Ticker churn.** Coinbase periodically delists or rebrands
  products. The candidate list keeps both the predecessor and the
  successor where known (MATIC + POL, RNDR + RENDER). The
  point-in-time universe will use each for the period it was
  active.

### 3. Eligible universe and RS ranking (point-in-time)

```python
from pathlib import Path
from strategy1.universe import load_all, top_n_at, rank_by_return

all_data = load_all(Path("data_cache"))

# Eligibility: top-15 by trailing 90D dollar volume on the as-of date.
eligible = [pid for pid, _ in top_n_at("2024-01-01", all_data,
                                       n=15, lookback_days=90)]

# Selection: top-5 by trailing 30D return *within* the eligible pool.
basket = [pid for pid, _ in rank_by_return("2024-01-01", all_data,
                                           eligible, n=5, lookback_days=30)]
```

Why split eligibility from selection:
- Volume-based eligibility says "this is liquid enough to trade."
- Return-based selection says "among the liquid set, this is strong."
- Doing the return ranking on the full universe instead would happily
  pick a freshly-listed pumper with no real liquidity. Don't.

Both rankings enforce point-in-time honesty:
- Minimum 120 days of history on the as-of date (no listing pumps).
- A bar must exist on the as-of date itself (no stale prices).
- All windows look strictly backward from the as-of date.

### 5. BTC directional backtest (Strategy 2 — current focus)

```bash
python -m strategy1.btc_backtest --in data_cache/            # Definition A-dir (trend only)
python -m strategy1.btc_backtest --in data_cache/ --vol-aware # Definition B-dir (vol stand-down on longs)
```

Runs three variants (`long_flat`, `short_flat`, `long_short`) plus a
buy-and-hold benchmark, and scores each against the spec §7 gates.
Prints a verdict per variant. Reminder printed at the end: only
`long_flat` is executable on the current spot account.

Read the verdicts honestly. The expected modal outcome is that the
short side's edge is concentrated in the 2022 downtrend (gate 6/7),
which would mean it is regime-dependent and **not** a reason to open
a perp venue.

### 4. BTC regime classification

```bash
python -m strategy1.regime --in data_cache/
```

Prints overall and yearly risk-on/off counts for both Definition A
(trend only) and Definition B (trend + non-stress vol). The spec
calls for testing both definitions independently — picking the
winner in-sample, re-testing on out-of-sample — so this module
exposes both. The picking happens in the backtest report, not here.

Library use:

```python
from strategy1.regime import regime_a, regime_b
a = regime_a(all_data["BTC-USD"]["close"])  # Series of risk_on/risk_off
b = regime_b(all_data["BTC-USD"]["close"])
```

By construction, every `risk_on` bar in B is also `risk_on` in A
(B applies an additional vol filter; it never relaxes A).

## What this package will not do

- Place orders.
- Hold credentials.
- Make decisions about capital allocation.
- Tune strategy parameters before a v0 result exists.

These are deferred until each prior phase has produced a verdict.
