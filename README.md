# Alas Trading Bot — Project 2

A personal crypto trading bot for a small Coinbase derivatives account
(BTC / ETH / SOL nano perpetual futures, US-eligible). The strategy is
deliberately low-frequency: it scans constantly but trades rarely, only
when a BTC-led regime, relative strength, and a breakout all line up.

**Canonical spec:** [`docs/STRATEGY_SPEC.md`](docs/STRATEGY_SPEC.md).
That document is the single source of truth; this README is a summary.

> ### Status (2026-05-24)
> The backtester has been rebuilt to the canonical spec (v3). `backtest.py`
> and `rules.json` are the **active** implementation. Earlier strategies
> (the 6H RSI-pullback scanner, and the daily `strategy1/` rotation/
> directional research) are **legacy** — see "Legacy" below.

## Strategy in one paragraph

Every closed 6H bar, classify BTC's **daily regime**: `risk_on` (BTC
above a rising EMA50, not within ±2% of it, not far below EMA20),
`risk_off` (below a falling EMA50), or `neutral` (everything else →
**no trades**). In a directional regime, rank BTC/ETH/SOL by **20-day
return** and take the single best candidate: long the strongest in
risk-on, short the weakest in risk-off. Enter only on a **20-bar
breakout** with a strong close (top/bottom 25% of the bar), above-
average volume, and **BTC confirmation** (BTC on the same side of its
EMA50). Stop is the breakout candle's extreme **or** 1.5×ATR, whichever
is wider. Take 50% off at +1.5R and move the stop to breakeven; trail
the runner at 2.5×ATR; time-stop a dead trade after ~12 bars. Risk is
**1% of equity** per trade, with daily-loss (2%) and weekly-loss (5%)
halts and Phase-1 caps of one open position and one trade per asset per
day. Entry fills at the next bar's open — no same-bar fills. Drawdown
is measured on **mark-to-market** equity (closed + unrealized), so open
losers can't hide behind closed winners.

## What's in here

| File | Purpose |
|---|---|
| `backtest.py` | **Active.** The canonical-spec backtester. Pulls 6H candles from Coinbase Exchange, simulates the strategy, exports CSVs, prints a summary and a §9 validation-gate verdict. |
| `rules.json` | **Active (v3).** Strategy config in machine-readable form; kept in sync with `backtest.py`. |
| `verify_offline.py` | Offline runtime harness — exercises the engine on synthetic data and asserts invariants (MTM, reconciliation, trade limits). No network needed. |
| `docs/STRATEGY_SPEC.md` | **Canonical strategy specification.** |
| `docs/VENUE_AUDIT_PERP_US.md` | Coinbase nano-perp venue audit (fees, contract sizes, margin). |
| `PRE_PAPER_TRADE_CHECKLIST.md` | Go/no-go checklist to run after a backtest, before paper trading. |
| `SAMPLE_RUN.md` | How to run the backtester locally and read the output. |
| `strategy1/` | **Legacy** research library (daily-bar regime/RS/indicator building blocks). |
| `docs/STRATEGY_1_*`, `docs/STRATEGY_2_*`, `docs/STRATEGY_ADA_XRP_LEGACY.md`, `REVIEW_NOTES.md`, `docs/HANDOFF.md` | Historical — superseded by `STRATEGY_SPEC.md`. |

## How to run the backtest

```bash
pip install requests pandas numpy
python backtest.py                # ~4 years of 6H data (default)
python backtest.py --days 365     # shorter window
```

Outputs `backtest_output/trades.csv`, `backtest_output/equity_curve.csv`,
a per-bucket summary, and a validation-gate verdict (PROMOTE / REJECTED).
See `SAMPLE_RUN.md`. To check the engine offline without hitting the API:

```bash
python verify_offline.py
```

## Backtest realism notes

- **Spot proxy.** Uses Coinbase spot candles (`BTC-USD`, ...) as a price
  proxy for the live perp universe (`BTC-PERP-INTX`, ...); the public
  candles endpoint serves spot only. The 6H spot/perp basis is small and
  partly absorbed by the slippage assumption.
- **Closed bars only.** The latest still-forming 6H candle is dropped
  before any indicator is computed.
- **No look-ahead.** Regime and relative strength for a 6H bar use the
  previous completed daily bar; entries fill at the next bar's open.
- **Funding is a flat placeholder** (−0.5%/month). Real historical
  funding must be wired in before live trading.
- **Sizing measures edge, not execution.** The backtest sizes by
  continuous 1%-risk notional to measure expectancy. The live
  integer-contract constraint (a nano perp is ~$188 margin) is a
  Phase-1 execution concern for the live adapter, not the backtest.
- Passing the backtest qualifies the strategy for **paper trading**
  only — never for live capital.

## Legacy

- **`strategy1/`** — a daily-bar research library from the post-2026-05
  reset: regime classifier, relative-strength ranker, indicator library,
  and single-asset/multi-asset directional backtesters. Useful building
  blocks, but the canonical engine is now `backtest.py`. Specs
  `docs/STRATEGY_1_REGIME_RS_SPEC.md` and `docs/STRATEGY_2_BTC_DIRECTIONAL_SPEC.md`
  are superseded by `docs/STRATEGY_SPEC.md`.
- The original 6H RSI-pullback scanner (`rules.json` v2) was archived as
  "no edge"; `backtest.py` has since been rebuilt to the v3 spec.

## Branch policy

Active development happens on the designated feature branch. Do not push
to `main` directly.

## What this bot must NEVER touch

The owner has a separate long-term spot portfolio. The bot's universe is
strictly the BTC/ETH/SOL perps in `rules.json` and its capital is the
isolated sandbox account. The bot must never read, place orders against,
or otherwise interact with any spot holdings.
