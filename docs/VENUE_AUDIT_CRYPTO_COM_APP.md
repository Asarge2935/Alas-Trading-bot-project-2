# Venue Audit — Crypto.com App (US retail)

**Date:** 2026-05-11
**Status:** Phase 1 complete (public-record review). No live probe
possible — the App's "API" (such as it is) requires an authenticated
key from the user's account. No keys have been created and none
should be created until the conclusions in §6 are accepted.
**Recommendation up front:** **App is not a viable programmatic
execution venue for this bot.** It is acceptable as a *manual*
execution venue for Strategy 1 (weekly rebalance ≈ 1–3 actions/week)
and as a balance-and-history data source. For automated execution,
the bot must use a different US-eligible spot venue. See §6.

---

## 1. Purpose

Determine whether the Crypto.com App (US retail; the user's confirmed
account type) exposes a programmatic API sufficient for the bot to
read account state and place orders automatically. This is the
follow-up audit promised in `VENUE_AUDIT_CRYPTO_COM.md` §10.

---

## 2. Scope distinction

There are several "Crypto.com APIs" in the public record. They are
not the same thing. This audit is **only** about the API surface
available to a US App account.

| API surface | Intended user | Available to US App account? |
|---|---|---|
| Exchange v1 REST/WS (`exchange-docs.crypto.com`) | International Exchange customers and brokers | **No.** Requires an Exchange account, which US users do not have. |
| Exchange Broker / OAuth program | Approved broker integrations against the Exchange | No. Requires Exchange. |
| Exchange "Grid Trading Bot" (in-app/web feature) | Exchange users | No. It runs inside the Exchange product. |
| **Crypto.com App "Read-Only API"** (tax/portfolio import) | App users, for tools like Koinly, CoinLedger, CoinTracker | **Yes — but read-only and very limited.** This is the only relevant surface for a US App user. |
| Crypto.com Pay API | Merchants accepting CRO/crypto payments | Out of scope. |
| Crypto.com Wallet (DeFi self-custody) | Self-custody DeFi users | Out of scope. |

---

## 3. What the App's read-only API actually offers

Compiled from third-party tax-tool integration docs (Koinly,
CoinLedger, CoinTracker, Cryptact), which are the only public
sources that document this surface in any detail:

- **Purpose:** designed exclusively for tax/portfolio import. Not
  marketed as, and not documented as, a trading API.
- **Permissions:** read-only by default. No "trade" permission
  setting is documented for App-issued keys (this is a hard
  difference from the Exchange API, where trade is an opt-in
  permission on the same key).
- **Data scope:** account balances and transaction/trade history
  for the App account.
- **Time coverage:** **limited to the last 6 months** of trading
  history per third-party docs. Anything older requires CSV export
  through the App UI.
- **No order placement endpoints** are documented for App-issued
  keys.
- **No public reference documentation.** Crypto.com's own help
  center page on the API is general; everything specific to App
  keys is reverse-engineered from integration partners.
- **Withdrawal permissions:** documented as not available on
  App-issued keys (good — but consistent with the read-only nature).
- **IP allowlist:** mentioned as available; behavior on App-issued
  keys is unverified.

This is a **portfolio-tracking surface, not a trading surface.**
Even if we wired the bot up to it, there is no documented endpoint
to place an order from outside the App.

---

## 4. What is unverified

Cannot be verified without the user generating a key, which I am
explicitly not asking for at this stage:

- Whether App-issued keys allow any kind of trade endpoint at all
  (almost certainly not, based on the public record, but unverified).
- Exact endpoint URLs, request format, and auth signature scheme for
  App-issued keys.
- Whether IP allowlist is actually enforced on App keys.
- Whether the 6-month history limit is hard-capped or paginated.

If at some later point we want to confirm any of this, the user can
generate a *read-only* key in the App settings, share **only** the
endpoint behavior (not the key), and we run a test. But we are not
there yet, because the public record is already strong enough to
make the execution decision.

---

## 5. Why Up/Down options and event contracts don't change this

