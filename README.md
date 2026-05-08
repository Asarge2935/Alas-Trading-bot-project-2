# Alas Trading Bot — Project 2

A personal crypto trading bot for a $500 sandbox account on Coinbase
Advanced (US, CFTC-regulated perpetual futures). Low-frequency 6H swing
scanner across BTC, ETH, SOL, XRP, ADA, DOT.

> Status: **backtest under review, no paper trade started.** Do not run
> with live capital. See `PRE_PAPER_TRADE_CHECKLIST.md` for go/no-go
> criteria before paper trading.

## What's in here

| File | Purpose |
|---|---|
| `backtest.py` | The backtester. Pulls 6H candles from Coinbase Exchange, simulates the strategy, exports CSVs and a summary report. |
| `rules.json` | Strategy configuration in machine-readable form. Source of truth for the (future) live bot config. |
| `REVIEW_NOTES.md` | Findings from the latest code review pass: critical / major / minor issues, what was fixed, what was left alone. |
| `PRE_PAPER_TRADE_CHECKLIST.md` | The go/no-go checklist to run after a 12-month backtest, before starting paper trade. |
| `SAMPLE_RUN.md` | How to run the backtester locally, expected output shape, common errors. |
| `docs/STRATEGY_ADA_XRP_LEGACY.md` | Original 2-asset, 4H, two-bucket strategy. Superseded — kept for reference only. |
| `docs/HANDOFF.md` | The original handoff prompt that scoped this review. |

## Strategy in one paragraph (v2.2)

A 6H trend-pullback scanner. Every closed 6H bar, scan all 6 assets.
A long fires when price is above EMA(50), EMA(20) is above EMA(50),
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

## How to run the backtest

```bash
pip install requests pandas numpy
python backtest.py
```

See `SAMPLE_RUN.md` for the full walkthrough.

## Branch policy

Active development happens on `claude/new-session-ysAXb`. Do not push
to `main` directly.

## What this bot must NEVER touch

The owner has a separate long-term spot portfolio (XRP, ADA, AVAX, HYPE,
PYTH, etc.). The bot's universe is the 6 perpetual futures listed in
`rules.json` and the bot's capital is strictly the $500 sandbox. The bot
must never read, place orders against, or otherwise interact with any
spot holdings.
