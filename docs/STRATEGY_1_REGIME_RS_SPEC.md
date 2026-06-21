# Strategy 1 — Regime + Relative Strength Rotation

> **⚠ SUPERSEDED (2026-05-24)** by `docs/STRATEGY_SPEC.md` (the canonical
> strategy). Kept for history.

**Status:** specification only. No code, no backtest, no execution.
**Venue dependence:** **none.** Designed to run on spot (long-only)
or perps (long/short), so it can proceed in parallel with the
unresolved Crypto.com venue question.
**Why this strategy first:** lowest execution sensitivity (daily
bars), portfolio-level signals (more trade samples), tests the most
fundamental crypto thesis (BTC drives the regime; alts rotate by
relative strength). If this strategy has no edge, our priors about
crypto market structure are wrong, which is information worth having
early.

---

## 1. Hypothesis (write this down before any code)

> When BTC is in a risk-on regime, a long-only basket of the top-N
> relative-strength alts, rebalanced weekly, outperforms BTC and
> survives realistic costs (fees, slippage, no funding because spot).
> When BTC is in a risk-off regime, the strategy is flat (in USDC).
> Result: equity curve with a meaningfully smaller drawdown than
> buy-and-hold BTC and a higher risk-adjusted return.

This is **falsifiable**. If the backtest comes back with a flat or
worse equity curve vs BTC buy-and-hold after costs, the hypothesis
is rejected and we move on.

We do **not** start by asking "what parameters make this work." We
start by asking "is the effect there at all with sensible defaults."

---

## 2. Universe

- **Index asset:** BTC (regime definition).
- **Trade universe:** top ~20 spot-tradeable USD/USDC pairs by
  3-month average dollar volume, **excluding stablecoins** and
  excluding wrapped/duplicate tokens (no WBTC, no stETH, no
  duplicates of the same underlying).
- **Survivorship handling:** universe must be reconstructed
  point-in-time. At every rebalance date, use only assets that were
  in the top-N **as of that date**, not as of today. This is the
  single biggest source of fake edge in this kind of strategy.
- **Listing/relisting:** an asset is eligible only if it has at least
  120 daily bars of history at the rebalance date, to avoid
  newly-listed pump windows.

---

## 3. Regime definition

Two parallel definitions; pick one only after looking at both.

**Definition A (simple):**
- Risk-on if BTC close > BTC SMA(50, daily) AND BTC SMA(50) is
  rising vs 10 days ago.
- Risk-off otherwise.

**Definition B (slightly more robust):**
- Risk-on if BTC close > BTC SMA(50) AND BTC realized vol(20) is
  not in the top 20% of its trailing 1Y distribution (i.e., not in
  a stress regime).
- Risk-off otherwise.

Backtest both **independently** and report which one produces the
better risk-adjusted result on the in-sample window. Then re-test
the chosen one on the held-out out-of-sample window. If the choice
flips between in-sample and out-of-sample, the regime definition is
overfitting the noise — kill the strategy.

---

## 4. Relative strength definition

- For each asset in the universe, compute return over a fixed
  lookback **L** measured in days, on daily closes.
- Rank assets descending by L-day return.
- Long the top **N** assets, equally weighted.

**Defaults (do not tune in v0):**
- L = 30 days
- N = 5
- Rebalance weekly (every 7 days, on the same weekday).

**Sensitivity check (after the v0 result, not as a tuning exercise):**
- Re-run with L ∈ {14, 30, 60, 90} and N ∈ {3, 5, 8}.
- Plot a heatmap of profit factor across (L, N).
- If the result is highly sensitive — e.g., great at (30, 5) and
  terrible at (30, 4) and (30, 6) — the strategy is **fragile** and
  not deployable, even if v0 looked good.

---

## 5. Position sizing

- Equal weight across the N positions.
- Per-position dollar value = `account_equity / N` at rebalance.
- No leverage. Spot only.
- No averaging in/out. Full rebalance once per week on close.

---

## 6. Entry / exit rules (spot, long-only)

