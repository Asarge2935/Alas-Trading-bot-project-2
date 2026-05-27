# Strategy Research Roadmap

**Status: RESEARCH / DIAGNOSTIC ONLY.** Nothing in this document authorizes
paper or live trading. No setup here is deployable. All findings are produced
by the offline backtester (`backtest.py`) and its diagnostics; results are
indicative until they clear the gates in §5 on real data.

_Last updated: 2026-05-25._

---

## 0. Current Bottleneck: Persistence Validation

**The bottleneck is now validation depth, not feature discovery.** Do NOT add
new indicators, filters, exits, or strategy complexity unless explicitly
requested. Both candidates are behaviorally coherent but statistically immature.

- **ETH** needs **occurrence expansion and persistence validation**, NOT more
  feature stacking. The open question is whether the momentum-acceptance /
  "failure-to-separate" behavior **persists** across more independent conditions
  (longer history, volatility regimes, ETH/BTC relative-strength cycles, and
  bull/bear/range regimes) — and whether more natural occurrences exist **without
  weakening setup quality**.
- **BTC** (12H compression / volatility-release) is **paused from further
  strategy-complexity work.** Next BTC progress requires **independent
  validation data** (more historical regimes, additional exchanges, and
  eventually perp-structure data: funding, OI delta, liquidation/sweep), NOT
  more variants. Keep BTC framed as volatility-release research, not trend
  continuation.
- **No strategy advances because of a small-sample PF spike.** The next research
  milestone is **not "higher PF"** — it is **"the same behavior persists across
  more independent conditions."**

Asset character (current working models, both unvalidated):
- ETH ≈ momentum-acceptance / failure-to-separate phenomenon.
- BTC ≈ rare volatility-release / compression-auction phenomenon.
- SOL ≈ parked (future separate model, §7).

---

## 0.1 Validation Data Expansion Phase (current)

The bottleneck is no longer strategy invention — it is **insufficient
independent validation depth.** This phase tests **structural persistence
across more independent data**, not better-looking metrics.

- **ETH** needs **independent validation data**, not more feature stacking.
- **BTC** needs **independent validation data**, not more strategy variants.
- **Next milestone:** the same behavior persisting across **more data sources
  and instruments** (e.g. Coinbase/Binance/Kraken ETH spot, BTC spot, and — if
  available — perpetual candles), validated via CSV import.

**Metrics to compare across each new dataset** (behavior, not just PF):
trade count · winner count · ex-best behavior · top-trade concentration ·
top-3 concentration · best-month concentration · OOS preservation · regime
consistency · **ETH failure-to-separate** behavior · **BTC full-compression**
behavior.

**Rules for this phase:**
- Do **not** advance a model because PF improves on one new dataset.
- Treat a higher PF as **descriptive only** unless robustness, distribution,
  and the sample/winner gates *also* improve.
- No new filters, no timeframe optimization, no weekly, no weakening setup
  quality to create more occurrences.

**First independent-data run — Binance spot, ~6.3 years (2020-01 .. 2026-04):**
- **ETH 6H strict breakout: robustness did NOT persist *in the partial run*.**
  With Binance ETH but **Coinbase** BTC/SOL context, ex-best PF fell 1.55 →
  0.97 and ex-best net went negative. **CORRECTED (see below):** that collapse
  was largely a **venue-mismatch artifact** — re-running **fully independent**
  (BTC/SOL also from Binance) restored ex-best PF to **1.13 / +$7.24**.
- **BTC 12H full-compression: behavior persisted, rarity is fatal.** Shape held
  (ex-best PF 2.35, OOS PF 4.31 > IS 2.54, top 35%), but only **15 trades over
  6.3y** — independent + longer data did **not** fix the structural rarity. See
  §4.1.1.

**Data integrity + fully-independent + failure-to-separate EXIT (Step 1 & 2):**
- **Data integrity CLEAN** (`data_integrity_audit.py`): Binance BTC/ETH 99.81%
  coverage, no dups/overlaps, ms→us seam clean; cross-source vs Coinbase
  corr 0.99973 (median |Δ| 0.45%, USDT-vs-USD). Data is trustworthy.
- **Venue-mismatch corrected:** fully-independent Binance ETH (BTC/SOL also
  Binance) gives ETH ex-best PF **1.13 (+$7.24)**, top-trade 110%→70% — so ETH
  is **fragile but ALIVE**, not the one-trade artifact the partial run implied.
