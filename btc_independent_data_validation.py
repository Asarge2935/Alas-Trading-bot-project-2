"""
BTC INDEPENDENT-DATA VALIDATION — runs the EXISTING BTC compression breakout
(FULL compression, comp_count == 3) against built-in data OR a user-supplied
OHLCV CSV, on 12H and 1D, to test STRUCTURAL PERSISTENCE. Not optimization.

Modes:
    python3 btc_independent_data_validation.py                       # built-in BTC data
    python3 btc_independent_data_validation.py --csv btc.csv --label binance_btc_spot

Scope (deliberately narrow):
  - 12H full-compression (the primary BTC research lead).
  - 1D  full-compression as STRUCTURAL PERSISTENCE only (NOT a new optimization
    path; not a timeframe search; no weekly; compare BEHAVIOR, not PF).
BTC compression is self-contained (BTC only), so CSV mode needs no cross-asset
context. Same logic everywhere (reuses btc_compression_diagnostic.compression_
indicators + _run_btc_compression). No tuning.

Outputs (backtest_output_btc_independent/<label>/): trades_{12h,1d}.csv, summary.txt
"""

import argparse
import os
import numpy as np

import backtest as bt
import btc_compression_diagnostic as bc
from btc_compression_next_diagnostic import _metrics, _f, _flags
from ohlcv_csv_validation_harness import load_ohlcv_csv, resample_ohlcv

OUT_BASE = "backtest_output_btc_independent"

# (label, entry_seconds, built-in fetch_gran, built-in agg, csv_resample, overrides)
TF_CONFIGS = [
    ("12h", 43200, 21600, "12h", "12h",
     {"TIMEFRAME_SECONDS": 43200, "BARS_PER_DAY": 2, "TIME_STOP_BARS": 6, "ATR_REGIME_PERIOD": 60}),
    ("1d", 86400, 86400, None, "1D",
     {"TIMEFRAME_SECONDS": 86400, "BARS_PER_DAY": 1, "TIME_STOP_BARS": 3, "ATR_REGIME_PERIOD": 30}),
]


def _full_filter(r):
    return r["comp_count_prev"] == 3


def _build_df(tf_label, entry_s, fetch_gran, agg, csv_resample, days, csv_raw):
    if csv_raw is not None:
        raw = resample_ohlcv(csv_raw, csv_resample)
    else:
        raw = bt.load_candles("BTC-USD", fetch_gran, days)
        if agg:
            raw = bc._aggregate(raw, agg)
    return bc.compression_indicators(raw, entry_s)