These two products were noted in the parent audit as visible inside
the user's App. To restate clearly so it doesn't get missed:

- They are not exposed via the App's read-only API surface for
  programmatic placement.
- Even if they were, both have payoff structures that are unsuitable
  for the strategies in the roadmap (binary/fixed payoff, house
  spread). See `VENUE_AUDIT_CRYPTO_COM.md` §10 for the full
  argument.

They do not rescue the App as an execution venue.

---

## 6. Decision and recommended path

### Decision

**Crypto.com App is rejected as a programmatic execution venue.**
Specifically:
- No documented order placement API for App-issued keys.
- Read-only history surface is too shallow (6 months) to support
  the bot's reconciliation needs over time.
- Approved tax/portfolio integrations are read-only by design.

### What the App *can* be used for

- **Manual execution venue for Strategy 1.** Weekly rebalance means
  ~1–3 trades per week. The App's UI supports market and limit
  orders on spot pairs. A disciplined user can execute the bot's
  output by hand in under 5 minutes per rebalance. **This is a
  legitimate v0 path** and avoids forcing a new account before any
  strategy has even passed its gates.
- **Spot price reference** for assets quoted on the App (cross-check
  against Coinbase / public Crypto.com Exchange data).
- **Tax import source** at year-end via the documented read-only
  API.

### What is required for *automated* execution later

If/when Strategy 1 passes its gates and the user wants automation
rather than manual execution, the bot needs a different US-eligible
spot venue with a real trading API. Candidates, in rough order of
fit:

| Venue | API quality | US-OH eligibility | Notes |
|---|---|---|---|
| **Coinbase Advanced (REST/WS)** | Good, well-documented | Yes (with state-by-state nuances; OH generally OK) | Already proven in the previous repo iteration. Supports IP allowlist, key scoping. |
| **Kraken Pro (REST/WS)** | Good, well-documented | Yes | Solid API, fee tier needs check at small size. |
| **Gemini ActiveTrader (REST/WS)** | OK | Yes, NY-domiciled but supports OH | Lower liquidity on alts vs Coinbase. |
| **CDNA / Coinbase INTX perps** | Good, but **perps only** — wrong product for Strategy 1 | Yes (eligibility per-state) | Defer until a perp strategy actually exists. |

**No venue gets selected now.** A second venue audit (modeled on
`VENUE_AUDIT_CRYPTO_COM.md`) runs only if/when Strategy 1 passes its
gates and manual execution becomes the bottleneck.

---

## 7. Implications for build order

This audit closes the venue question for v0. The build order
becomes:

1. **Step 2 (now starting): Strategy 1 data layer.**
   - Pull historical daily candles + dollar volumes for the
     candidate spot universe from a free public source.
   - Source choice: **Coinbase Exchange public REST** as primary
     (US-eligible, free, no key required for public market data).
     Crypto.com Exchange public REST as cross-check.
   - Build point-in-time universe construction.
   - Build data quality checks.
   - **No execution wiring of any kind in this phase.**
2. **Step 3: Strategy 1 backtester** (separate, follows step 2).
3. **Step 4: Evaluate against the 10 gates** in
   `STRATEGY_1_REGIME_RS_SPEC.md`.
4. **Step 5 (only if Strategy 1 passes):** decide between manual
   execution (App) and automated execution (new venue audit).

We do not pre-commit to a venue. We do not pre-build execution
adapters. We do not generate any API keys.

---

## Appendix — Sources

- Crypto.com API help center: <https://help.crypto.com/en/articles/3511424-api>
- CoinLedger import guide: <https://help.coinledger.io/en/articles/10446058-crypto-com-api-import-guide>
- Koinly Crypto.com integration: <https://koinly.io/integrations/crypto-com/>
- Cryptact Crypto.com API guide: <https://support.cryptact.com/hc/en-us/articles/6722399949465-How-to-get-an-API-key-for-crypto-com>
- CoinTracking Crypto.com Exchange API import: <https://cointracking.info/import/cryptocom_api/>
- Parent audit: `VENUE_AUDIT_CRYPTO_COM.md`