- **Failure-to-separate confirmed as a behavior across THREE datasets**
  (Coinbase 6/8 vs 0/5; Binance-partial 6/8 vs 1/8; Binance-full 9/11 vs 2/11;
  fast +1.02R/82% vs slow −0.57R/18%).
- **Fixed K=2 failure-to-separate EXIT improved ROBUSTNESS on BOTH datasets**
  (one un-tuned rule: "not +0.5R by close of bar 2 → exit at bar-2 close").
  Coinbase ex-best PF 1.55→1.90, maxDD 2.70→1.59%, top 51.6→45.9%, top-3
  131.8→110.7%. Binance-full ex-best PF 1.13→1.58, maxDD 3.84→2.20%, top
  70.1→42.2%, top-3 178.2→101.8%, positive years 4/7→5/7. It neutralized the
  worst year (2024) on both. **Strongest result in the project so far.**
  *Caveat:* the rule was derived from behavior on these same datasets, so it is
  **not truly out-of-sample**; and early exits free the 1-slot so trade counts
  shifted (13→14, 22→24) — baseline-vs-exit is faithfully re-simulated, **not**
  a pure same-trades comparison.
- **Still NOT deployable:** 14 / 24 trades (< 30 gate); Coinbase 8 winners
  (< 12); top-3 still ~100–110% of net; **2026 negative on both**.
- **Net: nothing deployable; validation caught real fragility (ETH) and an
  irreducible sample ceiling (BTC) that a PF-only view would have missed.**
- **Next milestone: TRUE unseen validation** — run the EXACT SAME fixed K=2 /
  +0.5R rule on a third venue/instrument (§0.3). Do NOT tune K. Do NOT advance
  on PF alone.

Tooling: `data_source_feasibility_audit.py` (what data expansion is possible),
`ohlcv_csv_validation_harness.py` (generic CSV loader/resampler),
`eth_independent_data_validation.py`, `btc_independent_data_validation.py`
(run the existing diagnostics against built-in or user-supplied CSV data),
`data_integrity_audit.py` (gap/seam/cross-source checks),
`eth_failure_to_separate_exit_diagnostic.py` (baseline vs fixed K=2 exit, both datasets),
`eth_third_source_validation.py` (same fixed rule on a third venue — §0.3),
`normalize_external_ohlcv.py` (normalize CryptoDataDownload / Binance-Vision CSVs),
`download_binance_vision_klines.py` (fetch + combine Binance Vision spot/futures klines).

### 0.2 Data-source plan

**Validation data expansion is the priority** — see `docs/DATA_DOWNLOAD_GUIDE.md`
for step-by-step instructions.

- **Layer 1 (now): independent OHLCV validation.** Use **Binance public data**
  (`data.binance.vision`) or **CryptoDataDownload** Binance BTCUSDT/ETHUSDT
  CSVs. Start with **BTCUSDT/ETHUSDT spot**. Normalize every file, then run the
  CSV harness + independent-data validators.
- **Layer 2 (later): perp-specific validation** (especially BTC) — Binance
  USD-M futures klines, funding, OI, and liquidation/sweep structure. Not in
  scope yet; scaffold only if trivial and without changing strategy logic.
- **Normalized CSV schema (exact):** `timestamp,open,high,low,close,volume`
  (UTC timestamps, ascending, de-duplicated, no silent forward-fill).
- **Provider notes:** avoid **Kraken REST OHLC** for now (it returns only a
  shallow recent window — bad for deep multi-year retrieval). Built-in
  **Coinbase** data remains usable but is constrained by candle bucket/
  pagination and a finite served history (~1460d).
- **Do not add new strategy logic while data validation is underway.**

### 0.3 Third-venue validation plan (current next step)

**Goal: run the EXACT SAME fixed ETH rule (strict breakout + K=2 / +0.5R
failure-to-separate exit) on UNSEEN venue/instrument data.** The goal is **not**
to improve results — it is to see whether the same un-tuned rule holds on data
it was never derived from. Do NOT tune K, do NOT sweep, do NOT change the rule.

