#!/usr/bin/env bash
#
# check_guard.sh — self-test for ./scripts/guard.sh.
#
# Runs the guard against a fixture set of commands and asserts each one is
# allowed (exit 0) or blocked (exit 2) as expected, in each of the four modes.
# This tests the HOOK logic itself; the settings files' allow/deny lists are
# enforced by Claude Code, not here.
#
# Usage:
#   ./scripts/check_guard.sh
# Exit 0 on all-pass; 1 on any failure.

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
GUARD="$HERE/guard.sh"

PASS=0
FAIL=0

# expect MODE EXPECTED_EXIT INPUT
expect() {
  local mode="$1" want="$2" input="$3"
  local got
  CLAUDE_TOOL_INPUT="$input" bash "$GUARD" "$mode" >/dev/null 2>&1
  got=$?
  if [ "$got" = "$want" ]; then
    PASS=$((PASS+1))
    printf '  [PASS] %-12s exit=%d  %s\n' "$mode" "$got" "$input"
  else
    FAIL=$((FAIL+1))
    printf '  [FAIL] %-12s want=%d got=%d  %s\n' "$mode" "$want" "$got" "$input"
  fi
}

echo "guard.sh self-test"
echo "------------------"
echo "ALWAYS-FORBIDDEN (every mode must block these):"
for mode in research validation paper live-assist; do
  expect "$mode" 2 'curl https://api.binance.com/api/v3/order'
  expect "$mode" 2 'submit withdraw 100 usd to wallet'
  expect "$mode" 2 'python sweep_balance.py'
  expect "$mode" 2 'python transfer_funds.py'
done

echo
echo "RESEARCH mode (offline; no execution):"
expect research 0 'python3 backtest.py'
expect research 0 'git status'
expect research 2 'python script.py --paper'
expect research 2 'python script.py --sim'
expect research 2 'python script.py --live'
expect research 2 'curl https://example.com'
expect research 2 'wget https://example.com'
expect research 2 'python place_order(amount=1)'

echo
echo "VALIDATION mode (network OK via wrapper; no orders):"
expect validation 0 'python3 backtest.py'
expect validation 0 './scripts/fetch_market_data.sh BTCUSDT 1h'
expect validation 0 'git fetch'
expect validation 2 'python script.py --paper'
expect validation 2 'python script.py --sim'
expect validation 2 'python script.py --live'
expect validation 2 'curl https://example.com'
expect validation 2 'python submit_order(amount=1)'

echo
echo "PAPER mode (paper/sim allowed; live denied):"
expect paper 0 'python script.py --paper'
expect paper 0 'python script.py --sim'
expect paper 0 './scripts/fetch_market_data.sh BTCUSDT 1h'
expect paper 2 'python script.py --live'
expect paper 2 'python script.py --real'
expect paper 2 'python submit_order(amount=1)'
expect paper 2 'curl https://example.com'

echo
echo "LIVE-ASSIST mode (catastrophic class + curl/wget only):"
expect live-assist 0 './scripts/run_live_assist.sh status'
expect live-assist 0 './scripts/check_bot_health.sh'
expect live-assist 0 'git status'
expect live-assist 2 'curl https://example.com'
expect live-assist 2 'wget https://example.com'
# (--live / submit_order are NOT blocked here by the guard; the settings
#  allowlist is what restricts which scripts can run. See docs.)

echo
echo "UNKNOWN mode rejected:"
expect bogus 2 'python3 backtest.py'

echo
echo "-----------------------------------------"
printf 'guard self-test: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
