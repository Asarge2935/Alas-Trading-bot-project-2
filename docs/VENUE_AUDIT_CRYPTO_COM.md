# Venue Audit — Crypto.com Exchange

**Date:** 2026-05-11
**Status:** Phase 1 (read-only public-API verification) complete.
Phase 2 (authenticated probes from the user's account) **not yet done** — required before any commitment.
**Recommendation up front:** *conditional go.* Crypto.com Exchange is
viable as primary execution venue **only if** the three blocker items
in [§7 Open blockers](#7-open-blockers) clear. If any blocker fails,
fall back to spot-only on Crypto.com plus a separate perp venue for
derivatives.

---

## 1. Purpose

Verify whether Crypto.com Exchange can serve as the primary data and
execution venue for the bot's derivatives strategies, against the
audit checklist agreed in the research roadmap.

This document records: what was verified, how it was verified, what
is still unverified, and the go/no-go decision rule.

---

## 2. Audit checklist — results

Legend: ✅ verified live  ·  📄 docs only  ·  ⚠️ conditional / qualified  ·  ❌ failed  ·  ❓ unverified

| # | Capability | Result | Evidence |
|---|---|---|---|
| 1 | REST API exists, public endpoints reachable | ✅ | Live calls to `public/get-instrument`, `public/get-candlestick`, `public/get-book`, `public/get-ticker` succeeded against `BTCUSD-PERP`. |
| 2 | WebSocket API exists | 📄 | Documented in Exchange v1 API. Not probed in Phase 1 (no live WS test yet). |
| 3 | Perpetual futures listed and tradable | ✅ | `BTCUSD-PERP` returned `inst_type: PERPETUAL_SWAP`, `tradable: true`, `max_leverage: 100`, USD-quoted, BTC-base, USD-INDEX underlying. |
| 4 | Native 4H candles | ✅ | `get_candlestick(BTCUSD-PERP, 4h)` returned valid OHLCV. |
| 5 | Native 12H candles | ✅ | `get_candlestick(BTCUSD-PERP, 12h)` returned valid OHLCV. |
| 6 | Native 1D candles | 📄 | Documented; not probed (low risk — the API clearly exposes a `timeframe` parameter and 4h/12h both worked). |
| 7 | Order book / depth via REST | ✅ | `get_book` returns bids/asks with depth parameter. |
| 8 | Top-of-book and open interest in ticker | ✅ | Live ticker on BTCUSD-PERP exposed `best_bid`, `best_ask`, sizes, `open_interest`, `volume`, `volume_value`. |
| 9 | Funding rate via REST | 📄 | Exchange v1 API exposes `public/get-valuations` with valuation types `funding_rate` and `estimated_funding_rate`. Not yet probed live — needs verification of historical depth. |
| 10 | Funding rate streaming via WS | 📄 | Documented (candlestick, funding rate, estimated funding rate subscriptions). |
| 11 | Isolated margin supported on perps | 📄 | `private/create-order` accepts `isolation_id`, `leverage`, `isolated_margin_amount`; `exec_inst` supports `ISOLATED_MARGIN`. |
| 12 | API keys default to read-only | 📄 | Help-center docs state defaults are "Can Read"; trade and withdrawal are separately enabled. |
| 13 | API keys can be created with withdrawals disabled | 📄 | Yes — withdrawal is a separate, opt-in permission. |
| 14 | IP allowlist on API keys | 📄 | Required if trading or withdrawal enabled. |
| 15 | Sandbox / paper environment for retail | ⚠️ | A UAT environment exists but is **invitation-only for institutional accounts** per current docs. No public sandbox confirmed. **This is a real gap.** |
| 16 | US perpetual eligibility on the main Exchange | ❌ | Perps on the main `exchange-docs.crypto.com` API are **not available to US, Canada, Australia, Hong Kong, and most EU customers.** |
| 17 | US-eligible alternative (CDNA) | ⚠️ | Crypto.com Derivatives North America (CFTC-registered) received DCM amendment approval Sep 2025 to offer US crypto perps. **Different product, different API, different account** from the international Exchange. API parity is unverified. |
| 18 | Account/jurisdiction match | ❓ | The user has not confirmed which region their existing Crypto.com account is in, nor whether their balance is on the **Exchange** (international, derivatives-capable in eligible regions) or the **App** (retail spot, no API for perps). This must be answered before further work. |

---

## 3. What was actually probed

The following live calls were made to public, unauthenticated
endpoints and returned valid data:

```
GET public/get-instrument         instrument_name=BTCUSD-PERP
GET public/get-candlestick        instrument_name=BTCUSD-PERP, timeframe=4h
GET public/get-candlestick        instrument_name=BTCUSD-PERP, timeframe=12h
GET public/get-book               instrument_name=BTCUSD-PERP, depth=20
GET public/get-ticker             instrument_name=BTCUSD-PERP
```

Sample evidence captured from the live response:

- BTCUSD-PERP instrument: `PERPETUAL_SWAP`, max leverage 100, price
  tick 0.1, qty tick 0.0001, tradable true.
- 4H candles: ~50 bars returned per request, OHLCV plus quote-volume.
- 12H candles: ~50 bars returned per request, same schema.
- Ticker exposes open interest (6241.5538 BTC at probe time) and
  24h notional volume — enough for a liquidity filter.

The MCP tool used returns ~50 candles per request. **For historical
backtesting we will need the raw REST endpoint with explicit
`start_ts`/`end_ts` pagination** — the MCP wrapper is not sufficient
for a multi-year pull. This is normal; flag it for the data-layer
phase, not a blocker.

---

## 4. What is documented but not probed

These are taken from current docs. They are not as trustworthy as the
live probes above and must be verified before relying on them:

- WebSocket API (subscriptions: ticker, book, candlestick, funding
  rate, estimated funding rate, trades, user.order, user.position).
- `public/get-valuations` for funding-rate history. Critical question:
  **how far back does funding history go, and at what granularity?**
- Authenticated endpoints for positions, balances, order placement.
- Isolated-margin semantics (margin call, liquidation thresholds,
  cross-contamination risk if the same wallet holds spot).
- Rate limits under sustained load with a 20+ symbol universe.

---

## 5. What is unverified and account-specific

These items cannot be answered from public data — they require the
user's account:

1. **Account type.** Crypto.com runs (at least) three distinct
   products with three distinct APIs:
   - **Crypto.com Exchange** (international, this audit's target).
     Perps available in eligible regions only.
   - **Crypto.com App** (retail spot wallet). No perp API.
   - **Crypto.com Derivatives North America (CDNA)** (US-eligible
     perps, CFTC-registered). Separate API surface, separate KYC,
     separate funds.
   The user's existing balance may be on **any of these three**, and
   they are not fungible without manual transfer.
2. **Region eligibility for derivatives.** If the user is in the US,
   Canada, AU, HK, or most of the EU, the international Exchange perp
   API is closed to them regardless of what the docs say.
3. **Sandbox access.** No public retail sandbox is documented. We
   need to either (a) get UAT access (unlikely without an institutional
   account), or (b) build our own paper-trading simulator on top of
   live market data (recommended fallback).

---

## 6. Cost / liquidity sanity check

Not a blocker, but worth recording so it isn't forgotten:

- BTCUSD-PERP 24h notional volume at probe time: ~$495M.
  Comfortably deep for retail-sized orders.
- Open interest: ~6,242 BTC (~$500M notional at probe price).
- Top-of-book spread observed: $0.10 on a ~$80,927 mid → ~0.0001%.
  This is execution-friendly **for BTC**. Spreads on alts will be
  wider and must be measured per symbol during data collection.
- Maker/taker fees: not captured in this audit. **Must be confirmed
  for the user's specific fee tier before any cost-aware backtest.**

---

## 7. Open blockers

These three must clear before Crypto.com is committed as primary
execution venue.

### Blocker A — Account / region eligibility
**Question for the user:** Which Crypto.com product does the existing
balance sit on (Exchange / App / CDNA), and which region is your KYC
registered in? If you cannot trade perps from your jurisdiction on
the international Exchange, this whole branch of the design has to
change.

### Blocker B — Sandbox / paper-trading path
There is no confirmed public retail sandbox. We need a decision:
- **Option 1:** Apply for UAT (unlikely for a retail account).
- **Option 2:** Build a local paper-trading simulator that consumes
  live market data via the public WS feed and simulates fills against
  the live order book. **Recommended.** This is also a better test of
  execution realism than most exchange sandboxes, which often have
  thin or fake liquidity.
- **Option 3:** Start live with $50–$100 of risk capital only,
  skipping paper. **Not recommended** until backtest gates pass.

### Blocker C — Funding-rate history depth
We need to confirm `public/get-valuations` returns ≥ 2 years of
funding history per instrument before relying on funding-aware
strategy variants. If history is shallow, we either start collecting
forward immediately and accept a 6-month wait before funding-aware
backtests, or source historical funding from a third party.

---

## 8. Decision rule

```
IF  Blocker A clears (user can legally trade perps on Crypto.com Exchange)
AND Blocker B has a chosen path (sandbox OR local simulator OR tiny-live)
AND Blocker C clears or has an accepted workaround
THEN Crypto.com Exchange = primary execution + data venue.
     Coinbase = read-only confirmation feed.

ELSE IF user cannot trade perps on Crypto.com Exchange
THEN Crypto.com = spot-only data/execution
     Perp execution = open question — re-evaluate Coinbase US perps,
     CDNA, or other CFTC-registered venues.
     The strategy roadmap stays the same; only the execution layer
     changes.
```

---

## 9. Recommended next actions (in order)

1. **User answers Blocker A.** One sentence: jurisdiction + which
   Crypto.com product holds your capital.
2. **Authenticated probe.** Once we know the right product, generate a
   read-only API key (no trade, no withdrawal, IP-restricted) and run
   a minimal authenticated probe: `private/user-balance`,
   `private/get-positions`, `private/get-fee-rate`. Confirms account is
   live and fee tier.
3. **Funding-history depth check.** Single call to
   `public/get-valuations` with the widest time window allowed; record
   how far back the data actually goes.
4. **Decision recorded** in this file under §10 (to be added).
5. **Only then** start the data layer (historical pulls, WS collector).

Do **not** start strategy implementation, paper-trading harness, or
live keys with trade permission until §10 records a green decision.

---

## 10. Decision — recorded 2026-05-11

**User confirmed:** logs in via the Crypto.com mobile App (blue
diamond logo). Inside the account, the available non-spot products
are **"Up/Down options"** and **event contracts** (prediction
markets). **No perpetual futures are visible.**

This places the account squarely on **Crypto.com App (US retail)**.
The international Exchange v1 API audited in §1–§9 of this document
is **not accessible to this account.** There is no perp product on
the App for this user.

### What this account can and cannot do

| Capability | Status on this account |
|---|---|
| Spot trading on USD pairs | ✅ Available via the App. |
| Perpetual futures | ❌ Not available. |
| Margin / leverage | ❌ Not available on this product. |
| Shorting | ❌ Not available except via the indirect / unsuitable products below. |
| Up/Down options | ⚠️ Available, but **not suitable** for the strategies in the roadmap (see warning below). |
| Event contracts (prediction markets) | ⚠️ Available, but **not suitable** for systematic edge-discovery (see warning below). |
| Funding rates | ❌ N/A — no perps on this account. |
| Isolated margin | ❌ N/A. |
| Public REST/WS data feed | ✅ Available to anyone, account-independent. We can still use the Exchange v1 public feed for free market data. |

### Honest warning on Up/Down options and event contracts

It is tempting to use these as a substitute for perps. **Do not.**

- **Up/Down options** on Crypto.com are very-short-duration
  directional payoffs (typically minutes to a few hours). They are
  priced with a substantial house spread/skew that creates a
  built-in negative expected value before any view is expressed.
  They reward correctness of *direction* but not magnitude, which
  is the opposite of the strategies in this project (which earn
  most of their P&L from the size of moves, not just direction).
  Systematic use of binary-style payoffs against a house spread is
  almost always EV-negative for the trader.
- **Event contracts** are CFTC-regulated prediction markets with
  fixed-payoff structures. They are designed for views on discrete
  outcomes, not for continuous-exposure trading strategies. They
  are also typically thinly-resolved (one tick on resolution),
  which makes them unsuitable for any strategy that depends on
  intra-position management.
- Neither product is a viable execution venue for the strategies in
  the research roadmap. If the project ever wants directional
  short exposure, it must come from a real perp/futures venue or be
  abandoned.

### Updated decision

```
Crypto.com (App) = SPOT-ONLY data and execution candidate.
                   Subject to a follow-up audit of the App's actual
                   programmatic API surface (separate doc, not done).

Crypto.com Exchange v1 public REST/WS = read-only market data.
                   Anyone can hit it. Useful as a second data source
                   for cross-venue confirmation against Coinbase et al.
                   Cannot be used for execution from this account.

Perp / short / leverage track = DEFERRED.
                   Requires a separate venue. Candidates to evaluate
                   later: Coinbase Advanced INTX perps (US, CFTC),
                   Kraken Pro US futures, CME micro-bitcoin/ether,
                   Bitnomial, CDNA (if/when GA in Ohio).
                   No perp work proceeds until a venue is chosen
                   AND audited the same way Crypto.com was.
```

### Implications for the strategy roadmap

- **Strategy 1 (Regime + Relative Strength rotation):** unchanged.
  It is spot-only and long-only by design. Proceeds as specified
  in `STRATEGY_1_REGIME_RS_SPEC.md`. The Crypto.com App may
  ultimately serve as its execution venue, pending the App-API
  audit.
- **Strategies 2–4 from the roadmap (Volatility expansion,
  Liquidation flush, Failed breakout):** all originally specified
  as needing perps for short side and/or leverage. They are now
  on hold pending venue resolution. They can be **researched and
  backtested** without execution access — but no live execution
  plan exists for them yet. We will not invest in their
  implementation until a perp venue is audited and committed.
- **Cross-venue confirmation:** unchanged. We use Coinbase and the
  Crypto.com Exchange public API as read-only confirmation feeds.

### Next concrete steps

1. **Audit the Crypto.com App API surface** (new document). Verify
   what programmatic access actually exists for a US App account:
   what endpoints, what auth, what permission scopes, whether
   withdrawals can be disabled on a key, whether IP allowlist is
   supported, what rate limits, whether there is a sandbox at all.
   This is the equivalent of §1–§9 of this document, but for the
   App. Until this is done, treat App as "data source viable,
   execution viability unknown."
2. **Proceed with Strategy 1 spec → data layer → backtester** in
   parallel. No venue access required for any of this.
3. **Defer the perp venue decision** until Strategy 1 produces a
   verdict. There is no point picking a perp venue for strategies
   that don't yet exist.

---

## 11. US (Toledo, Ohio) findings — added 2026-05-11

User confirmed: jurisdiction is **United States, Ohio**. They have
funds on "Crypto.com" but have not confirmed which product. We do not
assume.

### What the public record says about Crypto.com in the US

There are **three distinct Crypto.com products** for a US user, and
they are not interchangeable:

| Product | URL pattern | US availability | API for bot use |
|---|---|---|---|
| **Crypto.com App** (retail) | `crypto.com/us/app`, mobile app | Yes, in most US states including Ohio | Limited. The international Exchange v1 API documented above is **not** the App's API. App's programmatic surface is much narrower; no perps. |
| **Crypto.com Exchange** (international, the API audited in §1–§9) | `exchange.crypto.com` | **No.** Wound down US institutional access on 2023-06-21. US retail was never on it. | Full v1 API exists but is **not accessible to a US-resident account.** |
| **Crypto.com Derivatives North America (CDNA)** | separate signup; CFTC-regulated entity | Yes, in eligible states (Ohio status: needs confirmation per CDNA terms). Margined perps approved by CFTC 2025-09-26. | Separate API surface from international Exchange. **Parity with Exchange v1 API not confirmed.** |

### Implication

The most likely state of the world for this user is:

> Funds are on **Crypto.com App** (US retail spot). The international
> Exchange v1 API audited in this document **does not apply to their
> account.** Without a separate CDNA signup and KYC, perpetual
> futures execution on Crypto.com is not available.

This is not yet confirmed — the user has been asked to verify (see
§12). But it is the base-rate assumption.

### Consequences if confirmed

- The 4H/12H native candle convenience and the perp execution path
  documented in §1–§9 **are not usable** with a US-App account.
- Strategy work that depends on perps (shorts, leverage, funding-rate
  signals) cannot execute on the user's existing balance.
- Strategy work that is **spot-only** (long basket of alts during BTC
  risk-on, otherwise flat or rotated to USDC) can execute on the App,
  subject to confirming what API surface the App actually exposes
  (this is a follow-up audit, not done yet).
- For the perp track to be viable later, the user would need to:
  1. Open a separate CDNA account (and pass its KYC, and confirm Ohio
     state eligibility for that product), **or**
  2. Use a different US-eligible perp venue (Coinbase Advanced INTX
     perps, Kraken Pro US futures, Bitnomial, etc.).

### Updated decision rule

The original §8 rule still holds. With the US/Ohio fact added, the
likely branch is the `ELSE` clause:

> Crypto.com (App) = spot data and possibly spot execution.
> International Exchange API = read-only public data only (anyone can
> hit it without an account).
> Perp execution = re-evaluate as a separate audit.

We are still in **conditional pending** until the user completes §12.

---

## 12. Self-verification procedure for the user

I cannot check your account from here. Please run these read-only
checks yourself. **Do not create any API keys yet.** All steps below
are read-only and risk-free.

### Step 1 — Identify which Crypto.com product holds your funds

Open whichever Crypto.com app or website you normally use to see your
balance, and answer these:

1. **Where do you log in?**
   - Mobile app called "Crypto.com" with a blue diamond logo, or
     `crypto.com/us` website → almost certainly **App (US retail)**.
   - `exchange.crypto.com` website → **Exchange (international)**.
     Should not be possible from a US account; if it works, document it.
   - Any URL containing `cdna`, `cryptocom-derivatives`, `nadex`,
     or a separate "Derivatives" account → **CDNA**.

2. **Inside the account, what wallet types do you see?**
   - Just "Crypto Wallet" / "Fiat Wallet" → App.
   - "Spot Wallet" + "Margin Wallet" + "Derivatives Wallet" → Exchange.
   - "Futures" or "Perpetuals" with "Initial Margin" / "Maintenance
     Margin" terminology → likely CDNA.

3. **Can you see a perpetual futures product?** If you cannot find any
   perp trading screen at all in your account, you do not currently
   have perp access regardless of product.

Report back the answers to those three. That alone resolves Blocker A.

### Step 2 — Do NOT do these yet

- Do not generate any API key.
- Do not enable trading or withdrawal permissions on any key.
- Do not transfer funds between Crypto.com products.
- Do not sign up for CDNA "just to see" — each new account is a fresh
  KYC and a tax/reporting consideration.

### Step 3 — What I'll do once you answer

Based on your answer:

- **App only:** I update §10 to "no Crypto.com perp execution
  available." Strategy work continues on the spot-only Strategy 1
  (already venue-agnostic). I open a follow-up audit for the App's
  actual API surface (separate doc).
- **Exchange (somehow):** I update §10 to a green decision contingent
  on Blockers B and C, and we proceed with the read-only authenticated
  probe.
- **CDNA:** I open a new audit document for CDNA specifically, since
  it is a different API and different docs from the international
  Exchange.


---

## Appendix A — Sources

- Crypto.com Exchange v1 API: <https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html>
- Crypto.com Derivatives API (legacy v1): <https://exchange-docs.crypto.com/derivatives/index.html>
- API help-center article (key permissions): <https://help.crypto.com/en/articles/3511424-api>
- Derivatives geo-restrictions: <https://help.crypto.com/en/articles/4894470-derivatives-trading-geo-restrictions>
- CDNA (US perps) approval announcement: <https://crypto.com/us/company-news/cryptocom-becomes-first-major-crypto-platform-to-obtain-a-full-stack-of-cftc-derivatives-licenses>

## Appendix B — Live probe evidence (summary)

```
BTCUSD-PERP instrument:
  inst_type:       PERPETUAL_SWAP
  tradable:        true
  max_leverage:    100
  price_tick_size: 0.1
  qty_tick_size:   0.0001
  quote_ccy:       USD
  underlying:      BTCUSD-INDEX

BTCUSD-PERP ticker (2026-05-11T05:25Z):
  last:           80925.5
  best_bid/ask:   80927.0 / 80927.1   (spread 0.0001%)
  open_interest:  6241.5538 BTC
  24h volume:     6090.5119 BTC (~$495M notional)

Native timeframes verified by live probe: 4h, 12h
Native timeframes documented but unprobed: 1m, 5m, 15m, 30m, 1h, 1d, 7d, 14d, 1M (typical for this API)
```
