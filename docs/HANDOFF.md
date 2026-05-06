# Claude Code Handoff: Crypto Trading Bot Review & Finalization

## Context

I am building a personal crypto trading bot for a $500 sandbox account on Coinbase Advanced (US, CFTC-regulated perpetual futures). I have been designing this through extended conversations with Claude (chat) and now need a thorough code review and finalization pass before any paper trading begins.

**Critical context about me:**
- I am NOT a professional trader. This is a learning project with real but limited capital.
- I have a separate long-term spot portfolio (XRP, ADA, AVAX, HYPE, PYTH, etc.) that this bot must NEVER touch.
- I am an electrical engineer by trade, comfortable with code but not a software engineer by profession.
- I have repeatedly pushed for higher trade frequency during design and been pushed back on for fee-math reasons. The current design is intentionally LOW frequency (1-3 trades/week target). Do not "help" by increasing frequency.

**My goal for this handoff:**
- Review all code and documents for correctness and consistency
- Fix any remaining bugs from the review checklist
- Verify the strategy as documented matches the code as implemented
- Produce a final set of files I can confidently paper-trade with for 30 days
- Flag anything that looks risky or wrong before I run it with real data

**My goal is NOT:**
- Adding new features
- Adding new indicators
- Increasing trade frequency
- Adding new assets beyond the 6 already chosen
- Going live faster

---

## Files Included

You should have these files from the chat handoff. If any are missing, ask before proceeding:

1. `backtest_v2.py` — The current backtester (Python, 6H timeframe, scanner mode, 6 assets)
2. `rules_v2.json` — Strategy configuration in machine-readable form
3. `coinbase_exchange.py` — Exchange adapter for Coinbase Advanced perp API
4. `STRATEGY_ADA_XRP.md` — Original 2-asset strategy doc (now superseded but kept for reference)
5. `MIGRATION_NOTES.md` — Notes on BitGet → Coinbase migration
6. `coinbase-bot.env.example` — Environment variable template
7. This prompt file

---

## Strategy Summary (Source of Truth)

**Mode:** Multi-asset scanner across 6 assets: BTC, ETH, SOL, XRP, ADA, DOT (perpetual futures on Coinbase)

**Timeframe:** 6-hour bars (granularity 21600 — natively supported by Coinbase Exchange API)

**Indicators:**
- EMA(50) on 6H — slow trend filter, defines regime
- EMA(20) on 6H — fast momentum confirmation
- RSI(14) on 6H — entry trigger (long < 30, short > 70)
- ATR(14) on 6H — volatility gate and stop sizing
- Volume(20) on 6H — entry candle must show ≥1.2× 20-bar average
- BTC RSI(14) on 6H — inter-market regime filter

**Entry Rules (LONG):**
1. close > EMA(50) AND EMA(20) > EMA(50) — trend regime + momentum aligned
2. RSI(14) < 30 — oversold pullback in uptrend
3. ATR(14) < 2.0 × 30-day avg ATR — volatility regime check
4. Volume ≥ 1.2 × volume_avg_20 — confirmation
5. BTC RSI(14) ≥ 35 — don't take longs while BTC is dumping
6. No existing open position on this symbol
7. Open positions across portfolio < 2
8. Trades today for this symbol < 2
9. Trades this week portfolio-wide < 5
10. Drawdown < 15% (or in pause window)

**Entry Rules (SHORT):** Mirror of long — price < EMA(50), EMA(20) < EMA(50), RSI > 70, BTC RSI ≤ 65, etc.

**Signal Selection:** When multiple assets fire on the same bar, take the one with most extreme RSI (furthest from 50). If we already hold a same-direction position, the new signal must be ≥5 RSI points more extreme to qualify.

**Entry Execution:** Signal forms on bar N close. Order fills at bar N+1 open. (No same-bar fills.)

**Exit Rules (in priority order):**
1. Stop loss hit (2 ATR from entry) → market close immediately
2. Target 1 hit (3 ATR from entry) → close 50%, move stop to breakeven
3. Target 2 hit (6 ATR from entry) → close remainder
4. Time stop (56 bars = 14 days from entry) → market close
5. Regime flip (close crosses to wrong side of EMA(50) on 6H) → market close

**Position Sizing:**
- Risk per trade: $5 (1% of $500)
- Stop distance defines 1R
- Formula: notional_usd = risk_usd / stop_distance_pct, then cap at max_notional, margin = notional / leverage
- Max margin per trade: $50
- Max leverage: 2x
- Max notional per trade: $100
- Skip if stop distance < 0.5% (too tight) or required margin < $5 (too small)

