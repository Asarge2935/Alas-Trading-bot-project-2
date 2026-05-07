"""
Verify the drawdown circuit breakers from PRE_PAPER_TRADE_CHECKLIST §E
without manually editing backtest.py constants.

Fetches candles once, then runs run_backtest three times with different
DRAWDOWN_STOP_PCT / DRAWDOWN_PAUSE_PCT values via in-memory monkey-patching.
The original constants are restored at the end.

Run:
    python verify_drawdown_breakers.py

Paste the entire console output back into chat.
"""

import contextlib
import io
import sys

import backtest as bt


def run_with_constants(data, dd_stop_pct, dd_pause_pct):
    """Run a backtest with patched DD constants and capture stdout."""
    original_stop = bt.DRAWDOWN_STOP_PCT
    original_pause = bt.DRAWDOWN_PAUSE_PCT
    try:
        bt.DRAWDOWN_STOP_PCT = dd_stop_pct
        bt.DRAWDOWN_PAUSE_PCT = dd_pause_pct
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trades, curve = bt.run_backtest(data)
        return trades, curve, buf.getvalue()
    finally:
        bt.DRAWDOWN_STOP_PCT = original_stop
        bt.DRAWDOWN_PAUSE_PCT = original_pause


def main():
    print("=" * 70)
    print("Drawdown circuit-breaker verification (checklist §E)")
    print("=" * 70)
    print()

    # 1. Fetch data once and feed all three scenarios from the same candles.
    print("Fetching candles (one round-trip, reused for all 3 scenarios)...")
    data = {}
    for symbol in bt.ASSETS:
        df = bt.fetch_candles(symbol, bt.TIMEFRAME_SECONDS, bt.DAYS_BACK)
        df = bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS)
        if df.empty:
            print(f"  {symbol}: 0 bars — skipping")
            continue
        df = bt.add_indicators(df)
        data[symbol] = df
        print(f"  {symbol}: {len(df)} bars")

    if "BTC-USD" not in data:
        print("ERROR: BTC-USD missing — cannot proceed.")
        sys.exit(1)
    print()

    # 2. Reference run with production constants.
    print("--- A. Reference run (STOP=25%, PAUSE=15%) ---")
    _, curve_a, out_a = run_with_constants(data, 25.0, 15.0)
    breaker_lines = [l for l in out_a.splitlines() if "DRAWDOWN" in l]
    if breaker_lines:
        print("Breaker activity:")
        for line in breaker_lines:
            print(f"  {line}")
    else:
        print("No breaker activity (DD stayed below thresholds — fine for a normal run).")
    max_dd_a = max((p["drawdown_pct"] for p in curve_a), default=0.0)
    print(f"Max DD observed: {max_dd_a:.2f}%")
    print()

    # 3. Stop test: lower DRAWDOWN_STOP_PCT to 5%.
    print("--- B. Stop test (STOP=5%, PAUSE=15%) ---")
    _, curve_b, out_b = run_with_constants(data, 5.0, 15.0)
    stop_lines = [l for l in out_b.splitlines() if "DRAWDOWN STOP HIT" in l]
    if stop_lines:
        print("[PASS] DRAWDOWN STOP fired:")
        for line in stop_lines:
            print(f"  {line}")
    else:
        max_dd_b = max((p["drawdown_pct"] for p in curve_b), default=0.0)
        print(f"[INFO] DD never reached 5% (max observed: {max_dd_b:.2f}%). "
              f"Breaker code path not exercised in this dataset — neither pass nor fail.")
    print()

    # 4. Pause test: lower DRAWDOWN_PAUSE_PCT to 3%.
    print("--- C. Pause test (STOP=25%, PAUSE=3%) ---")
    _, curve_c, out_c = run_with_constants(data, 25.0, 3.0)
    pause_lines = [l for l in out_c.splitlines() if "DRAWDOWN PAUSE" in l]
    if pause_lines:
        print("[PASS] DRAWDOWN PAUSE fired:")
        for line in pause_lines:
            print(f"  {line}")
    else:
        max_dd_c = max((p["drawdown_pct"] for p in curve_c), default=0.0)
        print(f"[INFO] DD never reached 3% (max observed: {max_dd_c:.2f}%). "
              f"Breaker code path not exercised in this dataset.")
    print()

    # 5. Sanity: production constants must be back to 25 / 15.
    print("--- D. Constants restored ---")
    print(f"  DRAWDOWN_STOP_PCT  = {bt.DRAWDOWN_STOP_PCT}  (expected 25.0)")
    print(f"  DRAWDOWN_PAUSE_PCT = {bt.DRAWDOWN_PAUSE_PCT}  (expected 15.0)")
    if bt.DRAWDOWN_STOP_PCT != 25.0 or bt.DRAWDOWN_PAUSE_PCT != 15.0:
        print("[FAIL] Constants not restored — investigate the harness.")
        sys.exit(1)
    print()
    print("Done. Paste this entire output into chat.")


if __name__ == "__main__":
    main()
