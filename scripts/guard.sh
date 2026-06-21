#!/usr/bin/env bash
#
# guard.sh — PreToolUse guard for Claude Code, shared by all permission modes.
#
# Usage (from settings.json hook):
#   ./scripts/guard.sh research
#   ./scripts/guard.sh validation
#   ./scripts/guard.sh paper
#   ./scripts/guard.sh live-assist
#
# Behavior:
#   - Reads the tool input from $CLAUDE_TOOL_INPUT if set, otherwise from stdin.
#     (Different Claude Code versions deliver hook input differently; this
#      handles both. VERIFY against your installed version — see test below.)
#   - Exit 0 => allow.  Exit 2 => block (Claude sees the stderr message).
#
# IMPORTANT: this guard "fails closed" only for patterns it knows about.
# It is a backstop, NOT your only control. The allow/deny lists in the
# settings files and your app-level flags (RESEARCH_ONLY / PAPER_TRADING /
# LIVE_TRADING) are the primary locks.

# NOTE: deliberately NOT using `set -e` / `pipefail`. A `grep -q` with no match
# returns exit 1, which under `set -e` would abort the script and exit 0 (allow)
# before reaching the block logic — i.e. fail OPEN. We want fail-CLOSED, so we
# control exit codes explicitly and use a matches() helper instead.
set -u

MODE="${1:-research}"

# matches PATTERN  -> returns 0 (true) if $HAYSTACK matches, 1 otherwise.
# Wrapped so a non-match never propagates as a script-aborting error.
matches() {
  printf '%s' "$HAYSTACK" | grep -qiE "$1"
}

# --- Gather the command text to inspect -------------------------------------
# Prefer the env var; fall back to stdin. Lowercased for matching.
INPUT="${CLAUDE_TOOL_INPUT:-}"
if [ -z "$INPUT" ] && [ ! -t 0 ]; then
  INPUT="$(cat || true)"
fi
HAYSTACK="$(printf '%s' "$INPUT" | tr '[:upper:]' '[:lower:]')"

block() {
  # $1 = human-readable reason
  echo "BLOCKED [$MODE]: $1" >&2
  exit 2
}

# --- Patterns that are NEVER allowed, in ANY mode ---------------------------
# Fund movement and real-exchange REST hosts. These are the catastrophic ones.
FORBIDDEN_ALWAYS='(withdraw|transfer|sweep|payout|api\.coinbase\.com|api\.exchange\.coinbase\.com|api\.kraken\.com|api\.bybit\.com|api\.binance\.com)'
if matches "$FORBIDDEN_ALWAYS"; then
  block "fund movement or a real-exchange endpoint is never permitted"
fi

# --- Per-mode rules ---------------------------------------------------------
case "$MODE" in
  research)
    # No execution of any kind: no trading, no paper, no sim, no network fetch.
    if matches '(--live|--real|--paper|--sim|place_order|submit_order|create_order|cancel_order)'; then
      block "research mode forbids trading, paper, and sim execution"
    fi
    if matches '(\bcurl\b|\bwget\b)'; then
      block "research mode is offline; route data fetches through a wrapper in validation mode"
    fi
    ;;

  validation)
    # Integration + approved data work. No order paths of any kind.
    if matches '(--live|--real|--paper|--sim|place_order|submit_order|create_order|cancel_order)'; then
      block "validation mode allows data/integration work, not order execution"
    fi
    # Free-form network tools blocked; use ./scripts/fetch_market_data.sh
    if matches '(\bcurl\b|\bwget\b)'; then
      block "use ./scripts/fetch_market_data.sh instead of free-form curl/wget"
    fi
    ;;

  paper)
    # Paper/sim execution allowed; live paths forbidden.
    if matches '(--live|--real|place_order|submit_order|create_order)'; then
      block "paper mode forbids live orders and real exchange execution"
    fi
    if matches '(\bcurl\b|\bwget\b)'; then
      block "use ./scripts/fetch_market_data.sh instead of free-form curl/wget"
    fi
    ;;

  live-assist)
    # Human-supervised ops only. Allow nothing free-form toward exchanges.
    # (FORBIDDEN_ALWAYS already blocks withdraw/transfer/sweep/payout + hosts.)
    if matches '(\bcurl\b|\bwget\b)'; then
      block "live-assist forbids free-form curl/wget; use reviewed wrapper scripts"
    fi
    ;;

  *)
    block "unknown mode '$MODE' (expected: research|validation|paper|live-assist)"
    ;;
esac

# Allowed.
exit 0
