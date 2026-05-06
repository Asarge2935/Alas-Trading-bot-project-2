# Sample Run — Alas Trading Bot Backtester

This document is a how-to-run guide and an explanation of what the
output should look like. It does **not** include live API output —
the user has not green-lit hitting Coinbase's public API from this
session. Run the backtester locally and paste the summary back if
you want a sanity check.

---

## Prerequisites

- Python 3.10+ (3.11 or 3.12 recommended).
- `pip` available.
- Network access to `https://api.exchange.coinbase.com`.
- A clean working directory; the script writes to `./backtest_output/`.

## Install

From the repo root:

```bash
pip install requests pandas numpy
```

Optional, if you want a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate    # macOS / Linux
.venv\Scripts\activate       # Windows
pip install requests pandas numpy
```

## Run

```bash
python backtest.py
```

The script:

1. Pulls 365 days of 6H candles for each of BTC, ETH, SOL, XRP, ADA, DOT
   (300 candles per request, paginated, with a 0.3s polite delay between calls).
2. Computes EMA(50/20), RSI(14), ATR(14), 30-day-avg ATR, and 20-bar avg volume.
3. Runs the scanner over every 6H bar, opens / partials / closes trades.
4. Writes `backtest_output/trades.csv` and `backtest_output/equity_curve.csv`.
5. Prints a per-bucket and portfolio summary.

Expected runtime: 30–90 seconds on a typical laptop, dominated by the
6 paginated API calls. There are roughly 5 paginated requests per asset
for 365 days at 6H, so about 30 HTTP calls total.

---

## Expected output shape

```
Backtest config: 365 days, 6H bars, 6 assets
Assets: BTC-USD, ETH-USD, SOL-USD, XRP-USD, ADA-USD, DOT-USD
Indicators: EMA20/50, RSI14, ATR14, Vol20
Filters: BTC regime (35-65), volume >= 1.2x avg

Fetching BTC-USD ... 1460 bars (2025-05-06 to 2026-05-06)
Fetching ETH-USD ... 1460 bars (2025-05-06 to 2026-05-06)
Fetching SOL-USD ... 1460 bars (2025-05-06 to 2026-05-06)
Fetching XRP-USD ... 1460 bars (2025-05-06 to 2026-05-06)
Fetching ADA-USD ... 1460 bars (2025-05-06 to 2026-05-06)
Fetching DOT-USD ... 1460 bars (2025-05-06 to 2026-05-06)

Running scanner backtest...
Backtest complete. <N> trades simulated.
Exported <N> trades to ./backtest_output/trades.csv
Exported equity curve (<bars>) points to ./backtest_output/equity_curve.csv

======================================================================
BACKTEST SUMMARY (365 days, 6H bars)
======================================================================

--- ALL ---
  Trades:              <N>
  Win rate:            <pct>%   (<W>W / <L>L)
  Profit factor:       <pf>
  Avg R-multiple:      <r>R
  Gross P&L:           $<gross>
  Fees:               -$<fees>
  Slippage:           -$<slip>
  Funding drag:       -$<fund>
  Net P&L:             $<net>

--- ALL_long ---
  ... (same metrics, longs only)

--- ALL_short ---
  ... (same metrics, shorts only)

--- BTC-USD ---  /  --- BTC-USD_long ---  /  --- BTC-USD_short ---
  ... (per-asset, per-asset-per-side)

(...same for ETH, SOL, XRP, ADA, DOT)

--- PORTFOLIO ---
  Starting capital:    $500.00
  Final equity:        $<eq>
  Net P&L:             $<net>
  Return on capital:   <pct>%
  Max drawdown:        <dd>%
  Max consecutive losses: <streak>

======================================================================
INTERPRETATION GUIDE
======================================================================
Net Profit Factor > 1.5 = potentially viable, paper trade for 30 days
Net Profit Factor 1.0-1.5 = marginal, probably not worth deploying
Net Profit Factor < 1.0 = strategy loses money after costs, do not deploy
Avg R > +0.2 = each trade has positive expectancy
Max DD > 25% = risk parameters too aggressive
Max consecutive losses > 6 = high tail risk, expect rough patches
```

## Expected ranges (rough sanity checks)

For a strategy of this shape, on a 12-month window covering majors:

- Trade count: 30–150 across all 6 assets
- Win rate: 35–55% (low because of the 2:1 R:R structure and partial-then-runner)
- Profit factor: 0.8–1.8 in normal markets; outside that range, suspect
- Avg R: −0.2R to +0.5R
- Max drawdown: 8–22%
- Max consecutive losses: 3–7

If your run shows a 4.0 profit factor or a 90% win rate, treat it as
broken or curve-fit, not as a winning strategy.

---

## CSV columns

### `trades.csv`

| Column | Meaning |
|---|---|
| symbol, side | trading pair, `long` / `short` |
| signal_time | bar N close where signal formed |
| entry_time | bar N+1 open where order filled |
| entry_price | fill price |
| stop_price | initial 2 ATR stop, moves to BE after partial |
| target_1_price, target_2_price | 3 ATR / 6 ATR targets |
| initial_stop_distance | dollar distance from entry to initial stop |
| notional_usd, margin_usd, leverage | sizing |
| rsi_at_signal, atr_at_signal, btc_rsi_at_signal | snapshot at signal |
| exit_time, exit_price_partial, exit_price_final | exits |
| exit_reason | `stop_hit` / `target_2_runner_hit` / `time_stop` / `regime_flip` / `backtest_end` |
| bars_held | full bars between entry and final exit |
| gross_pnl_usd, fees_usd, slippage_usd, funding_usd, net_pnl_usd | P&L breakdown |
| r_multiple | net P&L expressed in units of initial risk |

### `equity_curve.csv`

| Column | Meaning |
|---|---|
| time | bar timestamp (UTC) |
| equity | account value = $500 + cumulative P&L |
| cumulative_pnl | sum of net P&L of all closed trades up to this point |
| open_positions | 0, 1, or 2 |
| drawdown_pct | (peak_equity − equity) / peak_equity × 100 |

---

## Common errors

### `requests.exceptions.HTTPError: 429 Too Many Requests`

Coinbase's public endpoint rate-limits anonymous traffic. The script
sleeps 0.3s between calls; if you still hit 429, increase the sleep to
0.5s and rerun. With 6 assets × ~5 pagination chunks, total request
count is low and 429s are rare.

### `KeyError: 'time'` after `pd.DataFrame` construction

The API returned an unexpected schema. Print the raw response — usually
this happens when an invalid product_id is passed (e.g., a typo in
`ASSETS`).

### `0 bars` for a symbol

The endpoint returned no candles for that symbol over the requested
range. Check the symbol exists on Coinbase Advanced today (delisting
mid-period would also cause partial coverage).

### `ValueError: cannot convert float NaN to integer` in `bars_held`

This was fixed in this rebuild — `bars_held` is now coerced via
`int(bars_held)` after the time-stop check, and the mark-to-market
branch computes it from the actual last bar timestamp. If you see this
on a stock checkout, you've likely diverged from `backtest.py`.

---

## After the run

1. Open `backtest_output/trades.csv` and pick 10 random trades.
2. For each, walk through `PRE_PAPER_TRADE_CHECKLIST.md` Section B
   (strategy fidelity).
3. Open `equity_curve.csv` in a spreadsheet, plot `equity` vs `time`.
4. Run through Section G (curve shape) by eye.
5. If everything passes, follow the rest of `PRE_PAPER_TRADE_CHECKLIST.md`.

If something looks wrong, paste the summary block and a few rows of
trades.csv into the next session and we'll dig into it.
