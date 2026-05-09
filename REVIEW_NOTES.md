# Review Notes — Alas Trading Bot Backtester

Branch: `claude/new-session-ysAXb`

This document records every issue found during review and what was
done about it. It is intended to be read alongside `backtest.py`,
`rules.json`, and `docs/HANDOFF.md`.

There have been three review passes on the original strategy plus a
strategic redesign:

- **First pass (2026-05-06)** — initial finalization from the handoff.
  Documented near the bottom of this file.
- **Second pass (2026-05-06)** — backtest realism, mark-to-market,
  API resilience, doc sync.
- **Third pass (2026-05-07)** — first live backtest produced 4 trades.
  Funnel diagnostic identified two over-restrictive filters; removed
  in v2.1 and RSI thresholds widened in v2.2.
- **Phase 1 redesign (2026-05-08)** — v2.2 1-year and 3-year backtests
  both produced PF 0.52–0.54 across regimes. Verdict: no edge in this
  shape. Strategy redesigned from scratch as a 1D BTC-regime
  volatility breakout in a separate file `breakout_backtest.py`.
  Documented immediately below.

---

## Phase 1 — strategy redesign (1D volatility breakout)

### Trigger

After v2.2 produced a regime-independent verdict of "no edge" (PF 0.52
across 94 trades and ~3 years of multi-regime data), the strategy was
abandoned in place. v2.2 stays in the repo as the archived failed
experiment. The user spec'd a fundamentally different strategy and
chose to start with a single setup before adding more.

### Spec source

User-provided design, locked in 2026-05-08:

- 1D timeframe (vs 6H) — 4× lower per-trade fee exposure
- BTC, ETH, SOL only (Phase 1 majors); ADA / XRP / DOT only added if
  majors prove edge
- BTC trend regime: longs only when BTC close > BTC EMA50, shorts only
  when BTC close < BTC EMA50
- Compression on the bar BEFORE the breakout: ATR(14) < 0.7 × ATR(60)
- Breakout: close clears prior 20-bar high/low by ≥ 0.1 × ATR(14)
- Volume confirmation: volume ≥ 1.2 × 20-bar average
- Structure stop: prior 20-bar low (long) / high (short)
- Exits: partial 50% at +2R, BE stop, then chandelier 3 × ATR with BE
  as floor; time stop 20 daily bars
- Risk: same $5/trade, max 2 open, max 5/week, drawdown 15%/25%
- Cooldown: 5 daily bars on a symbol after a clean stop-out (no partial)
- Skip guards: stop_distance_pct > 20%, notional < $25
- Setup tagging: every trade carries `setup_type = "breakout"` so
  Phase 2/3 setups (failed-breakout, flush) get separate attribution
- No regime-flip exit (chandelier handles reversals)
- Validation gates: PF ≥ 1.3, avg R > 0, no single asset > 50% of |net
  P&L|, ≥30 trades over 3 years, max consec losses < 8

### Defaults locked in (the four ambiguities from the planning chat)

1. **Structure stop placement** — `min(low) / max(high) of previous 20
   bars` (Donchian range edge). Wide stops are acceptable; fixed-dollar
   risk just shrinks the notional.
2. **Compression timing** — checked on the bar BEFORE the breakout
   (`prev_row["compression_ratio"] < 0.7`). Checking on the breakout
   bar itself would reject the very expansion the strategy wants.
3. **Chandelier + BE composition** — `current_stop = max(BE,
   chandelier)` for longs, `min(BE, chandelier)` for shorts. BE is
   the floor, chandelier is the profit ratchet. Stops never move
   backward.
4. **Volume filter rehabilitation** — same 1.2× threshold that was
   wrong for v2.2's pullback strategy is correct for breakouts (low-
   volume breakouts are notoriously fakey). Same number, opposite role.

### Files

- `breakout_backtest.py` — new, self-contained, ~530 lines. Imports
  cost / fetch / sizing / MTM helpers from `backtest.py`; defines its
  own indicators, signal, exit, trade dataclass, and run loop.
- `verify_offline_breakout.py` — new, ~190 lines. Synthetic-data gate
  harness. Confirms before any live run that the pipeline holds:
  CSVs written, P&L reconciles, MTM applied, drawdown non-negative,
  trade limits respected, every trade tagged `setup_type="breakout"`,
  cooldown invariant respected after clean stop-outs.

