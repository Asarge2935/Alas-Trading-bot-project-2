# Strategy Spec (Canonical) — BTC-Led Regime + Relative Strength + Breakout

**Date:** 2026-05-24
**Status:** authoritative specification. Supersedes
`STRATEGY_1_REGIME_RS_SPEC.md` and `STRATEGY_2_BTC_DIRECTIONAL_SPEC.md`
(kept for history). No code built against this yet — this doc is the
single source of truth the implementation and gates will follow.
**Provenance:** consolidates the user's four updates (2026-05-24):
the strategy plan, the edge theory, the frequency/sizing philosophy,
and the portfolio/correlation rules. Conflicts in the source material
are reconciled below and flagged where overridden.

---

## 0. Reconciled decisions (read first)

Two contradictions in the source material, resolved:

1. **Risk per trade: 1%, NOT 35%.** Upload 1 said both "Risk only:
   35% of bot capital per trade" and "Risk ≤ 1%." 35% is account-
   destroying (two stops ≈ −70% in a day) and contradicts the plan's
   own goal. **Overridden to 1% per trade.** Anything above ~2%
   requires explicit re-confirmation.
2. **Trade caps:** Upload 1 had "max 6/day" and "max 5/week"
   (inconsistent). Superseded by the user's final frequency rule:
   **maximum 1 contract per asset per day, no minimum**, plus the
   loss-based halts in §7.

Core philosophy (user, verbatim): *"The bot is not paid to trade. The
bot is paid to wait."* No forced trades. If no edge appears, the
correct result is **0 trades**.

Operating frame (user): **scan everything, trade selectively, size
intelligently, stop when risk limits are reached.** High scan
frequency, low trade frequency, strict ranking, risk capped across
correlated trades. The bot evaluates every asset / side / timeframe
every candle, but a signal only becomes a trade if it is A+ AND
portfolio risk limits still allow it (see §4a and §7).

---

## 1. Hypothesis (the edge to prove)

> When BTC is above its 50-day EMA and that EMA is rising (risk-on),
> **long** the strongest of BTC/ETH/SOL after a 20-bar breakout has
> positive expectancy after costs.
>
> When BTC is below its 50-day EMA and that EMA is falling (risk-off),
> **short** the weakest of BTC/ETH/SOL after a 20-bar breakdown has
> positive expectancy after costs.
>
> In chop (neutral regime), there is no edge — do not trade.

