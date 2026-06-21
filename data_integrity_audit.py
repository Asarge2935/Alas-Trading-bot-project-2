"""
DATA INTEGRITY AUDIT — analysis only, no strategy change.

Checks an OHLCV CSV (and optionally a second CSV for cross-source agreement)
before it is trusted for validation:
  1. Gap map        — missing-candle gaps vs the dominant cadence (count, size,
                      where, missing-bar estimate, % coverage, by year).
  2. Continuity     — duplicate/zero/negative spacings (a unit-seam or overlap
                      would show up here), spacing distribution, and a focused
                      look at the 2024-12..2025-03 window where Binance Vision
                      switched open_time ms -> us.
  3. Cross-source   — (optional) inner-join two CSVs on timestamp and compare
                      closes: median / p95 / max abs % diff and correlation
                      (catches venue/scaling/symbol artifacts).

Run:
    python3 data_integrity_audit.py --csv ~/btc.csv [--label binance_btc]
    python3 data_integrity_audit.py --csv ~/eth.csv --compare-csv ~/eth_other.csv

Outputs (backtest_output_data_integrity/<label>/): summary.txt, gaps.csv
"""

import argparse
import os
import numpy as np
import pandas as pd

from ohlcv_csv_validation_harness import load_ohlcv_csv

OUT_BASE = "backtest_output_data_integrity"
SEAM_WINDOW = ("2024-12-01", "2025-03-01")  # Binance Vision ms->us switch region


def _spacing(df):
    return df["time"].diff().dropna().dt.total_seconds()


