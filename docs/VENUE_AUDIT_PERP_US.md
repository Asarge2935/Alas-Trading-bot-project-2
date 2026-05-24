# Venue Audit — US Perp/Futures (Coinbase vs Crypto.com CDNA)

**Date:** 2026-05-11
**Status:** Phase 1 (public-record review). No authenticated probe yet
— that needs an account/key from the user and is deferred to Phase 2.
**Recommendation up front:** **Proceed with Coinbase US
Perpetual-Style Futures.** It is CFTC-regulated, US-eligible,
small-account-friendly, capped at a sane 10x leverage, and has a
mature documented API. Crypto.com CDNA **cannot be confirmed** as a
usable US retail crypto-perp venue with an API from the public record;
hold it as a fallback only.

This is the venue track the user asked for after redirecting the
project to a long/short BTC strategy (which needs perps to execute).

---

## 1. Why this audit exists

The user's strategy (Strategy 2, BTC long/short) requires shorting
and therefore a perp/futures venue. Their existing Crypto.com **App**
account is spot-only (see `VENUE_AUDIT_CRYPTO_COM_APP.md`). To execute
the short side live, a US-eligible CFTC-regulated perp venue is
required. The user named two to evaluate: Coinbase and Crypto.com.

---

## 2. Side-by-side

| Criterion | **Coinbase US Perps** | **Crypto.com CDNA** |
|---|---|---|
| Legal entity / regulator | Coinbase Financial Markets, **CFTC-regulated** | Crypto.com Derivatives North America, **CFTC FCM/DCM/DCO** |
| US retail crypto perps live? | **Yes** — US Perpetual-Style Futures launched 2025-07-21 | **Unconfirmed.** CDNA holds the licenses; public record shows commodity/index perps and "margined derivatives," but a US-retail **Bitcoin** perp product with a usable API is not confirmed. |
| BTC product | nano BTC-PERP = **1/100 BTC** per contract | Unconfirmed for US retail |
| Min order size | **10 USDC notional** | Unknown |
| Leverage cap | **Up to 10x** | International Exchange is 50–100x, but that venue is **not US-accessible**; CDNA cap unknown |
| Margin | USDC-margined; isolated margin to verify | Retail restricted to **isolated margin** (good) — but on which product/region unclear |
| API | **Advanced Trade API** (REST + WS), perpetual-futures endpoints documented at docs.cdp.coinbase.com | International Exchange v1 API exists but is **not US-accessible**; CDNA API parity with it is **unverified** |
| Sandbox | Advanced Trade sandbox exists | Unknown / institutional-invite only (per earlier audit) |
| Fees | Retail **0.60% taker / 0.40% maker** (<$10k/mo); promo 0.03% taker if live; 0.05% only at >$400M/mo | Unknown for CDNA US |
| Fit for ~$500 account | **Excellent** — 1/100 BTC nano + 10 USDC min order | Unknown |
| Prior project experience | Coinbase data path already used in this repo | None |

---

## 3. Coinbase — detail

**Product.** "US Perpetual-Style Futures," CFTC-regulated via
Coinbase Financial Markets, launched 2025-07-21. No monthly expiry
(perpetual-style with a funding mechanism). Nano BTC-PERP is **1/100
of a BTC** per contract, with a **10 USDC minimum order notional** —
this is the single most important fact for a $500 account: position
sizing down to ~$10 is feasible, so risk-per-trade can be kept tiny.

**Leverage.** Up to **10x**. This is *lower* than offshore venues
(50–100x), which is a feature, not a bug — it aligns with the
project's "no high leverage" risk rule and makes catastrophic
liquidation harder.

**API.** The Advanced Trade API (REST + WebSocket) documents
perpetual-futures order placement, positions, and market data. Key
scopes (view / trade / transfer) and IP allowlisting are configurable
on Coinbase API keys — to be confirmed on the user's account in
Phase 2.

**Eligibility.** US-wide via the CFTC framework, but **state-level
availability must be confirmed** — some states (historically NY, and
a handful of others for derivatives) can differ. **Ohio is not on any
known exclusion list, but this must be verified at signup.**

