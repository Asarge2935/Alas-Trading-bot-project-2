"""
BTC COMPRESSION TIMEFRAME FEASIBILITY — diagnostic only. Tests whether the BTC
compression / volatility-release behavior repeats across NEARBY timeframes
without tuning. The goal is NOT to pick the "best" timeframe — it is to check
whether the phenomenon is stable or just a 12H artifact.

No ETH/live/paper changes. No per-timeframe threshold tuning. Same logic
everywhere (reuses btc_compression_diagnostic.py's exact build + loop + the
single early-failure rule from btc_compression_next_diagnostic.py).

Variants (per timeframe): baseline, full_compression_only (comp_count==3),
baseline + early-failure exit, full_compression + early-failure exit.

Timeframes:
  - 12H (primary)  : aggregated from native 6H.
  - 8H  (nearby)   : aggregated from native 1H (8h = 8x1H; floor('8h') -> 00/08/16 UTC).
  - 6H  (nearby)   : native.
  (4H intentionally omitted — noisier per prior findings; not part of this test.)

Bar-based indicators (EMA50, ATR14, 20-bar breakout, 120-bar compression) are
held identical; only time-equivalent params are rescaled to preserve clock
intent: TIME_STOP_BARS ~72h (6@12H, 9@8H, 12@6H), ATR_REGIME_PERIOD ~30d
(60@12H, 90@8H, 120@6H), BARS_PER_DAY (2/3/4).

Run:
    python3 btc_compression_timeframe_feasibility.py [--days N]

Outputs (backtest_output_btc_compression_feasibility/):
    trades_{tf}_{variant}.csv + equity/loss_autopsy + console comparison.
"""

import argparse
import os
import numpy as np

import backtest as bt
import btc_compression_diagnostic as bc
from btc_compression_next_diagnostic import _metrics, _f, _flags, _make_early_exit

OUT_DIR = "backtest_output_btc_compression_feasibility"

# (tf label, entry_seconds, fetch_gran, agg_freq, overrides)
TIMEFRAMES = [
    ("12h", 43200, 21600, "12h",
     {"TIMEFRAME_SECONDS": 43200, "BARS_PER_DAY": 2, "TIME_STOP_BARS": 6, "ATR_REGIME_PERIOD": 60}),
    ("8h", 28800, 3600, "8h",
     {"TIMEFRAME_SECONDS": 28800, "BARS_PER_DAY": 3, "TIME_STOP_BARS": 9, "ATR_REGIME_PERIOD": 90}),
    ("6h", 21600, 21600, None,
     {"TIMEFRAME_SECONDS": 21600, "BARS_PER_DAY": 4, "TIME_STOP_BARS": 12, "ATR_REGIME_PERIOD": 120}),
]

VARIANT_DEFS = [
    ("base", None, False),
    ("full", lambda r: r["comp_count_prev"] == 3, False),
    ("base+exit", None, True),
    ("full+exit", lambda r: r["comp_count_prev"] == 3, True),
]


def run_tf(label, entry_s, gran, agg, overrides, days):
    saved = {k: getattr(bt, k) for k in overrides}
    for k, v in overrides.items():
        setattr(bt, k, v)
    out = {}
    try:
        df = bc._build_btc_df(entry_s, gran, agg, days)
        btc_indexed = df.set_index("time")
        early = _make_early_exit(btc_indexed)
        for vlabel, filt, use_exit in VARIANT_DEFS:
            trades, eq = bc._run_btc_compression(btc_indexed, extra_filter=filt,
                                                 early_exit_fn=(early if use_exit else None))
            autopsy = bt.build_loss_autopsy(trades, {"BTC-USD": df})
            key = f"{label}_{vlabel}"
            bt.export_trades_csv(trades, os.path.join(OUT_DIR, f"trades_{key}.csv"))
            bt.export_equity_csv(eq, os.path.join(OUT_DIR, f"equity_{key}.csv"))
            bt.export_loss_autopsy_csv(autopsy, os.path.join(OUT_DIR, f"loss_autopsy_{key}.csv"))
            out[key] = _metrics(trades, eq, autopsy)
            print(f"  {key}: {len(trades)} trades")
    finally:
        for k, v in saved.items():
            setattr(bt, k, v)
    return out


def print_table(m_all):
    labels = list(m_all)
    w = 12
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
        ("ExBest net", lambda m: _f(m.get("exbest_net", 0.0), pos=True)),
        ("ExBest PF", lambda m: _f(m.get("exbest_pf", float("nan")))),
        ("Top %", lambda m: _f(m.get("top_pct", float("nan")), pct=True)),
        ("Top3 %", lambda m: _f(m.get("top3_pct", float("nan")), pct=True)),
        ("BestMon %", lambda m: _f(m.get("best_month_pct", float("nan")), pct=True)),
        ("IS PF", lambda m: _f(m.get("is_pf", float("nan")))),
        ("OOS PF", lambda m: _f(m.get("oos_pf", float("nan")))),
        ("LosMFE", lambda m: _f(m.get("mfe_avg", float("nan")))),
        ("LosMAE", lambda m: _f(m.get("mae_avg", float("nan")))),
        (">=+0.5R", lambda m: f"{m.get('mfe_05', 0)}/{m.get('loss_n', 0)}"),
        (">=+1R", lambda m: f"{m.get('mfe_10', 0)}/{m.get('loss_n', 0)}"),
    ]
    print("\n" + "=" * (16 + w * len(labels)))
    print("BTC COMPRESSION TIMEFRAME FEASIBILITY (DIAGNOSTIC ONLY)")
    print("=" * (16 + w * len(labels)))
    print(f"{'Metric':<12}" + "".join(f"{lab:>{w}}" for lab in labels))
    print("-" * (12 + w * len(labels)))
    for label, fn in rows:
        print(f"{label:<12}" + "".join(f"{fn(m_all[lab]):>{w}}" for lab in labels))
    print("\nPer-year net $ (| separated):")
    yrs = sorted(set().union(*[set(m.get("by_year", {})) for m in m_all.values()]))
    for y in yrs:
        print(f"  {y}: " + "  ".join(f"{lab}={(m_all[lab].get('by_year', {}).get(y) or {}).get('net', 0):+.0f}" for lab in labels))
    print("\nVerdict flags per variant:")
    for lab in labels:
        print(f"  {lab:<12}: {', '.join(_flags(m_all[lab]))}")
    print("\nReading guide: a STABLE phenomenon repeats the *shape* (PF, ex-best PF, "
          "distribution, OOS) across 12H/8H/6H. If a shorter timeframe raises trade")
    print("count but COLLAPSES ex-best/OOS/distribution, treat it as REJECTED (noise),")
    print("not improvement. Full-compression staying tiny-sample = promising but UNVALIDATED.")
    print("Diagnostic only — no deploy/paper/live, no PF-alone advancement, no tuning.")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    m_all = {}
    for label, entry_s, gran, agg, ov in TIMEFRAMES:
        print(f"\n=== timeframe {label} ===")
        m_all.update(run_tf(label, entry_s, gran, agg, ov, args.days))
    print_table(m_all)


if __name__ == "__main__":
    main()