**Risk Limits (hard rules):**
- Max 2 open positions across all 6 assets
- Max 2 trades per asset per day
- Max 5 trades portfolio-wide per week
- Drawdown 15% → pause that bucket for 7 days
- Drawdown 25% → stop entirely

**Cost Model:**
- Coinbase taker fee: 0.03% per side
- $0.15 minimum fee per contract per side
- Slippage: 0.10% normal, 0.20% high-volatility
- Funding drag: -0.5%/month flat placeholder
- Apply fees, slippage, and funding to compute net P&L; report gross and net separately

**Exit fee modeling (subtle — verify carefully):**
- Entry: full notional fees + slippage
- Partial close at target 1: half notional fees + slippage
- Final close: half notional fees + slippage
- Trades that hit stop without partial: entry + full-notional exit fees
- Funding drag: based on bars held × notional

---

## REVIEW CHECKLIST — Work Through These In Order

### Phase 1: Code Correctness

Verify these were actually fixed (this checklist came from a prior code review and I want confirmation each item is truly addressed in `backtest_v2.py`):

- [ ] **Granularity is 21600** (6H, natively supported by Coinbase Exchange API). Confirm the API endpoint accepts this and returns valid candles.
- [ ] **Position management:** Open trades tracked per symbol. A symbol with an open position cannot fire a new entry. Verify by tracing through the code.
- [ ] **Next-bar entry:** Signal forms at bar N close, fill price is bar N+1 open. Confirm no same-bar fills exist anywhere.
- [ ] **Exit logic matches strategy:** Partial close at 3 ATR (50%), stop moves to breakeven, runner closes at 6 ATR. Trace a long trade through `check_exit` and `finalize_trade` to confirm.
- [ ] **Daily trade limit per asset:** 2 max, enforced and reset at day boundary
- [ ] **Weekly portfolio limit:** 5 max, rolling 7-day window
- [ ] **Max open positions:** 2 across portfolio, not 2 per asset
- [ ] **Drawdown circuit breakers:** 15% pause for 7 days, 25% stop entirely. Verify both trigger correctly.
- [ ] **Same-direction filter:** New signal needs RSI 5+ more extreme than existing same-direction position to qualify
- [ ] **Position sizing math:** notional = risk / stop_pct, then margin = notional / leverage. Confirm the formula is NOT incorrectly multiplying notional by leverage.
- [ ] **Skip-trade conditions:** Skip if stop_pct < 0.5% or margin < $5
- [ ] **R-multiples calculated correctly:** From actual stop distance, not hardcoded values

### Phase 2: Cost Model Verification

- [ ] Fees applied on every leg: entry, partial exit (if any), final exit
- [ ] $0.15 minimum is enforced — verify on a small trade where pct fee would be less
- [ ] Slippage applied to entry and each exit leg
- [ ] Funding drag based on bars held × notional, scaled monthly → daily correctly
- [ ] Net P&L = Gross P&L − Fees − Slippage − Funding (verify in `finalize_trade`)
- [ ] Report shows gross and net separately

### Phase 3: Edge Cases

Identify and handle (or document) these edge cases:
- [ ] What happens if a stop AND target are both touched in the same bar? (Conservative assumption: stop hits first.)
- [ ] What happens if `atr_regime_avg` is NaN at start of backtest? (Should skip, not crash.)
- [ ] What happens if next bar after signal doesn't exist (last bar of backtest)? (Should skip the trade.)
- [ ] What happens if BTC data is missing for a given timestamp but other assets have it? (Should treat BTC RSI as neutral 50, not crash.)
- [ ] What happens during a trade if 6H bar has high > target_2 AND low < stop on the same bar (volatile bar)? (Conservative: stop hits first if not yet at target; otherwise target hits.)
- [ ] What happens if backtest ends mid-trade? (Should mark to market at last bar close, label exit reason as "backtest_end".)

### Phase 4: Strategy ↔ Code Consistency

For each rule in `rules_v2.json`, verify the same rule exists and is enforced in `backtest_v2.py`. Specifically:

- [ ] All 10 entry conditions for longs are checked
- [ ] All 10 entry conditions for shorts are checked
- [ ] Signal ranking logic matches the doc
- [ ] Same-direction stronger-signal rule is implemented
- [ ] Exit priority order matches doc (stop > target_1 > target_2 > time > regime_flip)
- [ ] Position sizing formula matches doc
- [ ] Drawdown thresholds (15%, 25%) match doc
- [ ] Time stop is exactly 56 bars (14 days × 4 bars/day at 6H)

If any rule is in the doc but not the code, or vice versa, **flag and ask before changing.**

### Phase 5: Reporting & Output

