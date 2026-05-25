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
| **Simple BTC higher-timeframe trend-continuation pullback** | Sufficient sample, no edge across 12H/1D (see §1.1). PF < 1 on every run. **This specific passive pullback model is rejected; do not tune it.** NOTE: this rejects the *model*, **not BTC** — BTC remains an open research module (§4.1). |

### 1.1 BTC trend-continuation pullback — diagnostic result (`btc_diagnostic.py`)

| Run | Trades | PF | Avg R | Net P&L |
|---|---|---|---|---|
| 12H full | 157 | 0.83 | −0.06 | −$48.03 |
| 1D full | 108 | 0.84 | −0.05 | −$31.16 |
| 12H 730-day smoke | 73 | 0.90 | −0.03 | −$13.25 |
| 1D 730-day smoke | 51 | 0.71 | −0.12 | −$30.04 |

**Verdict: REJECT the simple BTC higher-timeframe trend-continuation pullback
model — NOT BTC itself.** Sufficient sample (well above the 30-trade gate) and
**no edge** — PF < 1 and avg R < 0 on every run. **Do not tune this setup.**

Interpretation:
- Sufficient sample; the passive BTC pullback model simply has no edge.
- Losers **rarely reached +1R** before failing, so **exits are not the main
  issue** — the trades were wrong from entry.
- The likely problem is **entry timing / regime / volatility context**, not the
  exit framework. A passive "buy the dip in an uptrend" rule does not capture
  when BTC is actually set up to continue.
- This points research toward **structural** BTC hypotheses (§4.1), not
  parameter tweaks.

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

- **BTC = regime / macro asset, and an OPEN research module (§4.1).** BTC is
  **not** rejected — only the simple passive trend-continuation pullback model
  is (§1.1). No deployable BTC setup yet. **Do not force BTC into the ETH
  breakout model.**
- **ETH = current priority.** Best-behaved candidate for this system; a
  momentum/rotation breakout. Continue researching ETH long breakout. **ETH
  pullback stays rejected** unless a new hypothesis is explicitly defined.
- **SOL = high-beta / experimental only.** Not part of the current ETH strategy.
  Likely needs a separate volatility/liquidation/flush model later. **Do not
  test SOL again this phase unless explicitly requested.**

Target architecture (future): independent `BTC module`, `ETH module`,
`SOL module`, each separately tagged and measured. Current focus is the ETH
module.

### 4.1 BTC research module (open)

BTC remains a separate, active research module. The simple passive pullback
model is rejected (§1.1); the next hypotheses are **structural**, not
parameter variants of the same idea.

**Future BTC hypotheses (each tested separately, tagged):**
1. **Compression → Expansion → Retest** — trade BTC out of a volatility
   squeeze on the expansion breakout (and/or its retest), not on a passive dip.
   _(First test: `btc_compression_diagnostic.py`.)_
2. **Failed breakdown / liquidity-sweep reversal** — fade a sweep of obvious
   lows that fails and reclaims.
3. **Funding + open-interest crowding model** — fade/align with crowded
   positioning using funding rate and OI.

**Warning — how to research BTC:**
- **Avoid EMA/threshold micro-optimization.** Do not tune EMA spans, ATR
  multiples, or gate cutoffs to make a weak idea look good (curve-fitting).
- **Only test structural hypotheses** (regime / volatility-state / positioning
  changes). If a structural idea has no edge at sensible defaults, reject it —
  do not tune it into looking profitable.

**BTC diagnostic needs (data/features for the hypotheses above):**
- volatility regime (e.g. ATR14 / ATR120)
- compression state (range/BB-width percentile)
- trend strength
- session / time bucket
- funding rate
- open-interest delta
- liquidation proxy (if available)

### 4.1.1 BTC compression breakout — results so far (research lead, NOT deployable)

The compression→expansion idea (hypothesis 1) is **structurally better** than the
passive pullback, but remains **under-sampled and fragile** — a research lead
only, not deployable.

**Baseline 12H compression breakout** (`btc_compression_diagnostic.py`):
21 trades, PF 1.50, avg R +0.14, net +$14.15 — but **ex-best PF 1.04** (edge
nearly all in one trade) and the 730-day smoke weakened to PF 0.87. Below the
30-trade gate.

