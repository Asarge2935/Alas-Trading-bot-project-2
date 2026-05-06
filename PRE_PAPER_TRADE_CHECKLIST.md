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
      `365 days × 4 bars/day = 1460` rows (minus warmup).
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
- [ ] For long trades: signal candle close > `ema_50`,
      `ema_20 > ema_50`, and `rsi_at_signal < 30`.
- [ ] For short trades: signal candle close < `ema_50`,
      `ema_20 < ema_50`, and `rsi_at_signal > 70`.
- [ ] `stop_price` is exactly `2 × atr_at_signal` from `entry_price` in
      the correct direction.
- [ ] `target_1_price` is exactly `3 × atr_at_signal` from entry.
- [ ] `target_2_price` is exactly `6 × atr_at_signal` from entry.
- [ ] `notional_usd ≤ 100` and `margin_usd ≤ 50`.
- [ ] `r_multiple` is sensible: stop-out should be ≈ −1.0R after costs;
      full target_2 hit should be ≈ +4.5R (avg of +1.5R partial and
      +3R runner, minus a half-R or so of costs).

## B1. Partial-exit accounting checks

- [ ] If `exit_price_partial` is populated, fees and slippage include
      entry + partial exit + final exit (three legs), not just two.
- [ ] If `exit_price_partial` is populated, `exit_time_partial` is also
      populated and is between `entry_time` and `exit_time`.
- [ ] Stop moves to breakeven only after target 1 partial fill — confirm
      by sampling a stopped-out post-partial trade and checking its
      exit price equals `entry_price` (within rounding).
- [ ] Runner is not closed on the same candle as target 1, even if
      target 2 was also reached inside that candle. The runner closes
      on the NEXT bar at earliest.

## C. Profit factor and risk numbers (the handoff's deployment gates)

From the printed summary or by computing from trades.csv:

- [ ] **Net profit factor (ALL bucket) ≥ 1.5.** If 1.0–1.5, paper trade
      is marginal — make an explicit informed-decision call. Below 1.0
      means the strategy loses money after costs and **should not be
      deployed.**
- [ ] **Max drawdown < 25%.** If it exceeds 25%, the drawdown_stop
      circuit breaker should have fired in the run.
- [ ] **Max consecutive losses < 8.** If 8+, the strategy has fat-tail
      losing streaks; expect months where you stare at the bot doing
      nothing right.
- [ ] **Avg R-multiple > +0.2.** Each trade should have positive
      expectancy on average.
- [ ] **Trade count between 30 and 200.** Lower means signals are too
      rare to be statistically meaningful at this account size; higher
      means the filters are too loose for the timeframe.

## D. Per-asset sanity (don't deploy a strategy that only works on one coin)

- [ ] At least 4 of 6 assets have ≥ 5 trades each. If only 1 or 2 assets
      generated signals, the strategy is curve-fit to those assets'
      regimes.
- [ ] No single asset accounts for > 50% of total P&L. If one asset
      carries the whole result, you're not running a 6-asset strategy,
      you're running a 1-asset strategy with 5 distractions.
- [ ] Both long and short trades fired. If only longs fired, you tested
      a bull market; the strategy hasn't been stress-tested to the
      downside.

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
      is barely viable.
- [ ] No single trade's combined fees exceed $1.50 (10% of a $5 risk
      unit). The $0.15 fee minimum applies — small notionals get hit
      hard. Trades sized down by the cap should be the exception, not
      the norm.
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

- [ ] `rules.json` and `backtest.py` constants match. Spot-check
      `RISK_PER_TRADE_USD`, `MAX_LEVERAGE`, `TIME_STOP_BARS`,
      `DRAWDOWN_PAUSE_PCT`, `DRAWDOWN_STOP_PCT`.
- [ ] `rules.json` `safety_flags`: `paper_trading_enabled: true`,
      `live_trading_enabled: false`, `kill_switch_required: true`.
- [ ] `docs/STRATEGY_ADA_XRP_LEGACY.md` has a clear "superseded" note at
      the top so future-you doesn't accidentally implement it.

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
- [ ] Max open positions (2) is enforced.
- [ ] Daily (per asset) and weekly (portfolio) trade limits are enforced.
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
- [ ] First live phase uses $50, not the full $500.
- [ ] Bot capital is isolated from long-term spot holdings (separate
      sub-account or wallet).

If any line above is "no", do not proceed. Return to the strategy and
fix the issue. The handoff was explicit — finding out a strategy
doesn't work is a successful outcome at this stage.