- [ ] Trade-by-trade CSV is exported with all required fields:
  symbol, side, signal_time, entry_time, entry_price, stop, target_1, target_2,
  exit_time, exit_price_partial, exit_price_final, exit_reason, bars_held,
  gross_pnl, fees, slippage, funding, net_pnl, r_multiple
- [ ] Equity curve CSV has time, equity, open_positions, drawdown_pct
- [ ] Summary report shows: per-asset, per-side per-asset, all-longs, all-shorts, all-portfolio
- [ ] Summary metrics: trade count, win rate, profit factor, avg R, gross P&L, fees, slippage, funding, net P&L, max drawdown, max consecutive losses
- [ ] Interpretation guide is included so the user can read results without me

### Phase 6: Live-Readiness (Out of Scope For This Pass — But Note)

Do NOT implement, but document that these are needed before live trading:
- [ ] Coinbase Advanced API integration via `coinbase_exchange.py` (currently a stub adapter)
- [ ] Idempotent order IDs to prevent duplicate orders on retry
- [ ] Rate-limit handling and exponential backoff
- [ ] State reconciliation on bot restart (compare local position state to exchange)
- [ ] Kill-switch script (cancel all orders, close all positions, disable entries)
- [ ] Alerting on entry/stop-move/partial/drawdown/error events
- [ ] Funding rate fetcher (replace flat -0.5%/month with real funding lookup)
- [ ] Logging to persistent storage (not just stdout)
- [ ] Separate Coinbase sub-account for bot capital, isolated from spot holdings

---

## Specific Things To Look For

These are the patterns I'd most expect to find issues with:

1. **Off-by-one in bar indexing** — make sure "next bar after signal" doesn't accidentally use the signal bar itself
2. **Timezone bugs** — all timestamps should be UTC; no naive datetimes
3. **NaN handling** — early bars during indicator warmup should not generate signals
4. **Floating point comparisons** — using `==` on floats is buggy; should use tolerance or `>=` / `<=`
5. **Lookahead bias** — any place where the code "knows" about a future bar's data when evaluating the current bar
6. **Survivorship in the data** — the public Coinbase API may not include delisted pairs, but for these 6 majors this should be fine
7. **Volume validity** — Coinbase reports volume in base currency; verify that's what the strategy intends

---

## What I Want From You

After your review, produce:

1. **`REVIEW_NOTES.md`** — Bullet-point summary of every issue found, severity (critical/major/minor), and what was changed
2. **Fixed `backtest_v2.py`** — All critical and major issues addressed; minor issues may remain but documented
3. **Updated `rules_v2.json`** if any rules needed clarification
4. **`SAMPLE_RUN.md`** — If you can run the backtester (with the user's permission to install pandas/numpy/requests and hit Coinbase's public API), include a sample of the output. Note that the public API may have rate limits.
5. **`PRE_PAPER_TRADE_CHECKLIST.md`** — A short list of things I should verify in the output before declaring the strategy ready for paper trading. Specific go/no-go criteria.

## What NOT To Do

- Do not add new indicators
- Do not add new assets
- Do not increase trade frequency
- Do not change timeframes
- Do not "improve" the strategy by tweaking thresholds — surface concerns instead
- Do not start the live bot
- Do not run anything that places real orders
- Do not modify `coinbase_exchange.py` to add features beyond what the backtest needs
- Do not assume my long-term spot portfolio assets are "available" to the bot — the bot's capital is strictly the $500 sandbox
- Do not skip ahead to live deployment topics — those are explicitly out of scope for this review

## Style/Tone

- Be direct about problems. I want criticism, not validation.
- Show me the line numbers and the specific code when flagging issues.
- If you find something that looks intentional but suspicious, ask before changing.
- If you disagree with a strategy choice, say so once with reasoning, then respect my decision and move on.
- Treat me as someone who can handle being told they're wrong about something — that's more useful than being told everything is great.

---

## Final Decision Gate (My Criteria)

I will proceed to paper trading only when:
- All critical bugs are fixed
- Backtest runs end-to-end without errors on real Coinbase data
- 12-month backtest produces a net profit factor > 1.5 OR I make an informed decision to proceed with worse numbers because I want the learning experience
- Max drawdown in backtest < 25%
- Max consecutive losses < 8
- Trade count is reasonable (I'd expect 30-150 trades over 12 months given the filters)
- I personally review at least 10 individual trades from the CSV and confirm they match the strategy I designed
- The kill-switch and pause logic have been tested by simulating a 30% drawdown event

If the backtest shows the strategy doesn't work, that's a successful outcome — better to find out now than to lose money live.

Begin with Phase 1 of the review checklist. Ask me anything you need before starting.