def gap_map(df):
    sp = _spacing(df)
    med = float(sp.median())
    gaps = sp[sp > med * 1.5]
    rows = []
    times = df["time"].values
    for idx in gaps.index:
        prev = pd.Timestamp(times[idx - 1])
        cur = pd.Timestamp(times[idx])
        secs = (cur - prev).total_seconds()
        rows.append({"gap_start": prev.isoformat(), "gap_end": cur.isoformat(),
                     "gap_seconds": int(secs), "missing_bars_est": int(round(secs / med)) - 1})
    span = (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds()
    expected = int(span / med) + 1
    coverage = len(df) / expected * 100 if expected else float("nan")
    return med, rows, coverage, expected


def continuity(df):
    sp = _spacing(df)
    out = {}
    out["min_spacing_s"] = int(sp.min()) if len(sp) else 0
    out["nonpositive_spacings"] = int((sp <= 0).sum())   # dups/overlap/seam error -> must be 0
    vc = sp.round().astype(int).value_counts().head(5)
    out["top_spacings"] = [(int(k), int(v)) for k, v in vc.items()]
    seam = df[(df["time"] >= pd.Timestamp(SEAM_WINDOW[0], tz="UTC")) &
              (df["time"] < pd.Timestamp(SEAM_WINDOW[1], tz="UTC"))]
    if len(seam) > 2:
        ssp = seam["time"].diff().dropna().dt.total_seconds()
        out["seam_window_bars"] = len(seam)
        out["seam_min_spacing_s"] = int(ssp.min())
        out["seam_nonpositive"] = int((ssp <= 0).sum())
        out["seam_anomalies"] = int((ssp > ssp.median() * 1.5).sum())
    else:
        out["seam_window_bars"] = len(seam)
    return out


def cross_source(df_a, df_b):
    a = df_a[["time", "close"]].rename(columns={"close": "close_a"})
    b = df_b[["time", "close"]].rename(columns={"close": "close_b"})
    m = a.merge(b, on="time", how="inner")
    if m.empty:
        return {"overlap": 0}
    pct = (m["close_a"] - m["close_b"]).abs() / m["close_b"] * 100
    return {"overlap": len(m),
            "median_abs_pct": float(pct.median()), "p95_abs_pct": float(pct.quantile(0.95)),
            "max_abs_pct": float(pct.max()), "corr": float(m["close_a"].corr(m["close_b"]))}


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--csv", required=True)
    p.add_argument("--compare-csv", default=None)
    p.add_argument("--label", default=None)
    args = p.parse_args()
    label = args.label or os.path.splitext(os.path.basename(args.csv))[0]
    out_dir = os.path.join(OUT_BASE, label)
    os.makedirs(out_dir, exist_ok=True)

    L = ["=" * 84, f"DATA INTEGRITY AUDIT — {args.csv}  (label='{label}', DIAGNOSTIC ONLY)", "=" * 84]
    try:
        df, errs = load_ohlcv_csv(args.csv)
    except Exception as e:
        print(f"REJECTED: {e}")
        return
    L.append(f"  rows={len(df)}  {df['time'].min()} -> {df['time'].max()}")
    for w in errs:
        L.append(f"  loader warning: {w}")

    med, gaps, coverage, expected = gap_map(df)
    L.append("\n-- GAP MAP --")
    L.append(f"  dominant cadence ~{med:.0f}s   coverage={coverage:.2f}% "
             f"({len(df)} of ~{expected} expected bars)")
    L.append(f"  gaps (> 1.5x cadence): {len(gaps)}   "
             f"total missing bars (est): {sum(g['missing_bars_est'] for g in gaps)}")
    for g in sorted(gaps, key=lambda x: -x["gap_seconds"])[:8]:
        L.append(f"    {g['gap_start']} -> {g['gap_end']}  "
                 f"{g['gap_seconds']}s (~{g['missing_bars_est']} bars)")
    by_year = {}
    for g in gaps:
        y = g["gap_start"][:4]
        by_year[y] = by_year.get(y, 0) + 1
    if by_year:
        L.append("  gaps by year: " + "  ".join(f"{y}:{c}" for y, c in sorted(by_year.items())))
    pd.DataFrame(gaps).to_csv(os.path.join(out_dir, "gaps.csv"), index=False)

    c = continuity(df)
    L.append("\n-- CONTINUITY / UNIT-SEAM --")
    L.append(f"  min spacing={c['min_spacing_s']}s   nonpositive spacings (dups/overlap)={c['nonpositive_spacings']}  "
             f"{'OK' if c['nonpositive_spacings'] == 0 else 'PROBLEM'}")
    L.append(f"  top spacings (s:count): {c['top_spacings']}")
    if c.get("seam_window_bars", 0) > 2:
        seam_ok = c["seam_nonpositive"] == 0
        L.append(f"  ms->us seam window {SEAM_WINDOW[0]}..{SEAM_WINDOW[1]}: {c['seam_window_bars']} bars, "
                 f"min spacing {c['seam_min_spacing_s']}s, nonpositive {c['seam_nonpositive']}, "
                 f"anomalies {c['seam_anomalies']}  -> {'CLEAN' if seam_ok else 'SEAM PROBLEM'}")
    else:
        L.append(f"  ms->us seam window: only {c.get('seam_window_bars',0)} bars in range (skipped)")

    if args.compare_csv:
        L.append("\n-- CROSS-SOURCE CLOSE AGREEMENT --")
        try:
            df_b, _ = load_ohlcv_csv(args.compare_csv)
            cs = cross_source(df, df_b)
        except Exception as e:
            L.append(f"  compare-csv REJECTED: {e}")
            cs = None
        if cs and cs["overlap"]:
            verdict = ("CONSISTENT" if cs["median_abs_pct"] < 1.0 and cs["corr"] > 0.999
                       else "DIVERGENT — investigate venue/scaling/symbol")
            L.append(f"  overlap bars={cs['overlap']}  median|Δ|={cs['median_abs_pct']:.3f}%  "
                     f"p95={cs['p95_abs_pct']:.3f}%  max={cs['max_abs_pct']:.3f}%  "
                     f"corr={cs['corr']:.5f}  -> {verdict}")
            L.append("  (small spot differences between venues are normal; large/structured "
                     "diffs or low corr indicate a data artifact.)")
        elif cs is not None:
            L.append("  no overlapping timestamps — cannot compare.")

    L.append("\n  DIAGNOSTIC ONLY. Integrity is a precondition for trusting validation, not a")
    L.append("  deployment signal. Gaps are reported, never forward-filled.")
    report = "\n".join(L)
    print(report)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote {out_dir}/summary.txt and gaps.csv")


if __name__ == "__main__":
    main()
