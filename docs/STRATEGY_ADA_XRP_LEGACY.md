# ADA + XRP Swing Strategy for Coinbase Perps

> **STATUS: SUPERSEDED.** This is the original 2-asset, 4H, two-bucket
> strategy. It has been replaced by the 6-asset 6H scanner described in
> `rules.alas` and `backtest.py`. Kept here for historical reference only.
> Do not implement against this document.

**Account size:** $500 (split: $250 ADA bucket, $250 XRP bucket — track separately)  
**Timeframe:** 4-hour candles (4H)  
**Leverage:** 2x maximum (well below the 3x ADA / 2x XRP caps)  
**Hold period:** 1-7 days per trade (true swing, not scalping)  
**Trades per week target:** 1-3 across both assets combined  
**Goal:** Survive 90 days. Beat fees. Learn execution. Profit is bonus.

---

## Why this strategy, written plainly

A 4H swing approach is the only thing that mathematically makes sense for a $500 account on Coinbase perps. Here's the reasoning:

Coinbase perp fees are 0.03% taker plus a $0.15 minimum per contract. On a $25 trade, the percentage fee is $0.0075 but the $0.15 minimum kicks in. That's an effective 0.6% round-trip cost per trade. If you scalp on 1-minute candles and take 20 trades a day, you're paying 12% in fees a day — you can't outrun that.

On a 4H timeframe taking 1-3 trades a week, fees become ~1-2% a month of your bankroll. That's beatable.

ADA and XRP both happen to be in choppy ranges right now (ADA $0.22-$0.30, XRP $1.40-$1.50 area). Range-bound assets favor mean-reversion strategies. Trending assets favor trend-following. We'll build for what the market is actually doing.

---

## The Strategy: "Range Reversion with Trend Filter"

Three indicators. All three must align.

**1. EMA(50) on the 4H — the trend filter**
- Price ABOVE EMA(50) = uptrend regime → only take LONGS
- Price BELOW EMA(50) = downtrend regime → only take SHORTS
- This single filter prevents you from buying knives or shorting rockets

**2. RSI(14) on the 4H — the entry trigger**
- For LONGS: RSI must drop below 35 (oversold pullback in an uptrend)
- For SHORTS: RSI must spike above 65 (overbought bounce in a downtrend)
- RSI(14), not RSI(3) like the Lewis Jackson scalping strategy. We want fewer, higher-quality signals.

**3. ATR(14) on the 4H — the volatility check**
- ATR is "average true range" — how much the asset typically moves per candle
- We use it for position sizing AND to skip trades when volatility is extreme
- If current 4H ATR > 2x the 30-day average ATR, SKIP. Volatility regime change.

---

## Entry Rules

### LONG (buy the asset, expecting price to rise)

ALL of these must be true on a closed 4H candle:
1. Price is above the EMA(50) on the 4H
2. RSI(14) on the 4H is below 35
3. Current 4H ATR is less than 2x the 30-day average ATR
4. No existing open position on this asset
5. Daily trade count for this asset is under 2

### SHORT (bet on price falling)

ALL of these must be true on a closed 4H candle:
1. Price is below the EMA(50) on the 4H
2. RSI(14) on the 4H is above 65
3. Current 4H ATR is less than 2x the 30-day average ATR
4. No existing open position on this asset
5. Daily trade count for this asset is under 2

---

## Position Sizing

For a $500 account split into two $250 buckets:

**Per-trade risk:** 2% of bucket = $5
**Stop loss distance:** 2x ATR(14) on 4H from entry
**Position size formula:** `position_size_usd = ($5 risk / stop_distance_pct) × leverage`

Example for ADA at $0.2644 with ATR of $0.012:
- Stop distance = 2 × $0.012 = $0.024 (about 9% from entry)
- Risk per dollar invested without leverage: 9%
- Notional size to risk $5: $5 / 0.09 = $55.55
- With 2x leverage, margin needed: $55.55 / 2 = $27.78

So a typical ADA trade puts $27.78 of margin to work, controlling $55.55 notional. If the stop hits, you lose $5. If the target hits, you make ~$10 (2:1 reward:risk).

**Hard cap:** No single trade uses more than $50 of margin (20% of bucket). Even if the math says otherwise, cap it.

---

## Exit Rules (in priority order)