### Out of scope for Phase 1

- Phase 2 (failed-breakout) and Phase 3 (flush / compression) setups
- Paper trader (`paper_trader.py`) — built only after Phase 1 passes
- Coinbase live adapter — built only before live capital, not now
- Any tuning of v2.2's RSI/EMA/ATR knobs (explicit user "do not")
- Building all 4 setups at once (explicit user "do not")
- Forcing 6 assets when 3 suffice (explicit user "do not")

### Action item for the user

```bash
cd Alas-Trading-bot-project-2
git pull
python3 verify_offline_breakout.py     # offline gate, ~10s, no network
python3 breakout_backtest.py           # ~2-3 min, fetches Coinbase 1D
ls breakout_output/                    # confirms trades.csv + equity_curve.csv
```

If the live run passes the printed Phase 1 validation gates → proceed
to Phase 2 (add failed-breakout as a separate `setup_type`). If it
fails → user decides between Phase 1 alternative (4H liquidation
flush), Phase 1 with a different breakout config, or stopping the
strategy family.

---

## Third review pass — filter funnel diagnostic and removal (v2.1)

### Trigger

User ran `python backtest.py` locally for the first time on 2026-05-07
against 2025-05-08 → 2026-05-07 Coinbase candles. Result: **4 trades total
across 6 assets, 0 wins, longs only, all losers, net P&L −$5.66, max DD
1.20% MTM.** Multiple checklist gates failed:

- §A trades.csv ≥ 30 rows: ❌ (only 4)
- §C net profit factor ≥ 1.5: ❌ (0.00)
- §C trade count 30–200: ❌ (4)
- §D ≥ 4 of 6 assets with ≥ 5 trades each: ❌ (only ETH and XRP, 2 trades each)
- §D both long and short trades fired: ❌ (longs only)
- §F costs 15–35% of |gross|: ❌ (~56%)

### Funnel diagnostic data

`verify_filter_funnel.py` was run on the same data set. Result:

| Step | Bars | % of total |
|---|---|---|
| Total bar-asset pairs evaluated | 7962 | 100% |
| After ATR regime gate | 7876 | 98.9% |
| After volume filter (≥ 1.2× 20-bar avg) | 2373 | 29.8% |
| LONG: + close > EMA50 | 1085 | 13.6% |
| LONG: + EMA20 > EMA50 | 796 | 10.0% |
| LONG: + RSI < 30 | 4 | 0.05% |
| LONG: + BTC RSI ≥ 35 | 4 | 0.05% (final) |
| SHORT: + close < EMA50 | 1288 | 16.2% |
| SHORT: + EMA20 < EMA50 | 1142 | 14.3% |
| SHORT: + RSI > 70 | 6 | 0.08% |
| SHORT: + BTC RSI ≤ 65 | **0** | 0.0% (final) |

Counterfactuals (signal counts under different filter configurations):

| Configuration | Long | Short | Total |
|---|---|---|---|
| Baseline (current rules) | 4 | 0 | 4 |
| Volume threshold 1.2× → 1.0× | 4 | 2 | 6 |
| RSI 30/70 → 35/65 | 5 | 5 | 10 |
| Drop BTC regime only | 4 | 6 | 10 |
| Drop volume only | 19 | 12 | 31 |
| **Drop volume + BTC regime** | **46** | **61** | **107** |

### Diagnosis and action

Two filters were responsible for the funnel collapse:

**1. Volume filter (≥ 1.2× 20-bar avg) — REMOVED.**
Structurally wrong for a pullback strategy. Pullbacks are defined by
*fading* volume — strong hands don't unload, weak hands exit quietly.
Requiring above-average volume on the entry candle filters out the
genuine pullbacks and only admits flash-crash capitulation bars (where
volume spikes). Lowering the threshold doesn't help: 1.2× → 1.0× still
gives 4 longs because the qualifying RSI<30 readings happen on
*below-average*-volume bars. The filter has to come out, not be tuned.