- **At each weekly rebalance close:**
  1. Determine regime (Definition A or B).
  2. If risk-off: target portfolio = 100% USDC. Sell anything not
     already USDC.
  3. If risk-on: compute the new top-N. Sell positions that have
     dropped out of the top-N. Buy positions that have entered the
     top-N. Rebalance held positions back to equal weight only if
     they have drifted by more than 25% relative weight (to reduce
     turnover; document this threshold so it can be tested later).

- **No intra-week stops.** This strategy is regime-driven, not
  trade-driven. Adding stops mid-week introduces a different
  hypothesis and must be tested separately.

- **No same-bar fills.** Decision is made on the daily close;
  execution price is the next day's open. Anything else is
  look-ahead bias.

---

## 7. Cost model (apply during backtest, not after)

- **Spot fees:** 0.20% per side as a default placeholder. Replace
  with the user's actual fee tier once venue is decided.
- **Spread / slippage:** 0.05% per side as a default for liquid
  top-20 alts. For each rebalance, also cap the assumed fillable
  size at 1% of the prior 20-day average dollar volume — if a
  rebalance would exceed that, the trade is shrunk, not pretended.
- **Funding:** N/A (spot).
- **Tax:** out of scope of the backtest. Note that frequent rotation
  in a US taxable account generates short-term gains. This is a real
  cost that the backtest will not show — flag it in the report.

The cost model is non-negotiable. If the strategy only works at
zero-cost it does not work.

---

## 8. Data requirements

- Daily close, daily volume, for ~30 candidate assets, going back at
  least 4 years (covers a full bull/bear/bull cycle).
- Daily BTC close for regime definition (already included).
- Source: any reliable public source. CoinGecko, Binance public
  REST, Coinbase Exchange public REST, or Crypto.com public REST
  all work. **Do not** use a single source if it has known gaps;
  cross-check at least two for any asset that produces a top result.
- Survivorship-safe universe: at each rebalance date, recompute the
  top-20 by trailing 90-day dollar volume **as of that date**.