**Compression quality audit** (`btc_compression_quality.py`, **LOW_SAMPLE —
descriptive only**): 21 trades, 9 winners / 12 losers.
- By compression count: **comp_count 2 → 3W/9L (25% win)**; **comp_count 3 →
  6W/3L (67% win)**.
- Winners had **lower ATR14/ATR120, tighter range percentile, and tighter BB-
  width percentile** than losers (i.e. winners came from *deeper* compression).
- **Volume ratio and breakout distance were HIGHER for losers** — raw breakout
  strength did **not** separate winners from losers.

**HTF-alignment diagnostic** (`btc_htf_alignment_diagnostic.py`, one filter at a time):

| Variant | Trades | PF | Avg R | Net | Ex-best PF |
|---|---|---|---|---|---|
| baseline | 21 | 1.50 | +0.14 | +$14.15 | 1.04 |
| close > daily EMA200 | 16 | 1.31 | +0.09 | +$6.86 | 0.98 |
| daily EMA50 slope > 0 | 15 | 0.98 | −0.00 | −$0.38 | 0.70 |
| both | 13 | 1.26 | +0.08 | +$5.12 | 0.89 |

**Verdict:**
- **HTF alignment did NOT improve BTC compression** — every variant cut sample
  and dropped ex-best PF below 1.0 (more fragile, not less).
- **Do not add EMA200 / EMA50-slope filters.**
- **BTC compression remains a research lead only — NOT deployable** (<30 trades
  and ex-best fragility).
- **Next BTC compression question (if tested):** does **full compression
  (comp_count == 3)** improve quality without overfitting? Test as a standalone
  variant (`btc_full_compression_diagnostic.py`); expect a *very* small sample,
  so treat any improvement as indicative only and **do not tune**.

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
| `btc_diagnostic.py` | BTC trend-continuation pullback, 12H/1D (rejected — see §1.1). |
| `btc_compression_diagnostic.py` | BTC compression → expansion breakout, 12H/1D (§4.1 hypothesis 1). |
| `robustness_report.py` | Per-run robustness (year, leave-one-out, IS/OOS, loss autopsy); `--setup-type` filter. |

All of the above are **diagnostic only**. No exchange adapter, order placement,
paper, or live behavior is affected by anything in this roadmap.

## 7. Future SOL Module — High-Beta Liquidity/Volatility Model

**STATUS: SOL is REJECTED under the current ETH-style strategy and is NOT part
of the current trading candidate. SOL is future experimental research only.**
Nothing in this section is implemented, tested, deployed, paper, or live. No
SOL strategy code exists yet; this records the intended research direction.

### Principles
1. **SOL should not use the ETH breakout/pullback rules.** It is a different
   market microstructure and must be modeled on its own terms.
2. **Direction alone is not enough for SOL perps.** Being right on direction
   does not survive SOL's noise, wicks, and funding/leverage costs.
3. **SOL requires timing, volatility, liquidity, leverage, positioning, and
   exit discipline** — all together, not direction prediction alone.
4. **SOL behaves as a high-beta, liquidity-hunting market**, characterized by:
   - fake breakouts
   - stop runs
   - violent squeezes
   - V-reversals
   - volatility regime shifts
5. **Model around trapped positioning, not simple direction prediction.** The
   edge (if any) comes from fading/aligning with where crowded positions are
   forced to unwind, not from forecasting price.

### Candidate SOL setup families (to be tested separately, each tagged)
1. **Volatility expansion after compression** (range/ATR compression → expansion).
2. **Liquidation flush reversal** (capitulation flush → mean reversion).
3. **Failed breakout / trap reversal** (breakout fails and reverses on trapped entries).
4. **Funding + open-interest crowding reversal** (crowded positioning unwind).

### Future SOL data requirements
- price
- volume
- ATR / realized volatility
- VWAP
- funding rate
- open interest
- spread / depth (if available)
- liquidation proxy or liquidation data (if available)
- BTC regime
- SOL/BTC relative strength
- time / session bucket

### Future SOL risk rules
- smaller starting risk than ETH
- dynamic leverage based on volatility
- hard daily drawdown stop
- no averaging down
- no trading during extreme spread / high-chaos conditions
- no forced daily trades
- strict max-consecutive-loss stop

These principles, setup families, data, and risk rules are research notes only.
Any future SOL module must still clear every deployment gate in §5 on real data,
and would require paper trading before any live consideration.