**Costs — CORRECTED with the user's actual Coinbase fee schedule.**
This audit went through two wrong fee numbers before landing on the
real one. For the record:
- First draft: ~0.02% taker (wrong — that's a high-volume rate).
- Second draft: 0.60% taker (wrong — that is the Coinbase **spot**
  *maker* rate; I conflated spot with derivatives).
- **Correct, per the user's account:** the bot trades **derivatives
  (perps)**, and Coinbase derivatives fees at Intro 1 (current tier,
  <$10k 30-day volume/balance) are **0.095% maker / 0.100% taker**
  per contract, plus a **$0.15/contract minimum** and a **0.10%
  liquidation fee** if force-closed.

For contrast, Coinbase **spot** Intro 1 is 0.60% maker / **1.20%
taker** — ~12x the perp taker rate. The bot does not trade spot, so
the perp rate governs.

Why this matters and the nuances:
- Round-trip perp cost is ~**0.20% taker** + slippage, not the ~1.2%
  I previously feared. A trend strategy that trades ~weekly is very
  fee-survivable at this rate.
- The **$0.15/contract minimum** is negligible for nano BTC
  (~$800 notional → 0.10% = ~$0.80 > $0.15). It only bites on
  sub-$150 positions. Not modeled.
- The **maker/taker gap on perps is tiny** (0.095 vs 0.100), unlike
  spot. So routing limit orders to capture maker barely helps on
  perps — don't contort the strategy for it.
- **Liquidation fee** (+0.10%) is avoided by the stop + no-high-
  leverage rule. Not modeled.

Fee-sensitivity on the same synthetic 79-trade strategy:

> perp 0.10%: −12.9% · (wrong) 0.60%: −60.6% · spot 1.20%: −84.9%

The backtester now defaults to `--fee-preset perp_taker` (0.100%).
`perp_maker` (0.095%) and `spot_taker` (1.20%, for contrast only) are
also available.

**Tier ladder & "balance trick".** Coinbase tiers are set by 30-day
volume **or** total balance, whichever is higher. The detailed ladder
the user supplied is a **spot** ladder (Intro 1 0.60/1.20 → Intro 2 at
$10k 0.40/0.80 → … → VIP 8 at $250M 0.00/0.02). Because perp fees are
already only 0.10% at Intro 1, climbing tiers helps perps far less
than it helps spot. Reaching $10k balance for Intro 2 is a "someday"
consideration at a ~$500 account, and consolidating long-term holdings
onto Coinbase to hit it would conflict with the project rule that the
bot must never touch long-term spot holdings. **VIP status-match**
(submitting another venue's volume to fast-track a tier) requires
meaningful *trading volume* elsewhere, not holdings; Crypto.com spot
*holdings* would not qualify, and at this scale it is not worth
pursuing. Bottom line: the perp fee is already low; do not reorganize
your finances chasing a tier that mainly benefits spot.

Funding remains a flat 0.03%/day placeholder and still needs a real
BTC-PERP series.

**Coinbase One does NOT reduce perp fees.** The Coinbase One
subscription (Basic $4.99 / Preferred $29.99 / Premium $299.99) waives
fees on **simple spot trades only** (Basic up to $500/mo, Preferred up
to $10k/mo). Its 25% fee rebate on Preferred/Premium applies to
**Advanced spot** fees only. None of the tiers reduce perpetual-futures
or other derivatives fees. For the perp strategy, Coinbase One is
irrelevant — the perp fee is set by the Advanced derivatives tier
(0.60% retail / 0.03% promo), independent of membership. Do not
purchase Coinbase One expecting it to improve perp-bot profitability.
(Coinbase One Basic/Preferred *would* zero out spot fees for the
non-shorting `long_flat` spot variant, subject to the monthly volume
cap.) Confirm current terms on the user's account — these programs
change.

---

## 4. Crypto.com CDNA — detail

**What is confirmed.** CDNA is a CFTC-registered exchange and
clearinghouse with a full license stack (FCM, DCM, DCO), approved
2025-09-26 to offer margined derivatives including crypto perpetuals.
Retail users are restricted to isolated margin.

**What is NOT confirmed (and matters).**
- Whether a **US retail Bitcoin perpetual** is actually live and
  available to an individual in Ohio today.
- Whether CDNA exposes a **programmatic API** suitable for a bot, and
  if so, whether it resembles the international Exchange v1 API or is
  a separate surface.
- Minimum order size, fee tier, leverage cap for US retail.
- Sandbox availability.

The international Crypto.com Exchange perps I probed earlier
(`BTCUSD-PERP`, 100x) are **not accessible to US users** — that probe
used the public data feed, which anyone can hit, but trading is
geo-blocked. CDNA is a separate entity and the public record does not
yet establish a usable US-retail crypto-perp API.

**Conclusion on CDNA:** insufficient public evidence to recommend.
Not rejected outright — it may be a fine option once its US retail
crypto-perp product and API are confirmed — but it is **not the path
to start on.**

---

## 5. Recommendation and decision rule

```
Primary perp venue to pursue: COINBASE US Perpetual-Style Futures.

Reasons: confirmed US-eligible + CFTC-regulated, nano BTC (1/100) with
10 USDC min order (ideal for $500), 10x leverage cap (aligns with risk
rules), mature documented Advanced Trade API, low taker fees.

CDNA: hold as fallback. Revisit only if Coinbase eligibility fails for
Ohio, or if CDNA later publishes a confirmed US-retail crypto-perp API.
```

---

## 6. Open blockers (must clear before live, not before backtest)

### Blocker A — Ohio eligibility (user action)
The user confirms, at Coinbase signup / in their existing Coinbase
account if any, that **US Perpetual-Style Futures are available to an
Ohio resident**. This is a read-only check in the Coinbase UI — no
key, no funding required.

### Blocker B — API key scopes (user action, Phase 2)
Confirm a Coinbase API key can be created **view + trade only, with
transfer/withdrawal disabled, IP-allowlisted**. Generate a read-only
key first for an authenticated probe (balances, positions, fee tier).

### Blocker C — Real cost + funding data
The backtester now defaults to **0.60% retail taker** (the realistic
starting tier) and exposes `--fee-preset`. Still to confirm against
the user's actual Coinbase account: their real fee tier (and whether
the promo is active for them), and a real historical BTC-PERP funding
series to replace the 0.03%/day placeholder. **Until the funding
series is real and the fee tier is confirmed, no Strategy 2 verdict is
final** — but the retail-fee run is now a fair pessimistic baseline.

---

## 7. How this plugs into the build

1. **Now (no account needed):** keep refining Strategy 2 on the
   existing spot-BTC data. The signal logic is venue-independent. The
   long/flat variant remains spot-executable as a fallback.
2. **Phase 2 (user verifies Ohio eligibility):** authenticated
   read-only probe of Coinbase — fee tier, BTC-PERP instrument specs,
   funding history endpoint.
3. **Update cost model** in `btc_backtest.py` with real Coinbase
   numbers; re-run the gates. This is when the Strategy 2 verdict
   becomes real.
4. **If gates pass:** paper trade (Coinbase has a sandbox), then tiny
   live with isolated margin and a hard leverage cap wired into the
   risk layer.

No live orders, no leverage, no real trading until the gates pass on
**real** costs and the risk controls (isolated margin, max leverage,
stop, daily/weekly loss caps, kill switch) are implemented and tested.

---

## Appendix — Sources

- Coinbase: US Perpetual-Style Futures launch — <https://www.coinbase.com/blog/coming-july-21-us-perpetual-style-futures>
- Coinbase: Perpetual futures have arrived in the U.S. — <https://www.coinbase.com/blog/perpetual-futures-have-arrived-in-the-us>
- Coinbase US Perpetual-Style Futures 101 — <https://www.coinbase.com/learn/futures/us-perpetual-style-futures-101>
- Coinbase Advanced Trade Perpetual Futures API — <https://docs.cdp.coinbase.com/coinbase-business/advanced-trade-apis/guides/perpetual>
- Coinbase leverage & margin rates — <https://help.coinbase.com/coinbase/trading-and-funding/derivatives/futures-leverage-margin>
- Crypto.com full CFTC license stack (CDNA) — <https://crypto.com/us/company-news/cryptocom-becomes-first-major-crypto-platform-to-obtain-a-full-stack-of-cftc-derivatives-licenses>
- CFTC permits perpetual futures on BTC/ETH — <https://www.pillsburylaw.com/en/news-and-insights/cftc-perpetual-futures-btc-eth-crypto-derivatives.html>