def render(m_by_tf, label, note):
    L = ["=" * 80,
         f"BTC INDEPENDENT-DATA VALIDATION — FULL compression (comp_count==3) — label='{label}'",
         "=" * 80, f"  {note}",
         "  (DIAGNOSTIC ONLY — structural persistence, not optimization. Compare BEHAVIOR, not PF.)"]
    labels = list(m_by_tf)
    w = 16
    rows = [
        ("Trades", lambda m: str(m.get("trades", 0))),
        ("Winners", lambda m: str(m.get("winners", 0))),
        ("Losers", lambda m: str(m.get("losers", 0))),
        ("Win rate", lambda m: _f(m.get("win_rate", float("nan")), pct=True)),
        ("PF", lambda m: _f(m.get("pf", float("nan")))),
        ("Avg R", lambda m: _f(m.get("avg_r", float("nan")), pos=True)),
        ("Net $", lambda m: _f(m.get("net", 0.0), pos=True)),
        ("Max DD %", lambda m: _f(m.get("max_dd", 0.0))),
        ("MaxConsecL", lambda m: str(m.get("max_consec_losses", 0))),
        ("Ex-best net", lambda m: _f(m.get("exbest_net", 0.0), pos=True)),
        ("Ex-best PF", lambda m: _f(m.get("exbest_pf", float("nan")))),
        ("Top %", lambda m: _f(m.get("top_pct", float("nan")), pct=True)),
        ("Top-3 %", lambda m: _f(m.get("top3_pct", float("nan")), pct=True)),
        ("Best month %", lambda m: _f(m.get("best_month_pct", float("nan")), pct=True)),
        ("IS PF", lambda m: _f(m.get("is_pf", float("nan")))),
        ("OOS PF", lambda m: _f(m.get("oos_pf", float("nan")))),
        ("Full-comp occ", lambda m: str(m.get("trades", 0))),
        ("Losers MFE_R", lambda m: _f(m.get("mfe_avg", float("nan")))),
        ("Losers MAE_R", lambda m: _f(m.get("mae_avg", float("nan")))),
        (">=+0.5R", lambda m: f"{m.get('mfe_05', 0)}/{m.get('loss_n', 0)}"),
        (">=+1R", lambda m: f"{m.get('mfe_10', 0)}/{m.get('loss_n', 0)}"),
    ]
    L.append(f"\n{'Metric':<16}" + "".join(f"{lab:>{w}}" for lab in labels))
    L.append("-" * (16 + w * len(labels)))
    for lab, fn in rows:
        L.append(f"{lab:<16}" + "".join(f"{fn(m_by_tf[t]):>{w}}" for t in labels))
    L.append("\nPer-year net $:")
    yrs = sorted(set().union(*[set(m.get("by_year", {})) for m in m_by_tf.values()]))
    for y in yrs:
        L.append(f"  {y}: " + "  ".join(f"{t}={(m_by_tf[t].get('by_year', {}).get(y) or {}).get('net', 0):+.0f}" for t in labels))
    L.append("Monthly net $ (per timeframe):")
    for t in labels:
        mm = m_by_tf[t].get("by_month", {})
        L.append(f"  [{t}] " + ("  ".join(f"{k}=${v:+.0f}" for k, v in sorted(mm.items())) if mm else "none"))
    L.append("\nVerdict flags:")
    for t in labels:
        L.append(f"  {t:<4}: {', '.join(_flags(m_by_tf[t]))}")
    L.append("\nReading guide: persistence = the SHAPE (ex-best PF, OOS, distribution, full-comp")
    L.append("occurrence) is consistent across 12H and 1D. 1D is a persistence cross-check, NOT a")
    L.append("better timeframe. Full-compression staying tiny-sample => promising but UNVALIDATED.")
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    p.add_argument("--csv", default=None)
    p.add_argument("--label", default="builtin")
    args = p.parse_args()

    csv_raw = None
    note = "built-in mode: BTC from current provider."
    if args.csv:
        csv_raw, errs = load_ohlcv_csv(args.csv)
        note = (f"CSV mode ({args.label}): BTC from CSV ({len(csv_raw)} bars). "
                + ("CSV warnings: " + "; ".join(errs) if errs else "no CSV warnings."))

    out_dir = os.path.join(OUT_BASE, args.label)
    os.makedirs(out_dir, exist_ok=True)
    m_by_tf = {}
    for tf, entry_s, fetch_gran, agg, csv_resample, ov in TF_CONFIGS:
        saved = {k: getattr(bt, k) for k in ov}
        for k, v in ov.items():
            setattr(bt, k, v)
        try:
            df = _build_df(tf, entry_s, fetch_gran, agg, csv_resample, args.days, csv_raw)
            trades, eq = bc._run_btc_compression(df.set_index("time"), extra_filter=_full_filter)
            autopsy = bt.build_loss_autopsy(trades, {"BTC-USD": df})
            bt.export_trades_csv(trades, os.path.join(out_dir, f"trades_{tf}.csv"))
            m_by_tf[tf] = _metrics(trades, eq, autopsy)
            print(f"  {tf} full-compression: {len(trades)} trades")
        finally:
            for k, v in saved.items():
                setattr(bt, k, v)

    report = render(m_by_tf, args.label, note)
    print("\n" + report)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote {out_dir}/summary.txt")


if __name__ == "__main__":
    main()
