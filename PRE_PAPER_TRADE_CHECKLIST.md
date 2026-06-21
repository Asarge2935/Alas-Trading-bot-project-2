# Pre-Paper-Trade Checklist — Alas Trading Bot

A go/no-go list to run AFTER your 12-month backtest completes and BEFORE
you start a 30-day paper trade. Every item is a hard gate. If any
critical line is "no", do not proceed — fix the issue or rebuild.

---

## A. Backtest output exists and is non-trivial

- [ ] `backtest_output/trades.csv` exists and has at least 30 rows.
      Fewer than 30 trades over 12 months means signals are too rare for
      a $500 account to ever turn a meaningful return after fees.
- [ ] `backtest_output/equity_curve.csv` exists and has roughly
      `days × 4 bars/day` rows (minus warmup) — ~5,800 for the ~4-year
      default.
- [ ] No exceptions raised during the run.
- [ ] No `ZeroDivisionError`, `KeyError`, or `IndexError` warnings on
      stderr.

## A1. Incomplete candle gate

- [ ] Latest incomplete 6H candle is excluded before indicators and
      signals are evaluated. The run output prints `(dropped N
      incomplete)` when it filters anything out.

## A2. Mark-to-market gate

- [ ] Equity curve includes unrealized P&L from open positions, not only
      closed trade P&L. Sample a row where `open_positions > 0` and
      confirm `equity != $500 + cumulative_pnl`.
- [ ] Max drawdown is calculated from mark-to-market equity (not from
      closed P&L alone).

## A3. Portfolio reconciliation gate

- [ ] Portfolio Net P&L printed in the summary equals
      `sum(net_pnl_usd)` from trades.csv.
- [ ] Final equity equals `$500 + sum(net_pnl_usd)`. (The summary
      reconciles to trades.csv directly — if it doesn't, the report
      logic and trade list have diverged and one of them is wrong.)

## B. Strategy fidelity (manually inspect 10 random trades from trades.csv)

For each of 10 random trades, confirm:

- [ ] `signal_candle_time` is a closed 6H bar; `entry_time` is the
      **next** 6H bar's open. They should be exactly 6 hours apart.
- [ ] `entry_price` matches the next bar's open price for that symbol.
- [ ] `regime_state` is `risk_on` for longs and `risk_off` for shorts —
      never `neutral`.
- [ ] The traded `symbol` is the strongest 20-day return of BTC/ETH/SOL
      for longs (weakest for shorts) as of the signal — `rs_return` of
      the chosen asset should be the most extreme of the three.
- [ ] `stop_price` is the breakout candle's extreme OR 1.5×ATR from
      entry — whichever is **wider** (farther from entry).
- [ ] `target_1_price` is exactly +1.5R from entry (R = entry-to-stop
      distance), in the correct direction.
- [ ] `notional_usd` ≈ `(0.01 × equity) / stop_distance_pct`, capped at
      `equity × 2` (leverage cap).
- [ ] `exit_reason` is one of: `stop_hit`, `breakeven_stop`,
      `trail_stop`, `time_stop`, `backtest_end`. (No `regime_flip` or
      `target_2` — those were v2.)
- [ ] `r_multiple` is sensible: a clean stop-out ≈ −1.0R after costs; a
      trade that took the +1.5R partial and trailed should be positive.

## B1. Partial-exit accounting checks

- [ ] If `exit_price_partial` is populated, fees and slippage include
      entry + partial exit + final exit (three legs), not just two.
- [ ] If `exit_price_partial` is populated, `exit_time_partial` is also
      populated and is between `entry_time` and `exit_time`.
- [ ] Stop moves to breakeven only after target 1 partial fill — confirm
      by sampling a stopped-out post-partial trade and checking its
      exit price equals `entry_price` (within rounding).
- [ ] The trailing stop does not tighten and fire on the same candle as
      the +1.5R partial — the runner's trailing stop ratchets starting
      the NEXT bar (conservative same-bar policy).

