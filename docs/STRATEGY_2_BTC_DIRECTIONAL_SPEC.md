# Strategy 2 — Directional (Long / Short / Flat) on BTC / ETH / SOL

**Status:** specification. Backtester implemented; no live execution.
**Current focus:** the user narrowed the universe to **BTC, ETH, and
SOL**, running the **same directional long/short strategy independently
on each** (not a rotation, not a BTC-gated basket). Strategy 1 (broad
RS rotation) is paused, not deleted — its data layer and regime logic
are reused here.

> **2026-05-24 update — three assets, independent.** Originally specced
> BTC-only; extended to BTC/ETH/SOL after the user's change of heart.
> Each asset is long/short/flat on its OWN trend signal. Single-asset
> runs use `strategy1.btc_backtest`; the three-asset version + an
> equal-weight portfolio use `strategy1.multi_backtest`. All three
> have Coinbase US nano perps (BTC 1/100 BTC, SOL 5 SOL/contract, ETH
> nano), so all are long/short-capable on the perp venue — subject to
> the small-account sizing caveat in `VENUE_AUDIT_PERP_US.md`.

---

## 0. Hard execution constraint (read first)

The user's account is **Crypto.com App, US retail, spot only**. It
**cannot short**. See `docs/VENUE_AUDIT_CRYPTO_COM_APP.md`.

Therefore:
- The **long/flat** variant is executable on the current account
  (manual, spot).
- The **short/flat** and **long/short** variants are **research-only**
  until a perp/futures venue is audited and chosen. They are built so
  we can *measure* whether the short side has edge — not because we
  can trade it today.

**Decision rule:** if the backtest shows the short side adds
meaningful, robust edge over long/flat after costs, that result is
the trigger to audit a perp venue. If it does not, we stay long/flat
on spot and never open a perp account. We do not open a perp venue on
spec.

---

## 1. Hypothesis

> BTC trends. A simple trend-state filter (price vs a rising/falling
> 50-day average) identifies regimes where being long (uptrend) or
> short (downtrend) earns more than buy-and-hold, after costs, with
> smaller drawdown. The short side specifically earns its keep during
> sustained downtrends (2022) rather than bleeding via whipsaw in
> chop.

Falsifiable. The most likely failure is that the short side bleeds in
choppy markets and only "works" in one big downtrend (2022), which
gate 7 below would reject as regime-dependent.

---

## 2. Instrument and data

- Single instrument: BTC (spot proxy: Coinbase `BTC-USD` daily, from
  the existing `data_cache/`).
- Daily bars only. No intraday.
- ~4 years available (2022-04 onward). Same history ceiling and its
  consequences as Strategy 1: one bear, one recovery — no 2020-21
  bull, no 2018 bear.

---

## 3. Directional signal

Extends the Strategy 1 regime logic to three states.

**Definition A-dir (trend only):**
- `long`  if close > SMA(50) AND SMA(50) rising over 10 days.
- `short` if close < SMA(50) AND SMA(50) falling over 10 days.
- `flat`  otherwise (price above a falling MA, or below a rising MA —
  i.e. conflicting signals).

**Definition B-dir (vol-aware):**
- Same as A-dir, but stand `flat` instead of `long` when realized
  20-day vol is in the top 20% of its trailing 1Y distribution.
- The vol stand-down applies to **longs only**, not shorts:
  downside-vol expansion is typically when shorts pay, so filtering
  shorts on high vol would throw away their best periods. (This is a
  deliberate asymmetry; it is itself a hypothesis to be checked, not
  a law.)

Signal is computed on the daily **close**. Position is taken at the
**next day's open** — no same-bar fills, no look-ahead.

---

## 4. Position, sizing, costs

- One position at a time (long, short, or flat). No pyramiding.
- Fixed fractional sizing: position notional = `equity × target_leverage`.
  v0 `target_leverage = 1.0` (no leverage). Spot-equivalent.
- **Costs (applied in backtest):**
  - Taker fee 0.20% per side (placeholder; replace with real tier).
  - Slippage 0.05% per side.
  - For short/long-short variants, a **funding/borrow drag** of
    0.03%/day on the position notional (placeholder for perp funding;
    real funding history must replace this before any conclusion is
    trusted). Long/flat spot variant has no funding.
- No leverage in v0. No averaging down. No martingale.

---

## 5. Risk controls (from project risk rules)

- Per-position stop: exit if close moves 2× ATR(14) against entry.
- Max drawdown stop on MTM equity: 25% halts the backtest equity
  curve evaluation (and would halt the live bot).
- These are applied in the backtester and reported.

---

## 6. Variants run

The backtester runs all three and reports them side by side:

1. `long_flat`   — long in uptrend, flat otherwise. **Spot-executable.**
2. `short_flat`  — short in downtrend, flat otherwise. Research-only.
3. `long_short`  — long in uptrend, short in downtrend, flat in
   between. Research-only.

Plus the benchmark: **buy-and-hold BTC**.

---

## 7. Pass / fail gates (per variant, stop at first failure)

| # | Gate | Threshold |
|---|---|---|
| 1 | Trade count | ≥ 30 closed trades (else "no sample") |
| 2 | Net profit factor after costs | ≥ 1.3 |
| 3 | Beats buy-and-hold BTC on risk-adjusted return | Sharpe ≥ 1.1 × HODL Sharpe |
| 4 | Max drawdown (MTM) | < 25% |
| 5 | No single trade > 25% of net P&L | hard cap |
| 6 | Works across both 2022 (down) and 2023-24 (up) | not catastrophic in either |
| 7 | Out-of-sample (2025+) profit factor | ≥ 0.75 × in-sample |

For the **short side specifically**: if `short_flat` fails gate 6
(i.e. all its P&L comes from the 2022 downtrend), the short side is
declared regime-dependent and **not** a reason to open a perp venue.

---

## 8. What this does not do

- No leverage in v0.
- No intraday signals.
- No multi-asset anything (BTC only, by the user's direction).
- No live shorting (no venue).
- No parameter optimization before a v0 result exists.
- Does not open or recommend a perp venue unless the short side
  passes its gates.
