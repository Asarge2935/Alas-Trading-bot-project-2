"""
SIGNAL ↔ BACKTEST PARITY CHECK — infrastructure only.

Compares a signal_logger JSONL output to a backtest trades.csv and (optionally,
under --strict) enforces a parity gate: exit 0 only if every required check
passes within tolerances. Without --strict the script is report-only.

Required checks under --strict:
  - trade count matches
  - no signals_only / csv_only entries when joined on entry_time
  - entry_price within --price-tol
  - exit_price within --price-tol (logger 'exit_price' vs csv 'exit_price_final')
  - exit_time within --time-tol-seconds
  - exit_reason exact match
  - r_multiple within --r-tol

Run:
  python3 signal_replay_parity_check.py --signals SIGNALS.jsonl --trades TRADES.csv
  python3 signal_replay_parity_check.py --signals SIGNALS.jsonl --trades TRADES.csv \\
      --strict --price-tol 1e-8 --r-tol 1e-8 --time-tol-seconds 0 --max-mismatches 20
"""

import argparse
import json
import os
import sys
import pandas as pd

SAFETY = "INFRASTRUCTURE ONLY. No orders, no exchange connection, no paper, no live."


def load_logger_trades(path):
    """Reconstruct one row per completed trade from entry+exit JSONL events."""
    entries, exits = {}, {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            pid = ev.get("position_id")
            if pid is None:
                continue
            if ev.get("signal_type") == "entry":
                entries[pid] = ev
            elif ev.get("signal_type") == "exit":
                exits[pid] = ev
    rows = []
    for pid, e in entries.items():
        x = exits.get(pid, {})
        rows.append({
            "position_id": pid, "asset": e.get("asset"), "side": e.get("side"),
            "entry_time": e.get("timestamp_utc"), "exit_time": x.get("timestamp_utc"),
            "entry_price": e.get("entry_price"), "exit_price": x.get("exit_price"),
            "exit_reason": x.get("reason"), "r_multiple": x.get("r_multiple"),
        })
    return pd.DataFrame(rows)


def _iso_utc(s):
    return pd.to_datetime(s, utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _fail_num(failures, rid, field, a, b, tol):
    try:
        if pd.isna(a) and pd.isna(b):
            return
        if pd.isna(a) or pd.isna(b):
            failures.append({"field": field, "row": rid, "sig": a, "csv": b}); return
        d = abs(float(a) - float(b))
        if not (d <= tol):
            failures.append({"field": field, "row": rid, "sig": a, "csv": b, "delta": d})
    except Exception:
        failures.append({"field": field, "row": rid, "sig": a, "csv": b})


def _fail_str(failures, rid, field, a, b):
    if str(a) != str(b):
        failures.append({"field": field, "row": rid, "sig": str(a), "csv": str(b)})


def _fail_time(failures, rid, field, a, b, tol_s):
    try:
        ta = pd.to_datetime(a, utc=True)
        tb = pd.to_datetime(b, utc=True)
        if pd.isna(ta) and pd.isna(tb):
            return
        if pd.isna(ta) or pd.isna(tb):
            failures.append({"field": field, "row": rid, "sig": a, "csv": b}); return
        d = abs((ta - tb).total_seconds())
        if d > tol_s:
            failures.append({"field": field, "row": rid, "sig": str(a), "csv": str(b), "delta": d})
    except Exception:
        failures.append({"field": field, "row": rid, "sig": a, "csv": b})


def _emit_verdict(args, failures):
    if not args.strict:
        print("\nNote: parity is NOT enforced (run with --strict to enforce). "
              f"Defaults: price/R tol 1e-8, time tol 0s.")
        return 0
    if failures:
        n = len(failures)
        print(f"\nSTRICT PARITY: FAIL ({n} mismatch(es); showing up to {args.max_mismatches})")
        for f in failures[:args.max_mismatches]:
            d = f.get("delta")
            extra = f"  Δ={d:.6g}" if isinstance(d, (int, float)) else ""
            print(f"  {f['field']:<22} @ {f.get('row','-')}  sig={f.get('sig')}  csv={f.get('csv')}{extra}")
        return 1
    print("\nSTRICT PARITY: PASS")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--signals", required=True)
    p.add_argument("--trades", default=None)
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero on any required-check failure")
    p.add_argument("--price-tol", type=float, default=1e-8)
    p.add_argument("--r-tol", type=float, default=1e-8)
    p.add_argument("--time-tol-seconds", type=float, default=0.0)
    p.add_argument("--max-mismatches", type=int, default=20)
    args = p.parse_args()

    print(SAFETY)
    if not os.path.exists(args.signals):
        print(f"ERROR: signals file not found: {args.signals}")
        sys.exit(2)
    log = load_logger_trades(args.signals)
    print(f"\nsignals: {len(log)} completed trade(s) reconstructed from {args.signals}")
    if not log.empty:
        print(log.head(min(5, len(log))).to_string(index=False))

    failures = []

    if not args.trades:
        print("\nNo --trades CSV provided. To compare against a backtest run:")
        print("  python3 signal_replay_parity_check.py --signals SIGNALS.jsonl \\")
        print("      --trades backtest_output_eth_fts_exit/<dataset>/trades_exit.csv [--strict]")
        if args.strict:
            failures.append({"field": "missing_trades_csv", "sig": "-", "csv": "-"})
        sys.exit(_emit_verdict(args, failures))

    if not os.path.exists(args.trades):
        print(f"ERROR: trades CSV not found: {args.trades}")
        sys.exit(2)

    bt_df = pd.read_csv(args.trades)
    for col in ("entry_time", "exit_time"):
        if col in bt_df.columns:
            bt_df[col] = _iso_utc(bt_df[col])
    if not log.empty:
        log["entry_time"] = _iso_utc(log["entry_time"])
        log["exit_time"] = _iso_utc(log["exit_time"])

    print(f"\ntrades CSV: {len(bt_df)} row(s) in {args.trades}")
    count_ok = len(log) == len(bt_df)
    print(f"trade count: signals={len(log)}  csv={len(bt_df)}  "
          f"-> {'COUNT OK' if count_ok else 'COUNT MISMATCH'}")
    if not count_ok:
        failures.append({"field": "trade_count", "row": "-", "sig": len(log), "csv": len(bt_df)})

    if log.empty or bt_df.empty or "entry_time" not in bt_df.columns:
        sys.exit(_emit_verdict(args, failures))

    merged = log.merge(bt_df, on="entry_time", how="outer",
                       suffixes=("_sig", "_csv"), indicator=True)
    both = int((merged["_merge"] == "both").sum())
    only_sig = int((merged["_merge"] == "left_only").sum())
    only_csv = int((merged["_merge"] == "right_only").sum())
    print(f"matched by entry_time: both={both}  signals_only={only_sig}  csv_only={only_csv}")
    if only_sig:
        failures.append({"field": "signals_only_entries", "row": "-", "sig": only_sig, "csv": 0})
    if only_csv:
        failures.append({"field": "csv_only_entries", "row": "-", "sig": 0, "csv": only_csv})

    if both:
        sub = merged[merged["_merge"] == "both"]

        def cmp(a, b, label):
            if a not in sub.columns or b not in sub.columns:
                return
            try:
                d = (pd.to_numeric(sub[a]) - pd.to_numeric(sub[b])).abs()
                print(f"  {label:<14}: max|Δ|={d.max():.6g}  mean|Δ|={d.mean():.6g}")
            except Exception:
                eq = int((sub[a].astype(str) == sub[b].astype(str)).sum())
                print(f"  {label:<14}: {eq}/{len(sub)} exact matches")

        cmp("entry_price_sig", "entry_price_csv", "entry_price")
        cmp("exit_price", "exit_price_final", "exit_price")
        cmp("exit_time_sig", "exit_time_csv", "exit_time")
        cmp("exit_reason_sig", "exit_reason_csv", "exit_reason")
        cmp("r_multiple_sig", "r_multiple_csv", "r_multiple")

        # per-row strict checks (collect into failures)
        for _, r in sub.iterrows():
            rid = r.get("entry_time", "?")
            _fail_num(failures, rid, "entry_price",
                      r.get("entry_price_sig"), r.get("entry_price_csv"), args.price_tol)
            _fail_num(failures, rid, "exit_price",
                      r.get("exit_price"), r.get("exit_price_final"), args.price_tol)
            _fail_time(failures, rid, "exit_time",
                       r.get("exit_time_sig"), r.get("exit_time_csv"), args.time_tol_seconds)
            if "exit_reason_sig" in r.index and "exit_reason_csv" in r.index:
                if not (pd.isna(r["exit_reason_sig"]) and pd.isna(r["exit_reason_csv"])):
                    _fail_str(failures, rid, "exit_reason",
                              r["exit_reason_sig"], r["exit_reason_csv"])
            if "r_multiple_sig" in r.index and "r_multiple_csv" in r.index:
                _fail_num(failures, rid, "r_multiple",
                          r.get("r_multiple_sig"), r.get("r_multiple_csv"), args.r_tol)

    sys.exit(_emit_verdict(args, failures))


if __name__ == "__main__":
    main()
