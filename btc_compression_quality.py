"""
BTC COMPRESSION-BREAKOUT QUALITY AUDIT — diagnostic only, no strategy change.

Runs the BTC 12H compression breakout (reusing btc_compression_diagnostic.py's
exact build + loop, unchanged) and exports a per-trade feature row, then prints
a winner-vs-loser comparison (averages + medians). Recommends NO filter and NO
deployment. It only describes differences to inform later, separately-tested
hypotheses.

Run:
    python3 btc_compression_quality.py [--days N]

Outputs:
    backtest_output_btc_compression_quality/btc_compression_quality.csv
    backtest_output_btc_compression_quality/summary.txt
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt
import btc_compression_diagnostic as bc

OUT_DIR = "backtest_output_btc_compression_quality"
LOW_SAMPLE = 10
TF_OVERRIDES = dict(bc.TF_CONFIGS[0][4])   # the 12H overrides
ENTRY_SECONDS, FETCH_GRAN, AGG = 43200, 21600, "12h"

NUMERIC = [
    "net_pnl_usd", "r_multiple", "bars_held", "comp_count",
    "atr14_atr120", "range20_pctl", "bbw_pctl", "breakout_dist_atr",
    "breakout_body_pct", "breakout_close_loc", "breakout_vol_ratio",
    "dist_from_ema50_atr", "dist_from_ema200_atr", "daily_ema50_slope",
    "daily_ema200_slope", "MFE_R", "MAE_R", "bars_to_MFE", "bars_to_MAE",
]


def _pctl_rank(series, window):
    return series.rolling(window).apply(lambda w: (w <= w.iloc[-1]).mean(), raw=False)


def build_rows(days):
    saved = {k: getattr(bt, k) for k in TF_OVERRIDES}
    for k, v in TF_OVERRIDES.items():
        setattr(bt, k, v)
    try:
        df = bc._build_btc_df(ENTRY_SECONDS, FETCH_GRAN, AGG, days)
        # Report-only percentile values (aligned to the prior bar, like the gate).
        range20 = df["high"].rolling(20).max() - df["low"].rolling(20).min()
        sma20 = df["close"].rolling(20).mean()
        bbw = (4.0 * df["close"].rolling(20).std()) / sma20
        df["range20_pctl_prev"] = _pctl_rank(range20, bc.COMPRESSION_LOOKBACK).shift(1)
        df["bbw_pctl_prev"] = _pctl_rank(bbw, bc.COMPRESSION_LOOKBACK).shift(1)
        daily = bc.daily_htf_features(df)
        norm = df["time"].dt.normalize()
        for col in ("d_ema200", "d_ema50_slope", "d_ema200_slope"):
            df[col] = norm.map(daily[col])
        btc_indexed = df.set_index("time")
        trades, _ = bc._run_btc_compression(btc_indexed)
    finally:
        for k, v in saved.items():
            setattr(bt, k, v)

    dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    rows = []
    for t in trades:
        mfe_r, mae_r, b_mfe, b_mae = bt._trade_excursions(t, btc_indexed)
        s = btc_indexed.loc[t.signal_candle_time] if t.signal_candle_time in btc_indexed.index else None
        atr = t.atr_at_signal
        rng = (s["high"] - s["low"]) if s is not None else float("nan")
        hour = t.entry_time.hour

        def col(v):
            return round(float(v), 4) if (s is not None and not pd.isna(v)) else ""

        rows.append({
            "entry_time": t.entry_time.isoformat(),
            "exit_time": t.exit_time.isoformat() if t.exit_time is not None else "",
            "exit_reason": t.exit_reason,
            "net_pnl_usd": round(t.net_pnl_usd, 4),
            "r_multiple": round(t.r_multiple, 3),
            "bars_held": t.bars_held,
            "comp_atr": bool(s["comp_atr_prev"]) if s is not None else "",
            "comp_range": bool(s["comp_range_prev"]) if s is not None else "",
            "comp_bb": bool(s["comp_bb_prev"]) if s is not None else "",
            "comp_count": int(s["comp_count_prev"]) if s is not None else "",
            "atr14_atr120": col(s["atr_14"] / s["atr_regime_avg"]) if s is not None and s["atr_regime_avg"] else "",
            "range20_pctl": col(s["range20_pctl_prev"]) if s is not None else "",
            "bbw_pctl": col(s["bbw_pctl_prev"]) if s is not None else "",
            "breakout_dist_atr": round((s["close"] - s["prior_high"]) / atr, 3) if s is not None and atr and not pd.isna(s["prior_high"]) else "",
            "breakout_body_pct": round(abs(s["close"] - s["open"]) / rng, 3) if s is not None and rng else "",
            "breakout_close_loc": round((s["close"] - s["low"]) / rng, 3) if s is not None and rng else "",
            "breakout_vol_ratio": round(s["volume"] / s["volume_avg_20"], 3) if s is not None and s["volume_avg_20"] else "",
            "close_gt_ema200": bool(s["close"] > s["ema_200"]) if s is not None and not pd.isna(s["ema_200"]) else "",
            "dist_from_ema50_atr": round((s["close"] - s["ema_50"]) / atr, 3) if s is not None and atr else "",
            "dist_from_ema200_atr": round((s["close"] - s["ema_200"]) / atr, 3) if s is not None and atr and not pd.isna(s["ema_200"]) else "",
            "daily_ema50_slope": col(s["d_ema50_slope"]) if s is not None else "",
            "daily_ema200_slope": col(s["d_ema200_slope"]) if s is not None else "",
            "time_bucket_utc": f"{(hour // 4) * 4:02d}:00-{((hour // 4) * 4 + 4) % 24:02d}:00",
            "day_of_week": dow[t.entry_time.dayofweek],
            "MFE_R": round(mfe_r, 3),
            "MAE_R": round(mae_r, 3),
            "bars_to_MFE": b_mfe,
            "bars_to_MAE": b_mae,
            "is_winner": t.net_pnl_usd > 0,
        })
    return rows


def _stats(vals):
    a = pd.to_numeric(pd.Series(vals), errors="coerce").dropna()
    if a.empty:
        return ("n/a", "n/a")
    return (round(a.mean(), 3), round(a.median(), 3))


def render(rows):
    lines = ["=" * 92,
             "BTC COMPRESSION-BREAKOUT QUALITY AUDIT — 12H, BTC-USD long (DIAGNOSTIC ONLY)",
             "=" * 92]
    n = len(rows)
    wins = [r for r in rows if r["is_winner"]]
    losses = [r for r in rows if not r["is_winner"]]
    lines.append(f"  trades={n}  winners={len(wins)}  losers={len(losses)}")
    if n < 30 or len(wins) < LOW_SAMPLE or len(losses) < LOW_SAMPLE:
        lines.append("  *** LOW_SAMPLE: too few trades/winners/losers for any statistical claim. ***")
        lines.append("  *** Differences below are ANECDOTAL. Do NOT derive a filter from them. ***")
    lines.append("")
    lines.append(f"  {'feature':<22}{'win_avg':>11}{'win_med':>11}{'los_avg':>11}{'los_med':>11}")
    lines.append("  " + "-" * 66)
    for c in NUMERIC:
        wa, wm = _stats([r[c] for r in wins])
        la, lm = _stats([r[c] for r in losses])
        lines.append(f"  {c:<22}{str(wa):>11}{str(wm):>11}{str(la):>11}{str(lm):>11}")
    lines.append("")
    for cat in ("comp_count", "close_gt_ema200", "exit_reason", "time_bucket_utc", "day_of_week"):
        lines.append(f"  win rate by {cat}:")
        buckets = {}
        for r in rows:
            buckets.setdefault(r[cat], [0, 0])
            buckets[r[cat]][0 if r["is_winner"] else 1] += 1
        for k in sorted(buckets, key=lambda x: str(x)):
            w, l = buckets[k]
            tot = w + l
            flag = "  LOW_SAMPLE" if tot < LOW_SAMPLE else ""
            lines.append(f"    {str(k):<16} {w}W/{l}L  win%={w / tot * 100:.0f}%{flag}")
    lines.append("")
    lines.append("  NO filter is recommended and NO deployment is implied. This audit is")
    lines.append("  descriptive only; any idea it suggests must be tested separately and")
    lines.append("  must still clear the deployment gates on real data.")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()

    rows = build_rows(args.days)
    os.makedirs(OUT_DIR, exist_ok=True)
    if not rows:
        print("No BTC 12H compression trades in this window — nothing to audit.")
        return
    pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, "btc_compression_quality.csv"), index=False)
    print(f"Exported {len(rows)} trades to {os.path.join(OUT_DIR, 'btc_compression_quality.csv')}")
    report = render(rows)
    print("\n" + report)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote {os.path.join(OUT_DIR, 'summary.txt')}")


if __name__ == "__main__":
    main()