1. **Stop loss hit:** 2x ATR from entry. Close immediately at market.
2. **Take profit at 4x ATR from entry** (2:1 reward:risk). Close half, move stop to breakeven on the rest.
3. **Time stop:** If position hasn't hit either target after 7 days, close at market regardless of P&L. Stale trades drift.
4. **Trend flip:** If price crosses to the wrong side of EMA(50), close immediately. Your trend filter has flipped.
5. **Funding rate spike:** If perp funding goes above 0.05% per 8-hour period and you're paying it, close. Funding can eat returns silently.

---

## Risk Limits (hard rules — never override)

- **Max 2% of bucket risked per trade**
- **Max 2 open positions across both assets** (one ADA, one XRP)
- **Max 2 trades per asset per day**
- **Max 5 trades per week across both buckets**
- **If bucket drawdown reaches -15%, stop trading that bucket for 7 days.** Sit out, review the trade log, look for what went wrong before resuming.
- **If bucket drawdown reaches -25%, stop trading that bucket entirely and reset.** Either you found out the strategy doesn't work for this asset, or you're in a regime it wasn't built for.

---

## What this strategy does NOT do

- **Pyramid into winners.** No adding to positions.
- **Average down on losers.** No "the trade just needs more time."
- **Trade through major news events.** Skip the 24h before/after FOMC, CPI, major Cardano protocol launches, XRP court ruling dates.
- **Trade with conviction.** This is mechanical. The bot has no opinion about ADA's long-term thesis. Your spot bag does that.

---

## Backtest Expectations (be honest with yourself)

A strategy like this — well-tested on similar timeframes and assets — typically shows:
- **Win rate:** 40-50%
- **Average winner:** ~2x average loser (because of 2:1 reward:risk)
- **Profit factor:** 1.3-1.7 in favorable conditions
- **Max drawdown:** 15-25% in a bad month
- **Annual return:** 20-50% in good conditions, -20% to -10% in bad conditions

If your backtest shows 80% win rate or 5x profit factor, you've over-optimized. Throw it out and rebuild.

---

## What "good" looks like in the first 90 days

- **Days 1-30 (paper):** Bot fires 4-12 trades total across both assets. You verify each signal manually. Track paper P&L.
- **Days 31-60 (paper, refined):** Adjust any parameters that obviously misfired. Don't over-tune.
- **Days 61-90 (tiny live, $50):** Same strategy, real money, smallest possible size. The goal is behavioral. Did you let it run? Did you override losing trades? Did you panic?

If you make it through day 90 with your discipline intact and even slightly profitable, you've done better than ~80% of retail algo traders. That's success at this stage. The money grows later.

---

## ADA-Specific Considerations

- **Current price action:** Range-bound between $0.22-$0.30 since early 2026 per multiple sources. Range-bound = mean reversion friendly.
- **Daily volatility:** ~3% per day on average per DigitalCoinPrice. ATR-based stops should sit comfortably outside noise.
- **Liquidity on Coinbase perp:** Decent. Slippage on $50-100 trades should be negligible.
- **Funding rate behavior:** Has been mildly positive (longs pay shorts) most of 2026 — short trades are slightly favored on funding alone. Don't let this bias your entries, but be aware.
- **Catalyst dates to skip trading:** Watch for Midnight sidechain news, governance hard forks, and ETF speculation announcements. Mechanical strategies fail around binary events.

## XRP-Specific Considerations

- **Current price action:** Trading near $1.40-$1.45. Has been more trending than ADA recently.
- **Volatility:** Generally lower than ADA on a percentage basis but with sudden spikes around legal/regulatory news.
- **The unique XRP risk:** Court rulings, SEC news, ETF announcements. XRP can move 10-15% in an hour on news. Your stop loss may not save you — slippage can be brutal during news-driven moves. **Skip trading XRP for 24h after any major Ripple/SEC headline.**
- **Funding rate behavior:** Variable, sometimes spikes during ETF speculation periods.
- **Liquidity on Coinbase perp:** Strong. XRP is one of Coinbase's most-traded assets.

---

## What I want you to do BEFORE writing any code

1. **Open the Coinbase Advanced charts for ADA-PERP and XRP-PERP**
2. **Switch to 4H timeframe**
3. **Add EMA(50), RSI(14), ATR(14) to your chart**
4. **Look at the last 6 months of price action**
5. **Manually mark every spot where ALL THREE entry conditions would have been met**
6. **Check what happened next** — did 2:1 reward:risk hit? Did the stop hit first?
7. **Count your wins and losses across both assets**

If you can't do this manually first and feel comfortable with what you see, automating it is premature. If the manual count shows the strategy works for these two assets in this market regime, then we automate.

This is the hardest discipline in algo trading: validating before automating. Most people skip this step and lose money. Don't.
