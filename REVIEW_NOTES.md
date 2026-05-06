# Review Notes — Alas Trading Bot Backtester

Date: 2026-05-06
Branch: `claude/new-session-ysAXb`
Reviewer pass: code review and finalization before paper trading.

This document lists every issue found during the Phase 1–6 review of the
materials provided in the handoff, the severity of each issue, and what
was changed (or explicitly left alone). It is intended to be read alongside
`backtest.py`, `rules.json`, and `docs/HANDOFF.md`.

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

**What was fixed:** Drawdown is now measured against
`ACCOUNT_SIZE_USD + cumulative_pnl`. `peak_account_value` is initialized
to `ACCOUNT_SIZE_USD` ($500). This makes drawdown equivalent to standard
peak-to-trough on actual account equity. See `backtest.py:227-233` and the
post-trade update at `backtest.py:248-258`.

The equity curve CSV now exposes both `equity` (account value in dollars)
and `cumulative_pnl` so you can sanity-check this manually.

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

**After:** `equity` is account value (`ACCOUNT_SIZE_USD + cumulative_pnl`,
starts at 500.00). Added a new `cumulative_pnl` column for the prior
meaning. Reporting in `report_summary` updated to print "Final equity"
and "Net P&L" separately so the two are not confused.

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