Three stacked edges: **regime** (don't fight BTC) + **relative
strength** (trade the best instrument) + **volatility/breakout
confirmation** (enter as movement expands). Falsifiable; rejected if
expectancy after fees/slippage/funding is ≤ 0 over ≥30 trades.

---

## 2. Universe & instrument

- Assets: **BTC, ETH, SOL** only.
- Instrument: **Coinbase nano perpetual futures** (BTC-PERP etc.),
  long and short. Confirmed available, ~0.10% taker, ~$765 notional /
  contract, ~$188 margin at ~4x. See `VENUE_AUDIT_PERP_US.md`.
- Regime timeframe: **1D**. Entry timeframe: **4H** (preferred) or
  **6H** if 4H unavailable. (4H derived via `data.resample_ohlcv`
  from 1H; 6H is native.)

---

## 3. Regime filter (BTC, 1D)

Note: source specifies **EMA** (current `regime.py` uses SMA — will
switch to EMA50 for this spec).

- **Risk-on** iff ALL:
  - BTC close > BTC EMA(50)
  - BTC EMA(50) rising (today > N days ago)
  - BTC is NOT more than 2% below its EMA(20)
- **Risk-off** iff:
  - BTC close < BTC EMA(50) AND BTC EMA(50) falling
- **Neutral (NO TRADE)** iff:
  - BTC within ±2% of EMA(50), OR EMA(50) flat

Neutral = stand down. This single rule is expected to remove a large
share of losing trades.

---

## 4. Relative strength selection

- Rank BTC, ETH, SOL by **20-day return** (point-in-time).
- Risk-on → candidate is the **strongest**.
- Risk-off → candidate is the **weakest**.
- Only one candidate per side at a time.

---

## 4a. Ranking when multiple A+ setups fire

The bot scans all assets/sides every candle and may find more than one
qualifying setup. It does **not** blindly take them all. Rank
qualifying setups and prefer the best, subject to the §7 portfolio
caps. Ranking criteria (in priority order):

1. BTC regime alignment (strength of the regime signal)
2. Relative strength (long) / weakness (short) — distance from the pack
3. Breakout / flush quality (clean expansion vs marginal poke)
4. Volume confirmation
5. Distance to invalidation (tighter, well-defined stop preferred)
6. Spread / fee impact (lower is better)
7. Reward-to-risk potential

**Correlation rule (critical):** BTC/ETH/SOL are highly correlated, so
multiple same-direction signals are largely **one** crypto-direction
bet, not independent trades. Therefore risk is capped *across*
correlated trades, not per-trade in isolation (see §7).

---

## 5. Entry (4H/6H)

**Long** (risk-on only), all required:
- Asset close > its 4H/6H EMA(50)
- Asset breaks above prior **20-bar high**
- Close in **top 25%** of the candle's range (strong close)
- Volume above recent average (if volume available)
- BTC also above its 4H/6H EMA(50)
- Asset is the strongest by 20-day return (§4)

**Short** (risk-off only): mirror image — below EMA(50), break below
prior 20-bar low, close in **bottom 25%** of range, BTC below its
4H/6H EMA(50), asset is the weakest by 20-day return.

Decision on bar close; fill next bar (no look-ahead).

---

## 6. Exits

- **Stop (long):** below the breakout candle's low, OR 1.5×ATR below
  entry — whichever is **wider** (safer).
- **Stop (short):** above the breakdown candle's high, OR 1.5×ATR
  above entry — whichever is wider.
- **R-based targets:** take **50% off at +1.5R**, move stop to
  **breakeven**, then **trail the remainder at 2.5×ATR**.
- **Time stop:** if price hasn't moved meaningfully within **8–12
  bars**, exit. Dead trades waste capital and invite reversal.

(R = entry-to-stop distance in price.)

---

## 7. Risk, sizing & phased scaling

- **Risk per trade: 1% of bot capital** (hard rule; see §0).
- **Sizing formula:** `contracts = floor(max_risk_dollars / risk_per_contract)`
  where `risk_per_contract = |entry − stop| × contract_multiplier`.
- **Frequency: maximum 1 contract per asset per day. No minimum.**
  No-trade always allowed.

**Portfolio / correlation risk caps (govern over per-trade risk):**
- **Max total same-direction crypto risk: 2%.** Because BTC/ETH/SOL
  longs (or shorts) are highly correlated, they count toward one
  combined budget — not independent 1% slots.
- When multiple A+ same-direction setups fire, **split** risk rather
  than stack it:
  - take the single best at 1%, OR
  - top two at 0.75% each, OR
  - all three at 0.5% each — never three full 1% positions.
- **Max risk per asset: 1%.**

**Loss limits (circuit breakers):**
- **Max daily loss: 2%** → stop trading for the day. (≈ 2 losing
  trades at 1%.)
- **Max weekly loss: 5%** → stop trading for the week.

**Phased scaling (do not scale by emotion — by rule):**

| Phase | Trigger | Open positions | Risk rule | Notes |
|---|---|---|---|---|
| 1 — Survival (current) | until the bot proves itself | **1 at a time** | 1% per trade; take highest-ranked only | daily-loss 2%, weekly-loss 5%, no forced trades |
| 2 — Validation | after 30 clean paper trades or 30 days | **2 total** | max total same-direction 2%; max 1%/asset; split if all three fire | still no forced trades |
| 3 — Growth | after account grows AND edge proven | by formula | `contracts = floor(max_risk_$ / risk_per_contract)`, caps still apply | scale by rule, not feel |

Funding reality: at the current ~$100 available the account cannot
fund even **one** contract (~$188 margin). Phase 1 live cannot begin
until the account is funded enough for ≥1 contract with the 1% risk
rule intact. Backtest/paper do not require funding.

---

## 8. No-trade conditions (any one ⇒ stand down)

- BTC regime is neutral/choppy.
- Price near the 50-EMA (inside the ±2% band).
- Spread is wide.
- Estimated fee is too high relative to the trade's risk.
- Stop cannot be defined before entry.
- Major news event imminent.
- Revenge trading ("making back" a loss).
- Already 2 losses today.

---

## 9. Validation gates (must pass before advancing a phase)

Per the edge definition (expectancy after all costs):
1. ≥ 30 trades (else "no sample").
2. Positive expectancy after fees + slippage + funding.
3. Profit factor ≥ 1.3.
4. Beats equal-weight BTC/ETH/SOL buy-and-hold on risk-adjusted return.
5. Max drawdown < 25% (MTM).
6. No single trade > 25% of net P&L; no single asset carries the result.
7. Out-of-sample profit factor ≥ 0.75 × in-sample.
8. Paper trading resembles backtest for 30 days before any live capital.

---

## 10. Phased rollout (build/trust order)

1. **Manual / paper rules** — prove valid setups can be identified
   (30 days or 30 clean trades).
2. **Signal bot** — alerts only (regime change, strongest/weakest,
   breakout trigger, no-trade zone). No auto-execution.
3. **Paper execution bot** — simulates orders, logs results.
4. **Tiny live** — only after paper matches backtest, Phase-1 sizing.

---

## 11. Maps to existing code

| Spec element | Status |
|---|---|
| BTC regime 1D | `regime.py` exists (SMA) — **change to EMA50, add ±2% band + neutral/no-trade state** |
| 20-day RS ranking | `universe.rank_by_return` exists |
| 4H/6H bars | `data.resample_ohlcv` (4H) + native 6H |
| Breakout + strong-close + BTC-confirm entry | **new** — extend `signals.py` |
| Setup ranking when multiple fire (§4a) | **new** — selection layer |
| Structure-or-1.5×ATR stop, R-targets, trail, time stop | partial in `btc_backtest` — **extend exits** |
| Multi-asset, long/short, perp/dated cost | `multi_backtest.py` exists |
| 1% sizing, correlation-aware portfolio caps, daily/weekly loss limits, phase caps | **new** — risk layer (feature 3) |
| Signal/paper/live execution | **new** — execution layer (feature 4) |
| Gates report | partial — extend to expectancy + the §9 set |

---

## 12. Open items

- Confirm the regime EMA-distance rule's exact parameters (±2%, EMA20).
- Real funding/basis series to replace the perp/dated placeholders.
- Confirm "volume above recent average" is available/used at 4H/6H.
- Account funding before any live phase.
