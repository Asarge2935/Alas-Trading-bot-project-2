# Claude Code Permissions — Mode Model

**Status: research mode only is shipped.** Validation, paper, and live-assist
templates will be added at their corresponding §8.5 build steps in
`docs/STRATEGY_RESEARCH_ROADMAP.md`. This file documents the model, the
defense-in-depth design, and the known gaps so future-you knows what these
configs do and do **not** protect against.

## The four modes

Claude Code permission scope is split into four modes that map directly onto
the live-readiness build order in roadmap §8.5:

| Mode | Roadmap stage | What's allowed | Network | Orders | Templates here |
|---|---|---|---|---|---|
| **research** | §8.4 phases A/B | offline backtests, diagnostics, signal logger, parity check | **off** | none | **shipped** |
| validation | §8.5 step 5 | data fetch via reviewed wrapper, integration work | on (wrapper only) | none | not yet |
| paper | §8.5 step 3 / 8 | dry-run / paper-fill paths (`--paper` / `--sim`) | on (wrapper only) | sim only | not yet |
| live-assist | §8.5 step 9 | tiny pilot ops via three reviewed scripts | on | only the three scripts | not yet |

Templates live in `claude_modes/`. We ship one at a time, in order. See
`claude_modes/README.md` for how to switch.

## Defense in depth

Four independent layers must agree before a tool call runs:

1. **Application code.** No order placement paths exist in this repo today.
   This is the most important lock. Removing it is a code change, reviewable.
2. **Sandbox flags** (`sandbox.network.allow`, `sandbox.filesystem.writeToWorkingDirectoryOnly`).
   OS-level constraints enforced by Claude Code's harness.
3. **Settings allow/deny lists** (`permissions.allow`, `permissions.deny`) in
   `.claude/settings.json` (or `.local.json`). Tool-call-shape blocklist.
4. **`scripts/guard.sh` PreToolUse hook.** Last-line regex backstop over the
   command text. Same script in all four modes, parameterised by `$1`.

> The guard's own header puts it bluntly: *"this guard 'fails closed' only for
> patterns it knows about. It is a backstop, NOT your only control."*

### What the guard catches

- **Always (every mode):** fund movement (`withdraw|transfer|sweep|payout`)
  and major exchange REST hosts (`api.coinbase.com`, `api.exchange.coinbase.com`,
  `api.kraken.com`, `api.bybit.com`, `api.binance.com`).
- **Research:** any of `--live|--real|--paper|--sim|place_order|submit_order|
  create_order|cancel_order`, and free-form `curl`/`wget` (research is offline).
- **Validation:** the order patterns above, and free-form `curl`/`wget` (must
  use `./scripts/fetch_market_data.sh`, which itself host-allowlists).
- **Paper:** `--live|--real|place_order|submit_order|create_order`, and
  free-form `curl`/`wget`. **Paper allows `--paper` and `--sim`.**
- **Live-assist:** free-form `curl`/`wget`. The settings allowlist (only three
  reviewed scripts) is the real restriction in this mode.

### A correctness detail worth keeping

The guard deliberately does **not** use `set -e`. Reason:
`grep -q PATTERN` returns exit 1 when there is no match. Under `set -e` that
would abort the script and exit 0 (allow) before any block logic ran —
i.e. **fail open**. The guard uses `set -u` + a `matches()` helper + explicit
`exit` calls so a non-match never aborts the script. Keep it that way.

## Known gaps (read before relying on this)

1. **Pattern matching is best-effort against accidents, not adversarial.**
   The hook only sees the command string. `python3 my_script.py` cannot be
   inspected for what Python will fetch at runtime. Real adversarial defense
   would need OS-level egress controls (iptables / network namespace / proxy
   with host allowlist).
2. **`FORBIDDEN_ALWAYS` host list is not exhaustive.** It covers the big four
   spot exchanges but **not** OKX, Bitget, Gemini, Bitstamp, dYdX, Hyperliquid,
   RPC providers (Alchemy / Infura / QuickNode), or chain-specific endpoints.
   If you rotate venues, **update the list** or invert the policy in
   `fetch_market_data.sh` (host allowlist).
3. **`data.binance.vision` is intentionally not in the forbidden list** — it's
   bulk historical klines, not orders. Your `fetch_market_data.sh` should
   explicitly allow exactly this host and deny everything else.
4. **`pip install:*` is allowed in research mode** even though `sandbox.network.allow = false`.
   Either pip will silently fail (dead allow) or the sandbox is not blocking
   network at the OS level (supply-chain risk). **Verify which it is on your
   installed Claude Code version.**
5. **`Bash(*--paper*)` is a substring match.** A future flag like
   `--no-paper-mode` would match. Trivial today; the lint to remember.
6. **`Bash(kill:*)` is denied even in live-assist** — you cannot kill a hung
   supervised process without unblocking. When live-assist actually ships,
   consider routing kills through a reviewed script.
7. **No `PostToolUse` hook.** A cheap layered add: verify after each call that
   nothing was written under `secrets/`, no new `.env*` appeared, etc.
8. **The three live-assist scripts (`run_live_assist.sh`,
   `check_bot_health.sh`, `check_positions_readonly.sh`) are the actual
   blast-radius surface in that mode.** They do not exist yet. When they're
   drafted, review each line — what they can do, Claude can do.

## Self-test

The hook itself is unit-tested by `./scripts/check_guard.sh`. It asserts the
expected exit code (0 allow, 2 block) for a fixture set in each of the four
modes — including the always-forbidden tier, mode-specific deny patterns, and
representative allows. Run it any time you edit `guard.sh`.

```bash
./scripts/check_guard.sh
# guard self-test: N passed, 0 failed
```

This self-test exercises the guard's per-mode logic. It does **not** test the
settings files' allow/deny lists — those are enforced by Claude Code at
tool-call time, not by the hook.

## How this maps to the trading-bot roadmap

- **Today (research):** active mode. Offline diagnostics, signal logger, parity
  check. Nothing connects to an exchange, nothing places orders.
- **When the third-venue OOS test runs (validation):** add
  `claude_modes/settings.validation.json` + write
  `./scripts/fetch_market_data.sh` with a hard host allowlist. The guard
  already supports `validation` mode.
- **When dry-run / paper mode is built (paper):** add `settings.paper.json`.
  The guard already supports it.
- **Only if every research / paper / operational / risk / execution gate in
  §8.3 has passed (live-assist):** add `settings.liveassist.json` and the
  three reviewed scripts. Review each script line by line before activating.

Nothing in this directory authorises live trading. It controls what Claude
Code is allowed to run, not what the trading system is allowed to do.
