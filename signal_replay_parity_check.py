"""
SIGNAL ↔ BACKTEST PARITY CHECK — infrastructure only.

Compares a signal_logger JSONL output to a backtest trades.csv (optional) to
prepare for a future backtest↔replay parity gate. Parity is NOT enforced today;
the tool just reports what matches and what doesn't.

Run:
  python3 signal_replay_parity_check.py --signals logs/signals_eth_replay.jsonl
  python3 signal_replay_parity_check.py --signals logs/signals_eth_replay.jsonl \\
      --trades backtest_output_eth_fts_exit/coinbase_builtin/trades_exit.csv
"""

import argparse
import json
import os
import pandas as pd

SAFETY = ("INFRASTRUCTURE ONLY. No orders, no exchange connection, no paper, no live.")


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


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--signals", required=True)
    p.add_argument("--trades", default=None)
    args = p.parse_args()

    print(SAFETY)
    if not os.path.exists(args.signals):
        print(f"ERROR: signals file not found: {args.signals}")
        return
    log = load_logger_trades(args.signals)
    print(f"\nsignals: {len(log)} completed trade(s) reconstructed from {args.signals}")
    if not log.empty:
        print(log.head(min(5, len(log))).to_string(index=False))

    if not args.trades:
        print("\nNo --trades CSV provided. To compare against a backtest run:")
        print("  python3 signal_replay_parity_check.py --signals SIGNALS.jsonl \\")
        print("      --trades backtest_output_eth_fts_exit/coinbase_builtin/trades_exit.csv")
        return
    if not os.path.exists(args.trades):
        print(f"ERROR: trades CSV not found: {args.trades}")
        return

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

    if log.empty or bt_df.empty or "entry_time" not in bt_df.columns:
        return

    merged = log.merge(bt_df, on="entry_time", how="outer",
                       suffixes=("_sig", "_csv"), indicator=True)
    both = int((merged["_merge"] == "both").sum())
    only_sig = int((merged["_merge"] == "left_only").sum())
    only_csv = int((merged["_merge"] == "right_only").sum())
    print(f"matched by entry_time: both={both}  signals_only={only_sig}  csv_only={only_csv}")

    if not both:
        return
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
    print("\nNote: parity is NOT enforced today (output schemas differ slightly). This tool "
          "prepares for a future bit-for-bit parity gate.")


if __name__ == "__main__":
    main()
