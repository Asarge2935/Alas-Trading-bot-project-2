# Alas Trading Bot — Project 2

A personal crypto trading bot for a $500 sandbox account on Coinbase
Advanced (US, CFTC-regulated perpetual futures).

**Active strategy:** 1D BTC-regime volatility breakout on BTC, ETH, SOL
(Phase 1). See `breakout_backtest.py` and `REVIEW_NOTES.md` "Phase 1".

**Archived:** 6H trend-pullback scanner v2.2 (`backtest.py`) — failed
its backtest gates; retained for audit.

> Status: **backtest under review, no paper trade started.** Do not run
> with live capital. See `PRE_PAPER_TRADE_CHECKLIST.md` for go/no-go
> criteria before paper trading.

## What's in here

| File | Purpose |
|---|---|
| **`breakout_backtest.py`** | **Phase 1 active strategy.** 1D BTC-regime volatility breakout on BTC/ETH/SOL. See `REVIEW_NOTES.md` "Phase 1 — strategy redesign". |
| **`verify_offline_breakout.py`** | Offline runtime gate harness for the breakout strategy. Run before any live Coinbase fetch. |
| `backtest.py` | **Archived (v2.2).** Original 6H trend-pullback scanner. Failed gates on both 1-year and 3-year live data — kept as the audit record of what was tried. |
| `rules.json` | v2.2 strategy configuration (archival). Phase 1 config is currently inline in `breakout_backtest.py`; will be formalized into JSON if/when the strategy passes its gates. |
| `REVIEW_NOTES.md` | Audit log across all review passes and the Phase 1 redesign. |
| `PRE_PAPER_TRADE_CHECKLIST.md` | Go/no-go checklist (gates apply equally to Phase 1 results). |
| `SAMPLE_RUN.md` | How to run the v2.2 backtester (mostly historical now). |
| `docs/STRATEGY_ADA_XRP_LEGACY.md` | Original 2-asset, 4H, two-bucket strategy. Superseded. |
| `docs/HANDOFF.md` | Original handoff prompt that scoped the v2.x review. |

## Active strategy — Phase 1: 1D BTC-regime volatility breakout

A daily-bar volatility breakout on BTC, ETH, and SOL only. Long signals
fire when BTC's daily close is above its 50-day EMA, the asset's prior
bar was in compression (ATR(14) < 0.7 × ATR(60)), and the current bar
closes above the previous 20-bar high by at least 0.1 × ATR(14).
Shorts mirror with BTC below its EMA50 and a 20-bar low breakdown.
Entry at the next daily open. Stop at the opposite edge of the prior
20-bar range (Donchian-style). Partial 50% at +2R; runner trails on a
chandelier exit (highest-high − 3 × ATR for longs) with breakeven as
a floor. Time stop at 20 daily bars. After a clean stop-out (no partial
fired), that symbol is on a 5-bar cooldown.

> **B1 relaxation (2026-05-09):** the original spec included a
> `volume ≥ 1.2 × 20-bar avg` confirmation. The first 3-year live run
> produced only 5 trades; the volume gate was the highest-impact
> single relaxation and was removed. See `REVIEW_NOTES.md` "Phase 1 —
> B1 relaxation".

Validation gates (user-stated): PF ≥ 1.3, avg R > 0, no single asset >
50% of net P&L, ≥ 30 trades over 3 years, max consecutive losses < 8.

Run via `python3 breakout_backtest.py`. See
`verify_offline_breakout.py` for the offline pipeline gate.

---

## Archived — v2.2 6H trend-pullback scanner

> Failed both 1-year and 3-year backtests with PF 0.52–0.54. Kept here
> as the audit record of what was tried before the Phase 1 redesign.

A 6H trend-pullback scanner across 6 assets. A long fires when price
is above EMA(50), EMA(20) is above EMA(50),
RSI(14) is below 35, and ATR is not in a volatility blow-off. Shorts
are the mirror (RSI > 65). Entry is at the next bar's open — no
same-bar fills. Stop at 2 ATR. Close 50% at 3 ATR and move stop to
breakeven. Close runner at 6 ATR or after 56 bars (14 days), whichever
comes first. Risk is fixed at $5 per trade ($500 account, 1%). Max 2
open positions, 5 trades per week portfolio-wide. Drawdown 15% pauses
the bot for 7 days, 25% stops it entirely; drawdown is measured on
**mark-to-market** equity (closed P&L plus unrealized open P&L), so
open losers can't hide behind closed winners.

> **v2.1 change:** the volume filter (≥ 1.2× 20-bar avg) and the BTC
> regime filter (RSI 35–65) were removed after a funnel diagnostic
> showed they were over-restrictive — the volume filter inverted the
> pullback premise (pullbacks are low-volume by nature) and the BTC
> regime gate killed 100% of short signals over the test window.
>
> **v2.2 change:** RSI thresholds widened from 30/70 to 35/65 to bring
> the trade count into the §A/§C statistical range (30–200 trades over
> 12 months). EMA alignment, ATR regime, position sizing, and all
> risk limits unchanged.
>
> See `REVIEW_NOTES.md` "Third pass" for data and reasoning.

## Backtest realism notes

- **Spot proxy.** The backtester uses Coinbase spot candles
  (`BTC-USD`, ...) as a price proxy for the live perp universe
  (`BTC-PERP-INTX`, ...). Coinbase's public candles endpoint serves
  spot only.
- **Closed bars only.** The latest still-forming 6H candle is dropped
  before any indicator is computed.
- **Funding is a flat placeholder** (-0.5%/month). Real historical
  funding-rate data must be wired in before live trading.
- Passing the backtest qualifies the strategy for **paper trading**
  only — not for live capital.

## How to run

```bash
pip install requests pandas numpy

# Phase 1 active strategy (1D breakout, BTC/ETH/SOL):
python3 verify_offline_breakout.py     # ~10s, no network — pipeline gate
python3 breakout_backtest.py           # ~2-3 min, fetches Coinbase 1D candles

# Archived v2.2 (6H trend-pullback, 6 assets) — kept for audit:
python3 backtest.py                    # see SAMPLE_RUN.md for the walkthrough
```

## Branch policy

Active development happens on `claude/new-session-ysAXb`. Do not push
to `main` directly.

## What this bot must NEVER touch

The owner has a separate long-term spot portfolio (XRP, ADA, AVAX, HYPE,
PYTH, etc.). The bot's universe is the 6 perpetual futures listed in
`rules.json` and the bot's capital is strictly the $500 sandbox. The bot
must never read, place orders against, or otherwise interact with any
spot holdings.
