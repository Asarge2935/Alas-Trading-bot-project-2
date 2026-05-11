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

## 10. Decision — pending

To be filled in once Blockers A/B/C resolve.

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