## C. Validation gates (the backtest prints these — STRATEGY_SPEC.md §9)

`backtest.py` prints a "VALIDATION GATES" block with a PROMOTE/REJECTED
verdict. Confirm each:

- [ ] **Sample ≥ 30 trades.** Fewer means the numbers aren't meaningful.
      With strict regime+RS+breakout gating this is the gate most likely
      to fail on a short window — use the full ~4-year default.
- [ ] **Positive expectancy:** net P&L > 0 AND avg R > 0.
- [ ] **Net profit factor (ALL bucket) ≥ 1.3.**
- [ ] **Beats equal-weight BTC/ETH/SOL HODL** on daily Sharpe.
- [ ] **Max drawdown < 25%** (mark-to-market). If it exceeds 25%, the
      drawdown_stop circuit breaker should have fired in the run.
- [ ] **No single trade > 25% of net P&L** (no one lucky trade carrying
      the result).
- [ ] **Out-of-sample PF ≥ 0.75× in-sample PF** (70/30 time split).
- [ ] **Max consecutive losses** is not alarming (8+ means fat-tail
      losing streaks; expect long do-nothing stretches).

## D. Per-asset / direction sanity (don't deploy a one-coin, one-regime fluke)

- [ ] More than one of BTC/ETH/SOL generated trades. If only one asset
      ever traded, the relative-strength selection is degenerate or the
      result is curve-fit to that asset.
- [ ] No single asset accounts for > 50% of total P&L.
- [ ] Both long and short trades fired. If only longs fired, you tested
      a bull market; the short side (risk-off regime) hasn't been
      stress-tested. The 20-bar window almost certainly lacks enough
      risk-off stretches if shorts are absent.

## E. Drawdown circuit breakers actually trip

Force a synthetic test once before paper trading:

- [ ] Temporarily drop `DRAWDOWN_STOP_PCT` to 5% and re-run a few months
      of data. The run should print `DRAWDOWN STOP HIT` and exit early.
- [ ] Temporarily drop `DRAWDOWN_PAUSE_PCT` to 3% and re-run. The run
      should print `DRAWDOWN PAUSE` and skip entries during the
      `DRAWDOWN_PAUSE_DAYS` window.
- [ ] Restore both constants to 25% and 15% before paper trading.

## F. Cost model is not eating the strategy alive

From the summary's per-bucket Fees / Slippage / Funding lines:

- [ ] Fees + slippage + funding combined should be 15–35% of gross P&L
      magnitude. Above ~50% means costs are dominating and the strategy
      is barely viable. (Taker fee is modeled at 0.10% per side.)
- [ ] No single trade's combined fees are a large fraction of its 1%
      risk unit. The $0.15 fee minimum applies — small notionals get hit
      hard, which is exactly why the live integer-contract sizing matters.
- [ ] **Funding warning understood:** the funding drag is a flat
      placeholder, not real historical funding-rate data. Real funding
      lookups must be wired in before live trading. After a partial
      close at target 1, funding on the runner is computed against the
      half-notional, not the full original notional.

## G. Equity curve looks like a curve, not a cliff

Open `equity_curve.csv` in a spreadsheet, plot `equity` vs `time`:

- [ ] No vertical drop greater than 10% in a single trade.
- [ ] The curve doesn't make all its money in the first 60 days and
      then flatline. That's a regime-change tell.
- [ ] The curve doesn't make all its money in the last 60 days either —
      that's a different kind of regime tell.
- [ ] Drawdown column never exceeds the printed Max DD, and never goes
      negative.

## H. Documentation is in sync

- [ ] `rules.json` (v3) and `backtest.py` constants match. Spot-check
      `RISK_PCT`, `MAX_LEVERAGE`, `TIME_STOP_BARS`, `MAX_OPEN_POSITIONS`,
      `MAX_DAILY_LOSS_PCT`, `MAX_WEEKLY_LOSS_PCT`, `DRAWDOWN_PAUSE_PCT`,
      `DRAWDOWN_STOP_PCT`, and the regime/breakout parameters.
