"""
VERIFY SIGNAL PARITY OFFLINE — strict-parity gate self-test.

Builds tiny synthetic signal JSONL + matching backtest trades.csv, runs
signal_replay_parity_check.py --strict, then intentionally mutates one field
and re-runs to confirm strict mode catches the mismatch. No network, no API,
no real data, no orders.

Exit codes:
  0 = both checks behaved as expected (strict PASS on match, strict FAIL on mutation)
  1 = the gate misbehaved (one or both checks gave the wrong verdict)
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PARITY = os.path.join(HERE, "signal_replay_parity_check.py")


def _write_jsonl(path, trades):
    with open(path, "w") as f:
        for i, t in enumerate(trades):
            pid = f"pos_{i}"
            common = {
                "run_id": "test_run", "mode": "csv-replay",
                "strategy_name": "eth_strict_breakout_fts_exit",
                "strategy_version": "1.0.0",
                "asset": "ETH-USD", "timeframe": "6H", "side": "long",
                "regime_state": "risk_on", "risk_state": {},
                "source_data_label": "synth", "position_id": pid,
                "config_hash": "abc",
            }
            entry = dict(common, event_id=f"e_{i}", signal_type="entry",
                         timestamp_utc=t["entry_iso"], bar_timestamp=t["entry_iso"],
                         entry_price=t["entry_price"],
                         stop_price=t["entry_price"] - 1.0,
                         target_1_price=t["entry_price"] + 1.5,
                         initial_stop_distance=1.0, notional_usd=100.0,
                         atr_at_signal=1.0, r_multiple_expected=1.5,
                         reason="breakout_entry")
            exit_ev = dict(common, event_id=f"x_{i}", signal_type="exit",
                           timestamp_utc=t["exit_iso"], bar_timestamp=t["exit_iso"],
                           entry_price=t["entry_price"], exit_price=t["exit_price"],
                           stop_price=t["entry_price"] - 1.0,
                           reason=t["reason"], r_multiple=t["r_multiple"],
                           gross_pnl_usd=0.0, net_pnl_usd=0.0,
                           fees_usd=0.0, slippage_usd=0.0, funding_usd=0.0,
                           bars_held=2)
            f.write(json.dumps(entry) + "\n")
            f.write(json.dumps(exit_ev) + "\n")


def _write_trades_csv(path, trades):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "exit_time", "entry_price",
                    "exit_price_final", "exit_reason", "r_multiple"])
        for t in trades:
            w.writerow([t["entry_iso"], t["exit_iso"], t["entry_price"],
                        t["exit_price"], t["reason"], t["r_multiple"]])


def _run_parity(signals, trades_csv):
    return subprocess.run(
        [sys.executable, PARITY, "--signals", signals, "--trades", trades_csv, "--strict"],
        capture_output=True, text=True)


def main():
    tmp = tempfile.mkdtemp(prefix="parity_test_")
    print("VERIFY SIGNAL PARITY OFFLINE — strict-gate self-test (no network, no API).")
    try:
        trades = [
            {"entry_iso": "2024-01-01T00:00:00+00:00",
             "exit_iso":  "2024-01-01T12:00:00+00:00",
             "entry_price": 100.0, "exit_price": 102.0,
             "reason": "trail_stop", "r_multiple": 1.20},
            {"entry_iso": "2024-02-01T00:00:00+00:00",
             "exit_iso":  "2024-02-01T12:00:00+00:00",
             "entry_price": 110.0, "exit_price": 108.0,
             "reason": "failure_to_separate", "r_multiple": -0.40},
        ]
        jsonl = os.path.join(tmp, "signals.jsonl")
        csv_path = os.path.join(tmp, "trades.csv")
        _write_jsonl(jsonl, trades)
        _write_trades_csv(csv_path, trades)

        # 1) matching => strict PASS expected
        r1 = _run_parity(jsonl, csv_path)
        ok1 = r1.returncode == 0 and "STRICT PARITY: PASS" in r1.stdout
        print(("[PASS]" if ok1 else "[FAIL]"),
              "matching signals + trades  ->  STRICT PARITY: PASS  "
              f"(exit={r1.returncode})")
        if not ok1:
            print("--- parity stdout ---\n" + r1.stdout)
            print("--- parity stderr ---\n" + r1.stderr)

        # 2) mutate one exit_price => strict FAIL expected
        bad = [dict(trades[0], exit_price=trades[0]["exit_price"] + 1.0), trades[1]]
        _write_trades_csv(csv_path, bad)
        r2 = _run_parity(jsonl, csv_path)
        ok2 = r2.returncode != 0 and "STRICT PARITY: FAIL" in r2.stdout
        print(("[PASS]" if ok2 else "[FAIL]"),
              "intentional exit_price mismatch  ->  STRICT PARITY: FAIL  "
              f"(exit={r2.returncode})")
        if not ok2:
            print("--- parity stdout ---\n" + r2.stdout)
            print("--- parity stderr ---\n" + r2.stderr)

        if ok1 and ok2:
            print("\n[PASS] verify_signal_parity_offline: strict-parity gate works end-to-end.")
            sys.exit(0)
        sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
