# Strategy Research Roadmap

**Status: RESEARCH / DIAGNOSTIC ONLY.** Nothing in this document authorizes
paper or live trading. No setup here is deployable. All findings are produced
by the offline backtester (`backtest.py`) and its diagnostics; results are
indicative until they clear the gates in §5 on real data.

_Last updated: 2026-05-25._

---

## 1. Rejected (evidence-based, this phase)

| What | Why rejected |
|---|---|
| **BTC/ETH/SOL shared strategy** | One breakout model across all three did not produce a robust edge; the assets behave too differently. |
| **Shorts** | Short side failed across the universe (risk-off breakouts did not follow through). |
| **SOL under current logic** | SOL was the single largest drag on net P&L; high-beta behavior is not served by the ETH-style breakout. |
| **BTC under ETH breakout logic** | BTC did not behave well when forced into the ETH momentum/breakout model; it is better treated as a regime/macro asset. |
| **ETH pullback continuation** | Full-window pullback-only: ~42 trades, PF ≈ 0.62, avg R ≈ −0.25, net ≈ −$50. Combined breakout+pullback turned negative. **This pullback definition is rejected** unless a brand-new hypothesis is explicitly defined later. |

## 2. Promising but NOT deployable

- **ETH long breakout under strict BTC risk-on.** The only pocket with positive
  expectancy so far, but **under-sampled** and concentration-sensitive:
  - 6H: ~13 trades, PF ≈ 2.13, avg R ≈ +0.50, net ≈ +$32.93; still positive
    without the best trade, but **below the 30-trade gate** and top-trade
    concentration still matters.
  - 4H: ~16 trades, PF ≈ 1.43, avg R ≈ +0.26 — only ~3 more trades than 6H and
    **weaker** robustness (4H added noise, not signal).

## 3. Current hypothesis

> ETH long breakout has an edge **only** under specific regime + relative-strength
> conditions: BTC in strict risk-on, ETH in its own uptrend, and ETH ranked #1
> by 20-day return among BTC/ETH/SOL. The edge is real but rare; the open
> question is whether a higher timeframe or a quality filter raises sample
> and/or signal quality **without** loosening the entry.

Active research (diagnostic only):
- **Higher-timeframe test** (`eth_htf_diagnostic.py`): 6H vs 12H vs 1D, breakout
  only, to see whether larger timeframes improve signal quality.
- **Breakout quality audit** (`eth_breakout_quality.py`): what separates winning
  from losing ETH breakouts — descriptive, pre-filter, no recommendation.

## 4. Asset-specific framework

**Do not treat BTC, ETH, and SOL as the same asset.** Build toward separate
modules:

- **BTC = regime / macro asset.** Needs its own model (higher-timeframe
  confirmation, chop avoidance; candidate ideas: trend-continuation pullback,
  compression breakout, liquidity-sweep reversal). **Do not force BTC into the
  ETH breakout model.**
- **ETH = current priority.** Best-behaved candidate for this system; a
  momentum/rotation breakout. Continue researching ETH long breakout. **ETH
  pullback stays rejected** unless a new hypothesis is explicitly defined.
- **SOL = high-beta / experimental only.** Not part of the current ETH strategy.
  Likely needs a separate volatility/liquidation/flush model later. **Do not
  test SOL again this phase unless explicitly requested.**

Target architecture (future): independent `BTC module`, `ETH module`,
`SOL module`, each separately tagged and measured. Current focus is the ETH
module.

## 5. Deployment gates (must ALL pass on real data before paper trading)

A setup is **not deployable** until it clears every gate:

1. **≥ 30 trades** (sample sufficiency).
2. **Profit factor ≥ 1.3.**
3. **Avg R > +0.2.**
4. **Positive expectancy WITHOUT the single best trade** (no single-trade dependence).
5. **Max drawdown < 20–25%.**
6. **Not profitable in only one calendar year** (edge must persist across years / IS & OOS).
7. **Paper trading required before any live trading.**

Passing the backtest gates only qualifies a setup for **paper trading** — it
does **not** authorize live trading.

## 6. Diagnostics index

| Tool | Purpose |
|---|---|
| `backtest.py` | Canonical 6H engine + `--setup` (breakout/pullback/both), `--strict-regime`, `--compare-regime`, `--trade-assets`, `--long-only`. Default behavior unchanged. |
| `eth_htf_diagnostic.py` | ETH breakout across 6H / 12H / 1D. |
| `eth_4h_diagnostic.py` | ETH breakout at 4H (aggregated from 1H). |
| `eth_pullback_diagnostic.py` | Breakout vs pullback-continuation vs combined (tagged by `setup_type`). |
| `eth_breakout_quality.py` | Winner-vs-loser feature audit for ETH breakouts. |
| `robustness_report.py` | Per-run robustness (year, leave-one-out, IS/OOS, loss autopsy); `--setup-type` filter. |

All of the above are **diagnostic only**. No exchange adapter, order placement,
paper, or live behavior is affected by anything in this roadmap.