Validation-source priority:
1. **Kraken ETH/USD spot** — if practical to download deep historical OHLCV(T).
   Kraken REST OHLC is shallow (recent only); prefer a bulk CSV export
   (Kraken's downloadable historical data) if available.
2. **Binance ETHUSDT USD-M futures/perp** — easiest path, since Binance Vision
   publishes futures klines (`download_binance_vision_klines.py --market
   futures`) with the same schema; lets us test spot-vs-perp on the same venue.
3. **Any other clean exchange CSV** (Bitstamp, OKX export, CryptoDataDownload)
   if Kraken is difficult.

Tool: `eth_third_source_validation.py` runs baseline vs the fixed exit on the
third source. Prefer **matching-venue BTC/ETH/SOL** context (strict regime needs
BTC + RS). If SOL is unavailable on that venue, the script states clearly that
it is a **partial-context** run (or cannot replicate strict regime) — venues are
never silently mixed. A real effect should hold on this third, unseen source
too; a one-source-only effect is noise.

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

- **ETH long breakout under strict BTC risk-on — PRIMARY ACTIVE RESEARCH LEAD.**
  ETH-USD long-only, 6H, strict-regime breakout. The only pocket with positive
  expectancy so far, but **under-sampled** and concentration-sensitive:
  - 6H: ~13 trades, PF ≈ 2.13, avg R ≈ +0.50, net ≈ +$32.93; still positive
    without the best trade, but **below the 30-trade gate** and top-trade
    concentration still matters.
  - 4H: ~16 trades, PF ≈ 1.43, avg R ≈ +0.26 — **weakened the edge**; 4H does
    **not** replace 6H.
  - 12H / 1D: **confirmed quality but reduced sample size** (fewer trades).

  **Not deployable yet** (trade count below the sample gate). **Current priority
  is diagnostics, not new entries.** ETH **pullback continuation is rejected**
  (§1) and must **not** be combined with the breakout model.

  **ETH goal (tightened):** increase sample size through **market occurrence and
  robust validation**, NOT by weakening setup quality. Do not loosen the entry
  to manufacture trades.

  **ETH research focus areas (diagnostic only — `eth_quality_audit_v2.py`):**
  - time-to-expansion (how soon +0.5R / +1R is reached)
  - **breakout acceleration quality** — how fast price expands after the
    breakout; whether strong winners *separate quickly* from entry; whether
    *slow* trades correlate with failure
  - distribution stability (top-trade / top-3 / best-month concentration,
    ex-best, rolling drawdowns)
  - ETH/BTC relative strength before entry
  - BTC regime strength before entry
  - volatility / chop state (ATR ratio, range compression)
  - early-failure behavior (did adverse move come before any favorable move)

  **Latest audit (`eth_quality_audit_v2.py`) — still NOT deployable:**
  - 13 trades, **8 winners** (fails ≥30 trades and ≥12–15 winners gates).
  - Concentration warnings: top trade = **51.6%** of net, best month = **51.6%**
    of net, top-3 trades = **131.8%** of net.
  - Best *relative* profile of any model: PF 2.13, avg R +0.504, **ex-best PF
    1.55**, ex-best avg R +0.263, and **OOS PF > IS PF**.
  - **Most useful clue — "failure to separate":** winners reached **+0.5R
    before meaningful (0.5R) MAE in 6/8** cases; losers in **0/5**. This is
    **hypothesis-generating only — do NOT convert it into a trading rule yet.**

  **Independent-data result (Binance ETHUSDT spot, ~6.3y; `eth_independent_data_validation.py`,
  label binance_eth_spot) — robustness did NOT persist:**
  - 16 trades, 8 winners. PF **2.13 → 1.33**, avg R **+0.504 → +0.198** (at the
    gate edge).
  - **Ex-best PF 1.55 → 0.97 and ex-best net = −$1.52.** Top trade = **110% of
    net** → remove the single best trade and the strategy is **net-negative**.
    The ETH strict-breakout strategy **FAILED the ex-best robustness check** on
    independent data — it is one-trade-dependent, weaker not stronger.
  - Year-inconsistent: 2024 −$10.4 (PF 0.54) vs 2025 +$30.5 (PF 2.77).
  - **BUT the failure-to-separate signature DID replicate independently:** fast
    starters (+0.5R within 2 bars) avg R **+1.04 / 88% win**; slow starters
    **−0.65 / 12% win**; winners reached +0.5R-before-MAE **6/8**, losers
    **1/8**. *Caveat:* "fast starter" is measured AFTER entry → it explains
    win/loss, it is **not** a tradeable filter (no look-ahead edge).
  - Caveat: only ETH came from Binance; BTC/SOL regime/RS context still came
    from the built-in Coinbase cache (not fully independent).
  - **Takeaway: the ETH *strategy P&L* is fragile/one-trade-dependent across
    sources; the *behavioral phenomenon* persists. Still NOT deployable.**

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
passive pullback, but remains **under-sampled and fragile** — a **SECONDARY
research lead only**, not deployable.

**Framing:** BTC compression behaves like a **volatility-release / volatility-
event model**, NOT a normal directional trend-continuation model. It fires when
BTC exits a volatility squeeze; its character is event-driven (vol expansion),
which is why sample is naturally low and outcomes are clustered.

**Why it is interesting (vs the rejected pullback):**
- better PF and drawdown than the pullback model
- better MAE behavior (losers don't bleed as far before resolving)
- aligns with BTC's compression→expansion volatility behavior, not with passive
  dip-buying

**Warnings (all currently true — do NOT advance on PF alone):**
- fewer than 30 trades (sample gate fails)
- too few winning trades (winner-count gate fails)
- top-trade dependence
- ex-best collapse (ex-best PF ≈ 1.04)
- weak / unstable OOS (730-day smoke weakened to PF 0.87)
- **NOT deployable**

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
- **EMA200 / EMA50-slope HTF-alignment filters are REJECTED / not useful for now.**
- **BTC compression remains a SECONDARY research lead only — NOT deployable**
  (<30 trades, too few winners, ex-best fragility).

**Next BTC hypotheses (each tested separately, no stacking, no tuning):**
1. **Full compression only (comp_count == 3)** — `btc_full_compression_diagnostic.py`
   and `btc_compression_next_diagnostic.py`. Expect a *very* small sample;
   treat any gain as indicative only.
2. **One simple early-failure exit** — a single deterministic rule (e.g. exit if
   price closes back inside the prior compression range). Tested in
   `btc_compression_next_diagnostic.py`. One rule only; do not compare many exits.
3. **Liquidity sweep + reclaim** (later) — separate structural hypothesis.
4. **Perp data (later)** — funding rate, OI delta, liquidation clusters.

**Latest results (`btc_compression_next_diagnostic.py`) — none deployable:**
- **Full compression only (comp_count == 3):** cleaner shape (9 trades, 6
  winners, PF 6.49, ex-best PF 4.41, top trade 37.9%) — but **extremely
  under-sampled. Promising as a HYPOTHESIS only, not proof.**
- **Baseline + early-failure exit** (exit on close back inside the prior
  compression range) **improved shape:** PF 1.50 → **1.88**, net +$14.15 →
  **+$19.91**, ex-best PF 1.04 → **1.31**, losers' MAE 0.56R → **0.34R**. Still
  **NOT deployable:** 21 trades, **8 winners**, month-dependent, top-trade
  dependent.
- BTC compression stays a **volatility-release research lead**, not a
  trend-continuation model. BTC pullback stays **rejected**.

**Independent-data result (Binance BTCUSDT spot, ~6.3y; `btc_independent_data_validation.py`,
label binance_btc_spot) — behavior persisted, rarity is the blocker:**
- 12H full-compression: **15 trades** (9 winners) over 6.3 years, PF 3.09,
  avg R +0.26, ex-best PF **2.35**, top trade **35.3%**, **OOS PF 4.31 > IS PF
  2.54**, losers MFE 0.19R / MAE 0.48R. The *shape* (ex-best holds, OOS exceeds
  IS, low concentration, controlled losers) is **consistent with Coinbase** —
  the behavior replicated across two independent exchanges.
- **Decisive finding:** even with an independent source AND ~2 extra years,
  full-compression still yields only **15 trades (~2.4/yr)** — it **fails the
  ≥30-trade and ≥12–15-winner gates**. **More/longer/independent data did NOT
  fix the rarity; the rarity is STRUCTURAL.** Net ≈ $19 on a $500 account over
  6 years — economically marginal regardless.
- 1D full-compression: 3 trades (ex-best PF 0.00) — too thin to read.
- **Takeaway: behaviorally validated, but likely UN-validatable at 12H because
  the occurrence rate is irreducibly low. Not deployable. Not a tuning problem.**

**BTC strategy-complexity work is PAUSED (see §0).** No new BTC variants,
filters, or exits. The 12H full-compression result (9 trades, 6 winners) is
**too small to validate or advance** — promising hypothesis, not proof. Next
BTC progress requires **independent validation data, not more logic.**

**Future BTC data wishlist (to enable independent validation):**
- additional exchange candles (cross-venue confirmation)
- longer BTC historical periods if available
- funding rates
- open-interest delta
- liquidation / sweep structure
- session / liquidity-regime data

## 5. Deployment gates / global validation rules (must ALL pass on real data)

> **Core philosophy:** No strategy advances on **profit factor alone.**
> Robustness, distribution stability, ex-best behavior, minimum winner count,
> regime consistency, and OOS preservation are equally important.

A setup is **not deployable** until it clears **every** gate:

1. **≥ 30 trades** (sample sufficiency).
2. **≥ 12–15 winning trades** (winner-count gate).
3. **Profit factor ≥ 1.3.**
4. **Avg R > +0.2.**
5. **Positive expectancy WITHOUT the single best trade** (no single-trade dependence).
6. **Reasonable max drawdown** (< ~20–25%).
7. **Not dependent on one year, one month, or one outlier trade.**
8. **No single month contributes more than ~40–50% of total net profit.**
9. **OOS PF must not collapse** relative to IS PF.
10. **Distribution stability checked** before paper trading (top-trade / top-3 /
    best-month concentration, ex-best, rolling drawdowns).
11. **Regime segmentation required** for all future diagnostics — segment by
    **bull / bear / range-chop** (and BTC risk-on/off, volatility state) when
    possible.

**Persistence caveat:** a historical edge does **not** imply persistence.
Future validation must monitor for **regime drift and structural decay** — a
setup that passed once can stop working.

- **No live trading.** Ever, in this phase.
- **No paper trading** until a candidate clears the research gates above.
- Passing the gates only qualifies a setup for **paper trading** — it does
  **not** authorize live trading.

## 5.1 Research phase vs Validation phase

**Research phase (where we are now).**
- *Allowed:* diagnostics, descriptive analysis, hypothesis generation,
  single-hypothesis testing.
- *Not allowed:* deployment, live trading, paper trading, parameter tuning to
  force profitability, combining many new filters at once.

**Validation phase** — entered **only after** a candidate clears the research
gates (sample, winner-count, robustness, distribution stability, regime
segmentation, OOS preservation). Only then:
- paper **signal logging** (no orders)
- slippage validation
- fee validation
- funding validation
- execution-quality validation

## 5.2 No feature stacking

**Do not combine multiple new filters simultaneously unless each has first been
tested independently.** Avoid "overfit soup" from stacking ETH/BTC strength +
volatility state + chop filter + session filter + acceleration filter + … all at
once. One hypothesis at a time; measure it alone; only then consider combining.

## 6. Diagnostics index

| Tool | Purpose |
|---|---|
| `backtest.py` | Canonical 6H engine + `--setup` (breakout/pullback/both), `--strict-regime`, `--compare-regime`, `--trade-assets`, `--long-only`. Default behavior unchanged. |
| `eth_htf_diagnostic.py` | ETH breakout across 6H / 12H / 1D. |
| `eth_4h_diagnostic.py` | ETH breakout at 4H (aggregated from 1H). |
| `eth_pullback_diagnostic.py` | Breakout vs pullback-continuation vs combined (tagged by `setup_type`). |
| `eth_breakout_quality.py` | Winner-vs-loser feature audit for ETH breakouts. |
| `eth_quality_audit_v2.py` | ETH 6H breakout audit v2: distribution stability, time-to-expansion / acceleration, regime segmentation, monthly tables. |
| `eth_candidate_stability_audit.py` | ETH 6H breakout stability: walk-forward splits, Monte-Carlo order randomization, failure-to-separate. |
| `eth_persistence_feasibility_audit.py` | ETH 6H breakout persistence across segments (year/quarter/regime/vol/RS); occurrence-expansion feasibility. |
| `btc_diagnostic.py` | BTC trend-continuation pullback, 12H/1D (rejected — see §1.1). |
| `btc_compression_diagnostic.py` | BTC compression → expansion breakout, 12H/1D (§4.1 hypothesis 1). |
| `btc_compression_quality.py` | Winner-vs-loser feature audit for BTC compression breakouts. |
| `btc_htf_alignment_diagnostic.py` | BTC compression + EMA200 / slope HTF filters (rejected — §4.1.1). |
| `btc_full_compression_diagnostic.py` | BTC compression baseline vs comp_count==3. |
| `btc_compression_next_diagnostic.py` | BTC compression: baseline / full / +early-failure-exit variants. |
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