- [ ] `backtest.py` matches `docs/STRATEGY_SPEC.md` (the canonical spec).
- [ ] `rules.json` `safety_flags`: `paper_trading_enabled: true`,
      `live_trading_enabled: false`, `kill_switch_required: true`.
- [ ] Legacy docs (`docs/STRATEGY_1_*`, `STRATEGY_2_*`, `*_LEGACY.md`)
      are clearly marked superseded so future-you doesn't reimplement them.

## I. Paper-trade setup

- [ ] Paper trader uses the same constants as the backtest (no overrides).
- [ ] Paper trader logs every skipped signal and the reason it was skipped.
- [ ] Paper trader logs every simulated entry, partial exit, stop move,
      and final exit.
- [ ] Paper trader writes daily summaries (cumulative P&L, open positions,
      per-asset stats, errors).
- [ ] No parameter changes are allowed during the 30-day paper period.
- [ ] Live trading is disabled by default
      (`safety_flags.live_trading_enabled = false`).

## J. Live-readiness items that are explicitly NOT in scope yet

These are required before LIVE capital, not before paper trading.
Acknowledged here so you don't forget:

- [ ] `coinbase_exchange.py` adapter wired up and unit-tested
- [ ] Idempotent client order IDs to prevent duplicate orders on retry
- [ ] Rate-limit handling and exponential backoff (the backtester's
      `safe_get` is a starting reference)
- [ ] State reconciliation on bot restart (compare local position state
      to exchange)
- [ ] Kill-switch script (cancel all orders, close all positions,
      disable entries)
- [ ] Alerting on entry / partial / stop-move / drawdown / error events
- [ ] Real funding rate fetcher (replace flat -0.5%/month placeholder)
- [ ] Persistent logging (not just stdout)
- [ ] Separate Coinbase sub-account for bot capital, isolated from spot

---

## Final go/no-go gates

### Do not proceed to paper trading unless:

- [ ] Backtest runs with no errors.
- [ ] Latest incomplete candle is excluded.
- [ ] Portfolio P&L reconciles to trades.csv (`sum(net_pnl_usd)`).
- [ ] Equity curve includes unrealized open-position P&L.
- [ ] Max drawdown uses mark-to-market equity.
- [ ] Partial exits, fees, slippage, and funding are accounted for
      correctly (3 fee/slippage legs when partial fired; funding split
      across pre- and post-partial windows at full vs half notional).
- [ ] High-vol slippage is implemented (fires when bar range > 2×ATR).
- [ ] RSI does not produce false NaNs in strong trends.
- [ ] No duplicate same-symbol positions occur.
- [ ] Max open positions (1, Phase 1) is enforced.
- [ ] Per-asset/day (1) trade limit and daily-loss (2%) / weekly-loss
      (5%) halts are enforced.
- [ ] Drawdown pause (15%) and drawdown stop (25%) are tested.
- [ ] Backtest result is understood to qualify the strategy for paper
      trading only — not for live trading.

### Do not proceed to live trading unless:

- [ ] Paper trading runs for at least 30 days.
- [ ] No parameters are changed during the paper-trade period.
- [ ] Paper results are consistent with backtest expectations
      (profit factor and drawdown within roughly the same bands).
- [ ] Kill switch exists and is tested (cancels orders, closes
      positions, disables further entries).
- [ ] Live trading remains disabled by default in `rules.json`.
- [ ] The account is funded enough for ≥ 1 nano contract with the 1%
      risk rule intact before any live entry (Phase-1 sizing).
- [ ] Bot capital is isolated from long-term spot holdings (separate
      sub-account or wallet).

If any line above is "no", do not proceed. Return to the strategy and
fix the issue. The handoff was explicit — finding out a strategy
doesn't work is a successful outcome at this stage.