Do not start strategy code until the data set passes a quality check:
- Plot every series, eyeball for gaps and obvious bad ticks.
- Check listing dates per asset against external records.
- Confirm dollar-volume rankings match a known reference (e.g.,
  CoinGecko's historical volume rank) for a few spot-checked dates.

---

## 9. Backtest mechanics

- Walk-forward / out-of-sample split:
  - **In-sample:** earliest available data through 2024-12-31.
  - **Out-of-sample:** 2025-01-01 through last full calendar month
    before run date. Held back. Look at it **once.**
- Per-trade tagging: every closed position records (entry date,
  exit date, asset, regime at entry, weeks held, gross P&L, net
  P&L, contribution to portfolio return).
- Equity curve must be marked-to-market daily, not just at
  rebalance points. Drawdown is measured on the daily MTM equity.

---

## 10. Pass / fail gates (apply in this order, stop at first failure)

| # | Gate | Threshold | If failed |
|---|---|---|---|
| 1 | Sample size | ≥ 30 closed-position observations across the in-sample window | "No sample." Do not interpret further results. Strategy is **not testable**, not necessarily bad. |
| 2 | Net profit factor (in-sample, after costs) | ≥ 1.3 | "No edge." Strategy rejected. |
| 3 | Beats BTC buy-and-hold on risk-adjusted return (in-sample) | Sharpe of strategy ≥ 1.1 × Sharpe of BTC HODL | "Doesn't beat the benchmark." Rejected — you could just hold BTC. |
| 4 | Max in-sample drawdown | < 25% on MTM equity | "Too painful." Rejected for this account size. |
| 5 | No single asset > 40% of net P&L | hard cap | "Concentration risk." Rejected as a strategy; may be a single-asset trade. |
| 6 | No single trade > 25% of net P&L | hard cap | "Lucky, not edge." Rejected. |
| 7 | Works in both 2022 (bear) and 2023–24 (recovery/bull) — non-trivially positive in at least one of the down years and not catastrophically negative in any year | qualitative | "Regime-dependent." Rejected as standalone, may be combined later. |
| 8 | Sensitivity heatmap (§4) shows a contiguous green region, not a single sweet spot | qualitative | "Curve-fit." Rejected. |
| 9 | Out-of-sample profit factor | ≥ 0.75 × in-sample profit factor | "Decay." Rejected. |
| 10 | Out-of-sample drawdown | not worse than in-sample by more than 1.5× | "Out-of-distribution failure." Rejected. |

If **all 10 pass**, strategy is promoted to paper trading. Not before.

---

## 11. Promotion criteria (only after §10 passes)

Paper trade for 30–60 calendar days using live data and the same
rules. Verify:
- Number of rebalances in paper period is within ±25% of what the
  backtest would have produced over the same window.
- Realized fills (after spread/slippage) are within ±25% of the
  cost model.
- Equity curve shape resembles the corresponding 30–60 day window in
  the backtest.

If paper diverges materially from backtest, **the backtest is wrong
or the cost model is wrong** — do not advance to live. Fix it first.

Live phase 1: start with $50–$100 of risk capital, not the full
$500 account. The point is to verify execution wiring, not to make
money.

---

## 12. Explicit non-goals (do not build these in v0)

- No shorting (perp-dependent — venue not resolved).
- No funding-rate signal.
- No intraday entries.
- No stop-losses (different hypothesis — test separately later).
- No volatility targeting / position-size scaling (different
  hypothesis — test separately later).
- No optimization over (L, N) before the v0 result is known.
- No combination with any other strategy. One strategy in flight.

---

## 13. What an honest negative result looks like

The most likely outcome — based on prior research on momentum/RS
strategies in crypto and equities — is one of:

1. **Edge exists but is marginal.** Profit factor 1.1–1.3, Sharpe
   close to BTC HODL. → Rejected on Gate 2 or 3. **This is the
   modal outcome and you should expect it.**
2. **Edge appears in-sample, decays out-of-sample.** → Rejected on
   Gate 9. The 30D/N=5 default may be coincidentally good on the
   2020–2024 window and not in 2025+.
3. **Edge depends on one bull cycle.** → Rejected on Gate 7. The
   2020–2021 alt-season would carry the entire result.
4. **Edge depends on a few specific coins.** → Rejected on Gate 5.

If any of these happens, archive a one-page post-mortem describing
which gate failed and by how much. **Do not** retune parameters to
rescue the strategy — that is exactly the curve-fitting trap the
project is supposed to avoid. Move to Strategy 2.

A real positive result would look like: profit factor ≥ 1.5 in-sample
and ≥ 1.2 out-of-sample, Sharpe meaningfully above BTC HODL, drawdown
under 20%, no single-asset or single-year dependence, sensitivity
heatmap broad. That outcome is possible but should not be the
expectation going in.

---

## 14. Build order for this strategy

1. **Data layer** — historical daily candles + volumes for ~30
   candidate assets, point-in-time universe construction utility.
   Quality-check the data **before** writing any strategy code.
2. **Regime classifier** — implement Definitions A and B as pure
   functions over BTC daily series. Plot regime overlays on BTC
   chart and eyeball them — do they look like risk-on/risk-off
   periods you'd recognize? If not, the definition is wrong.
3. **Relative strength ranker** — pure function from daily price
   matrix to top-N selection at a given date, with point-in-time
   universe filter.
4. **Backtester** — daily MTM, weekly rebalance, cost model from §7,
   per-trade tagging from §9.
5. **Report generator** — outputs the table for §10 gates plus the
   sensitivity heatmap from §4.
6. **Run, evaluate against §10, decide.** Either promote or kill.

No part of this requires execution access to any venue. It can
proceed in parallel with the Crypto.com venue resolution.

---

## 15. Open questions that do not block this strategy

- Final fee tier for cost model — placeholder used until venue is
  decided.
- Whether USDC is the right cash leg vs USDT or fiat — depends on
  venue. Doesn't affect the backtest result materially.
- Tax treatment in a US taxable account — flagged in §7; not a
  backtest concern but a real-money concern.