**2. BTC regime filter (RSI ∈ [35, 65]) — REMOVED.**
Killed 100% of short signals that survived the EMA + RSI > 70 gates.
Original intent was tail-risk protection ("don't long while BTC dumps,
don't short while BTC rips"). In practice it double-counts the per-asset
EMA-trend filter (correlation-redundant) and is symmetric in a market
that wasn't symmetric over the test window. Tail risk is already managed
by the 25% drawdown stop and the per-trade 2-ATR stop — those remain.

**Unchanged:** RSI 30/70 thresholds, EMA alignment, ATR regime gate,
position sizing, all per-trade and portfolio limits, drawdown breakers,
mark-to-market accounting, all v2 fixes from passes 1 and 2.

### Implementation

- `backtest.py` `evaluate_signal`: volume gate dropped, BTC-regime
  comparison dropped from `long_ok` / `short_ok`. `btc_rsi` is still
  computed and recorded on each Trade for diagnostic value, but no
  longer affects entry decisions. `volume_avg_20` removed from the
  required-NaN-check list. Constants `VOLUME_MULTIPLIER`,
  `BTC_REGIME_RSI_LOW`, `BTC_REGIME_RSI_HIGH` kept (used by
  `verify_filter_funnel.py`).
- `backtest.py` `main()` print updated so it doesn't lie about which
  filters are active.
- `rules.json` `entry_rules` has the two lines removed; the removed
  rules are preserved under `entry_rules._removed_in_v2_1` for audit.
  `indicators.volume_avg_20` and `indicators.btc_rsi_14_6h` now marked
  INFORMATIONAL ONLY. `_meta.v2.1_change` documents the rationale.
  Strategy renamed `"6H Trend-Pullback Scanner"` (dropped the "with
  Volume + BTC Regime Filter" suffix).

### Expected effect on the next backtest

Predicted from the counterfactual table:
- ~107 signals → ~35–55 realised trades (after scanner cap, weekly
  portfolio limit, and same-direction filter).
- Trade count should clear §A (≥30) and §C (30–200).
- Both directions should fire, satisfying §D's "both long and short".
- Cost ratio should fall toward the §F band (15–35% of |gross|) as the
  $0.15 fee minimum gets amortized across roughly 10× more trades.

### What this is NOT

This is not "tune until the backtest looks good." Both removals are
motivated by diagnosed structural problems, not by chasing better
numbers. If the rerun still produces a net profit factor < 1.0 in the
30–55 trade range, the strategy genuinely doesn't have edge in this
regime and we don't deploy it. That outcome is fine — the handoff was
explicit that finding out now is better than losing money live.

### Action item for the user

Run `python backtest.py` again locally and upload the new `trades.csv`,
`equity_curve.csv`, and the console summary. The §B fidelity checks and
§A2 mark-to-market gate can now be evaluated against a meaningful
sample.

### v2.2 follow-up — RSI thresholds 30/70 → 35/65

User-driven decision after reviewing the v2.1 outlook. Even with volume
and BTC filters removed, the funnel projected only ~25–30 short signals
across the year because RSI > 70 on 6H is rare in any regime. To bring
the trade count comfortably into the §C 30–200 range, RSI thresholds
were widened:

- `RSI_LONG_MAX`: 30 → **35**
- `RSI_SHORT_MIN`: 70 → **65**

**Justification.** 30/70 on a 6H timeframe captures only the deepest
pullbacks; 35/65 captures normal mid-pullbacks, which is closer to the
strategy's pullback-entry intent. This is a single, hypothesis-motivated
change, not iterative tuning.

**Unchanged in v2.2.** EMA alignment, ATR regime gate, position sizing,
all per-trade and portfolio limits, drawdown breakers, mark-to-market
accounting, all v2.0/v2.1 fixes.

**Expected effect.** Roughly 2× the v2.1 signal count → realised trades
likely 50–100 over 12 months. §A and §C trade-count gates should clear
comfortably. Whether profit factor lands above or below 1.0 is the
actual edge test; that question is for the next backtest output to
answer, not for further parameter tuning.

---

## Second review pass — applied

This pass made 12 changes to `backtest.py`, restructured `rules.json`,
and rewrote large parts of `SAMPLE_RUN.md` and
`PRE_PAPER_TRADE_CHECKLIST.md`. It also **supersedes** part of the
first-pass C1 and m4 notes below; see the "Superseded by second pass"
markers.

### `backtest.py` changes

| # | Change | Function / area |
|---|---|---|
| 1 | Drop incomplete latest candle before indicators | `drop_incomplete_candles`, `main` |
| 2 | Rename `signal_time` → `signal_candle_time` (Coinbase candle START) | `Trade` dataclass |
| 3 | **Mark-to-market equity** drives peak / drawdown / circuit breakers (replaces first-pass C1 fix) | `run_backtest` |
| 4 | End-of-backtest closes add `net_pnl_usd` to `cumulative_pnl` so portfolio total reflects them | `run_backtest` mark-to-market loop |
| 5 | `report_summary` derives final P&L from `sum(t.net_pnl_usd)` so the printed total reconciles to `trades.csv` | `report_summary` |
| 6 | Add `exit_time_partial` to `Trade`; recorded when target 1 fires | `Trade`, `check_exit` |
| 7 | `calculate_trade_funding_drag` charges full notional pre-partial, half post-partial | new helper, `finalize_trade` |
| 8 | `is_high_vol_bar` (range > 2 × ATR) wired into `check_exit` and end-of-backtest close | new helper, `check_exit` |
| 9 | RSI returns 100 / 0 / 50 in extreme/flat regimes instead of NaN | `add_indicators` |
| 10 | `safe_get` adds HTTP 429 backoff, bounded retries, and a `User-Agent` header | new helper, `fetch_candles` |
| 11 | Empty DataFrame guard skips the symbol; aborts if BTC missing (BTC RSI is required) | `main` |
| 12 | Trades sorted by `exit_time` before max-consecutive-losses calculation | `report_summary` |

### `rules.json` changes

- Strategy renamed to `"6H Trend-Pullback Scanner with Volume + BTC Regime Filter"`.
- Symbols split into `live_symbols` and `backtest_symbols`; `watchlist` preserved as alias.
- Added `safety_flags`: `paper_trading_enabled: true`, `live_trading_enabled: false`, `kill_switch_required: true`.
- Added `execution.incomplete_candle_policy`.
- `risk_rules.drawdown_calculation` documents the mark-to-market definition.
- `cost_assumptions.high_vol_slippage_trigger` and `funding_partial_close_policy` documented.
- `blackout_dates` restructured into a per-symbol date-list dict; old free-text examples preserved under `_examples`.
- Drawdown entry rule clarified to `"drawdown_pct < 15 and not in_pause_window"`.
- Blackout-date entry condition added explicitly to long and short rules.

### `SAMPLE_RUN.md` changes

- Caveats block at the top: spot proxy, funding placeholder, incomplete candles dropped, "passing the backtest does not authorize live trading".
- Run-step list updated to mention drop-incomplete and rate-limit backoff.
- `trades.csv` column table: `signal_candle_time` replaces `signal_time`; `exit_time_partial` added.
- `equity_curve.csv` `equity` column defined as mark-to-market (`$500 + closed P&L + unrealized open-position P&L`).

### `PRE_PAPER_TRADE_CHECKLIST.md` changes

- Added gate sections **A1** (incomplete-candle), **A2** (mark-to-market), **A3** (portfolio reconciliation).
- Section **B** reworded for `signal_candle_time` semantics; added **B1** partial-exit accounting checks.
- Section **F** funding warning made explicit; half-notional post-partial called out.
- Added section **I** — paper-trade setup (logging, daily summaries, no parameter changes during paper period).
- Final go/no-go split into "to paper trade" and "to live trade" gate blocks per the review.

### What's NOT done (intentionally deferred)

- Real funding-rate fetcher — still a flat -0.5%/month placeholder.
- `coinbase_exchange.py` adapter — not provided in the handoff upload, so live wiring is still out of scope.
- Per-leg high-vol slippage — currently uses the **exit bar's** high-vol flag for entry, partial, and final legs. A more correct version would track each leg's bar separately. Acceptable simplification given the magnitudes involved.

---

## Files reviewed

- `9dfd8145-backtest_v2.py` → renamed to `backtest.py`, **modified**
- `829691d7-rules_v2.json` → renamed to `rules.json`, **modified** (clarifications only)
- `bb7e939c-STRATEGY_ADA_XRP1.md` → moved to `docs/STRATEGY_ADA_XRP_LEGACY.md`, **unchanged**
- `125947af-CLAUDE_CODE_HANDOFF_PROMPT.md` → moved to `docs/HANDOFF.md`, **unchanged**

## Files referenced by the handoff but not provided

These were listed in `docs/HANDOFF.md` but were not in the upload:

- `coinbase_exchange.py` — exchange adapter
- `MIGRATION_NOTES.md` — BitGet → Coinbase migration notes
- `coinbase-bot.env.example` — env var template

**Impact:** Phase 1–5 of the review are fully covered by the four files
provided. Phase 6 (live-readiness) is partially incomplete — I cannot verify
the adapter's correctness, idempotent order IDs, rate-limit handling,
state reconciliation, or kill-switch script. These remain on the
"required before live" list.

---

## CRITICAL issues — fixed

### C1. Drawdown calculation against zero P&L baseline

**Where:** original `backtest_v2.py:380-385`, `run_backtest`.

**What was wrong:** `equity` started at 0 (cumulative P&L, not account
value). `peak_equity` started at 0 too. The drawdown calc had a guard:

```python
((peak_equity - equity) / max(peak_equity, ACCOUNT_SIZE_USD)) * 100
if peak_equity > 0 else 0
```

Before the first winning trade, `peak_equity == 0`, so the `else 0` branch
fired and reported drawdown as 0% even when the account was deeply
underwater. Concrete failure: an early `-$100` losing streak (real DD =
20% on a $500 account) would report 0% DD, and **the 15% pause and 25%
stop circuit breakers would never fire** during early adverse runs.

**What was fixed (first pass):** Drawdown was moved from
`peak_equity / cumulative_pnl` to `ACCOUNT_SIZE_USD + cumulative_pnl`,
with `peak_account_value` initialized to $500.

**Superseded by second pass (#3):** The drawdown baseline is now
**mark-to-market**: `ACCOUNT_SIZE_USD + cumulative_pnl + unrealized_open_pnl`.
Open losers can no longer hide behind closed winners. See `run_backtest`
and the equity-curve CSV's `equity` column, which now stores the MTM
account value directly. The CSV also retains `cumulative_pnl` (closed
P&L only) for sanity checks.

### C2. Exit priority order mismatch between code and rules

**Where:** original `backtest_v2.py:505-559`, `check_exit`.

**What was wrong:** The strategy doc and `rules.json` list exit priority
as `stop > target_1 > target_2 > time_stop > regime_flip`. The code
checked `regime_flip` inside each side branch and `time_stop` outside,
so the actual order was `stop > target_1 > target_2 > regime_flip >
time_stop`. They were swapped.

In practice, this changes which exit reason gets logged when both fire on
the same bar, and on the 56th bar a regime-flipped trade would close as
"regime_flip" instead of "time_stop". Not a P&L bug but a strategy
fidelity bug.

**What was fixed:** `check_exit` now explicitly orders the checks in the
documented priority. See `backtest.py:374-405`.

### C3. Same-direction filter used a fragile timestamp lookup

**Where:** original `backtest_v2.py:437-440`.

**What was wrong:** To find the strongest existing same-direction signal,
the code did `indexed[s].loc[open_trades[s].signal_time]` and re-computed
RSI strength. This works as long as `signal_time` is exactly an index
key, but it's awkward and silently misbehaves if a row is missing.

**What was fixed:** The trade dataclass already stores `rsi_at_signal`.
The filter now reads that directly via
`signal_strength_from_rsi(t.rsi_at_signal)`. Identical math, no lookup.
See `backtest.py:307-313`.

---

## MAJOR issues — fixed

### M1. Same-bar partial-then-runner is now an explicit, documented limitation

**Where:** `check_exit` in both versions.

**Behavior:** When target 1 fires on bar X, the code records the partial,
moves the stop to breakeven, and returns `None`. On bar X, the runner is
NOT also evaluated against target 2 or the BE stop — that happens on bar
X+1.

**Impact:** A single 6H bar that covers entry → 6 ATR is rare but
possible during high-volatility events. In that case the runner is
recorded as closing on the next bar at next-bar prices, which usually
reverts toward target 1. Net effect: the model slightly understates the
biggest winners. It also means the BE stop can't fire on the same bar as
the partial — usually fine.

**What was changed:** Behavior preserved (it's a reasonable conservative
choice), but it is now documented in the `check_exit` docstring at
`backtest.py:339-353` and called out in `rules.json` under
`execution.exit_priority_note`. Flagging here so you don't get surprised
by it when reviewing trades.csv.

### M2. NaN BTC RSI now treated as neutral

**Where:** `evaluate_signal`, `Trade` construction.

**What was wrong:** The original used `btc_row["rsi_14"] if btc_row is not
None else 50`. If `btc_row` was present but `rsi_14` was NaN (early
warmup before 14 bars of BTC data exist), the value would propagate as
NaN. Comparisons like `NaN < 35` evaluate False, so neither extreme would
trip — effectively neutral, which is the right outcome — but the value
written to `Trade.btc_rsi_at_signal` would be NaN, polluting the CSV.

**What was fixed:** Both `evaluate_signal` (`backtest.py:174-178`) and
the trade construction (`backtest.py:294-296`) check `pd.isna` and fall
back to 50.0.

### M3. Strategy/code consistency check (Phase 4)

> **Historical record from the first pass.** Reflects v2.0 rules and
> first-pass line numbers. The current rules are v2.2 (see "Third
> review pass" at the top of this document for the diff). This table
> is preserved as an audit artifact, not as a current claim.

I walked through every entry rule in `rules.json` against the code:

| Rule | Code reference | Match? |
|---|---|---|
| `close > ema_50_6h` (long) | `backtest.py:185` | ✅ |
| `ema_20_6h > ema_50_6h` (long) | `backtest.py:186` | ✅ |
| `rsi_14_6h < 30` (long) | `backtest.py:187` | ✅ |
| `atr_14_6h < 2.0 * atr_30day_avg` | `backtest.py:166` | ✅ |
| `volume >= 1.2 * volume_avg_20` | `backtest.py:168` | ✅ |
| `btc_rsi >= 35` (long) / `<= 65` (short) | `backtest.py:188, 194` | ✅ |
| no_open_position_on_this_symbol | `backtest.py:282-283` | ✅ |
| open_positions_total < 2 | `backtest.py:268` | ✅ |
| trades_today < 2 | `backtest.py:284-285` | ✅ |
| trades_this_week < 5 | `backtest.py:270` | ✅ |
| drawdown 15% pause / 25% stop | `backtest.py:251-258` | ✅ (after C1 fix) |
| Signal ranking by RSI extremity | `backtest.py:319-320` | ✅ |
| Same-direction stronger-by-5 rule | `backtest.py:303-315` | ✅ |
| Stop @ 2 ATR | `backtest.py:331-336` | ✅ |
| Target 1 @ 3 ATR (50% close, BE stop) | `backtest.py:362-365` | ✅ |
| Target 2 @ 6 ATR | `backtest.py:367-369` | ✅ |
| Time stop @ 56 bars | `backtest.py:391-394` | ✅ |
| Regime flip exit | `backtest.py:397-404` | ✅ |
| Position sizing formula | `backtest.py:140-156` | ✅ |
| Skip conditions (stop_pct, margin) | `backtest.py:144, 153` | ✅ |

All entry conditions and exit branches are present. No silent rule drift.

---

## MINOR issues — addressed or documented

### m1. ASSETS uses spot tickers as proxies for perp prices

**Behavior:** `ASSETS = ["BTC-USD", "ETH-USD", ...]`. `rules.json`
correctly names the live perp universe as `BTC-PERP-INTX` etc.

**Why it's OK:** The Coinbase Exchange public candles endpoint serves
spot only. Spot/perp basis on majors at 6H resolution is small (typically
< 0.1%) and is partly absorbed by the 0.10% slippage assumption.

**What was added:** A header comment in `backtest.py` and a
`_meta.live_universe_naming` / `_meta.backtest_proxy_naming` block in
`rules.json` explaining this. **Action item for you:** when the real
`coinbase_exchange.py` adapter is wired up, expect the live signal RSI
values and ATR values to differ slightly from backtest values for the
same bar — a few percent of slippage variance is normal.

### m2. Slippage is not applied to the entry price used for stop sizing

**Behavior:** `size_position` uses the unslipped `next_bar.open` as the
entry price. Real fills slip. We then deduct slippage from gross P&L.

**Effect:** Stop distance and risk math use a slightly idealized entry.
At $5 risk per trade and 0.10% slippage, the error is bounded at well
under a dollar. Not worth complicating the sizing math.

**What was changed:** Nothing. Documented here.

### m3. Drawdown circuit-breaker fires on the trade that triggers it

**Behavior:** When a trade closes and pushes drawdown over 25%, the
function `return`s — that drawdown event itself counts.

**Effect:** Net P&L from the trade that pushed us to -25% is realized,
then trading stops. This matches the spec.

**What was changed:** Nothing. Verified.

### m4. Equity curve CSV column name

**Before:** `equity` was cumulative P&L (started at 0).

**After (first pass):** `equity` was set to
`ACCOUNT_SIZE_USD + cumulative_pnl` (closed P&L only).

**Superseded by second pass:** `equity` is now the **mark-to-market**
account value: `$500 + closed P&L + unrealized open-position P&L`. The
`cumulative_pnl` column still tracks closed-only P&L so you can compare
the two and verify open-position drift on bars where `open_positions > 0`.

### m5. Volume units

Coinbase Exchange returns volume in base currency (e.g., BTC for BTC-USD).
The strategy compares current volume to the rolling 20-bar average volume,
both in the same units, so the multiplier comparison is dimensionally
correct. Verified.

---

## Edge cases verified (Phase 3)

| Case | Behavior | Where |
|---|---|---|
| Stop AND target hit on same bar | Stop fires first (conservative) | `check_exit` |
| `atr_regime_avg` NaN at warmup | `evaluate_signal` returns None | `backtest.py:159-163` |
| Signal on last bar (no next bar) | Trade skipped | `backtest.py:325-326` |
| BTC bar missing for a timestamp | BTC RSI defaults to 50 | `backtest.py:174-178` |
| Same-bar volatility covering full range | Stop priority preserves conservatism | `check_exit` |
| Backtest ends mid-trade | Mark-to-market at last bar close, reason `backtest_end` | `backtest.py:347-353` |

---

## Cost model verification (Phase 2)

- Entry fee: full notional, applied once. ✅
- Partial close at target 1: half notional fee. ✅
- Final close: half notional fee (after partial) or full notional fee (no partial). ✅
- Stop hit without partial: entry fee + full-notional exit fee. ✅
- $0.15 minimum: enforced in `calculate_fees` via `max(pct_fee, MIN_FEE_USD)`. ✅
- Slippage: applied symmetrically to entry and each exit leg. ✅
- Funding drag: `notional × monthly_rate/30 × days_held`. ✅
- Net P&L = Gross − Fees − Slippage − Funding. ✅
- Report shows gross and net separately, plus each cost line item. ✅

---

## Items NOT changed (and why)

- **Indicator periods, thresholds, leverage caps, risk per trade.** These
  are strategy choices, not bugs. The handoff was explicit: do not tune.
- **Trade frequency.** Same as above. The 1.2× volume + BTC regime + RSI
  30/70 stack is intentionally restrictive.
- **Backtest length (365 days).** Spec requires 12+ months minimum;
  365 satisfies that. You can override `DAYS_BACK` if you want longer.
- **Funding drag model.** Flat -0.5%/month placeholder is acknowledged
  as conservative. Replacing with real funding history requires the
  Coinbase funding endpoint and is out of scope for the backtest.
- **Slippage applied to gross P&L instead of entry price.** See m2 above.
- **Same-bar partial-then-runner conservatism.** See M1 above.

---

## Open questions / your call

1. **Do you want me to wire up funding-rate fetching from a Coinbase
   endpoint** for a more accurate cost model before paper trading? Not
   required by the spec but would tighten the net-P&L estimate.
2. **The handoff lists `coinbase_exchange.py` as a stub.** Do you want
   me to draft a stub that mirrors the interface the live runner would
   need, even though we're not running it? Or wait until you have the
   missing files and review them next pass?
3. **Backtest ran successfully on prior 6H Coinbase data on your end?**
   I did not run the backtest live — you said earlier you'd run it
   yourself. Once you have results, paste the summary output and I can
   sanity-check the trade.csv against the strategy.

---

## Severity legend

- **CRITICAL** — affects correctness of P&L, drawdown, or risk limits. Must fix before paper trading.
- **MAJOR** — strategy fidelity issue or behavior that diverges from the doc.
- **MINOR** — documentation, units, or convenience. Fix when convenient.
