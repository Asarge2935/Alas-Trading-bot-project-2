"""
ETH breakout QUALITY AUDIT — diagnostic only, no strategy change.

Purpose: find what separates WINNING ETH long-breakout trades from LOSING ones,
BEFORE adding any filter. Runs the 6H ETH-long-only strict-regime breakout
(unchanged engine), extracts a rich feature row per trade, and prints a
winner-vs-loser comparison (averages, medians, min/max). It recommends nothing
and changes nothing — it is purely descriptive.

Run:
    python3 eth_breakout_quality.py [--days N]

Outputs:
    backtest_output_eth_breakout_quality/eth_breakout_quality.csv
    backtest_output_eth_breakout_quality/summary.txt   (same as the printed report)
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt

OUT_DIR = "backtest_output_eth_breakout_quality"
LOW_SAMPLE = 10

# Numeric features compared between winners and losers.
NUMERIC = [
    "net_pnl_usd", "r_multiple", "bars_held", "eth_ema50_slope", "rs_rank",
    "eth_20d_return", "btc_20d_return", "sol_20d_return", "atr14_atr120",
    "entry_extension_atr", "volume_ratio", "candle_body_pct", "close_loc_in_range",
    "breakout_dist_atr", "prior_range_pct", "dist_from_ema20_atr",
    "dist_from_ema50_atr", "MFE_R", "MAE_R", "bars_to_MFE", "bars_to_MAE",
]


def _nz(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else x


def build_quality_rows(days):
    data = {}
    for sym in bt.ASSETS:
        df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)
        data[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
    trades, _ = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                                strict_regime=True, setup="breakout")
    indexed = {s: d.set_index("time") for s, d in data.items()}
    daily_close = {s: bt._daily_close(indexed[s]) for s in indexed}
    rs_by_day = bt.compute_rs(daily_close)
    dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    rows = []
    for t in trades:
        sym_idx = indexed[t.symbol]
        mfe_r, mae_r, b_mfe, b_mae = bt._trade_excursions(t, sym_idx)
        sig = sym_idx.loc[t.signal_candle_time] if t.signal_candle_time in sym_idx.index else None
        day = t.signal_candle_time.normalize()
        rs_row = rs_by_day.loc[day] if day in rs_by_day.index else None

        atr = t.atr_at_signal
        rng = (sig["high"] - sig["low"]) if sig is not None else float("nan")
        def safe(v, d=atr):
            return v if (sig is not None and not pd.isna(v) and d not in (0, None) and not pd.isna(d)) else float("nan")

        eth_uptrend = (sig is not None and sig["close"] > sig["ema_50"] and sig["ema_50_slope"] > 0)
        rs_rank = float("nan")
        eth20 = btc20 = sol20 = float("nan")
        if rs_row is not None:
            valid = rs_row.dropna()
            if t.symbol in valid.index:
                rs_rank = int(valid.rank(ascending=False)[t.symbol])
            eth20 = rs_row.get("ETH-USD", float("nan"))
            btc20 = rs_row.get("BTC-USD", float("nan"))
            sol20 = rs_row.get("SOL-USD", float("nan"))
        atr_ratio = (sig["atr_14"] / sig["atr_regime_avg"]) if (sig is not None and sig["atr_regime_avg"]) else float("nan")
        vol_regime = "unknown"
        if atr_ratio == atr_ratio:  # not NaN
            vol_regime = "low" if atr_ratio < 0.75 else ("high" if atr_ratio > 1.25 else "normal")
        hour = t.entry_time.hour

        rows.append({
            "setup_type": t.setup_type,
            "entry_time": t.entry_time.isoformat(),
            "exit_time": t.exit_time.isoformat() if t.exit_time is not None else "",
            "exit_reason": t.exit_reason,
            "net_pnl_usd": round(t.net_pnl_usd, 4),
            "r_multiple": round(t.r_multiple, 3),
            "bars_held": t.bars_held,
            "btc_regime": t.regime_state,
            "eth_self_uptrend": bool(eth_uptrend),
            "eth_ema50_slope": round(float(sig["ema_50_slope"]), 4) if sig is not None and not pd.isna(sig["ema_50_slope"]) else "",
            "rs_rank": rs_rank,
            "eth_20d_return": round(float(eth20), 4) if eth20 == eth20 else "",
            "btc_20d_return": round(float(btc20), 4) if btc20 == btc20 else "",
            "sol_20d_return": round(float(sol20), 4) if sol20 == sol20 else "",
            "atr14_atr120": round(float(atr_ratio), 3) if atr_ratio == atr_ratio else "",
            "volatility_regime": vol_regime,
            "entry_extension_atr": round((t.entry_price - sig["ema_50"]) / atr, 3) if sig is not None and atr else "",
            "volume_ratio": round(float(sig["volume"] / sig["volume_avg_20"]), 3) if sig is not None and sig["volume_avg_20"] else "",
            "candle_body_pct": round(abs(sig["close"] - sig["open"]) / rng, 3) if sig is not None and rng else "",
            "close_loc_in_range": round((sig["close"] - sig["low"]) / rng, 3) if sig is not None and rng else "",
            "breakout_dist_atr": round((sig["close"] - sig["prior_high"]) / atr, 3) if sig is not None and atr and not pd.isna(sig["prior_high"]) else "",
            "prior_range_pct": round((sig["prior_high"] - sig["prior_low"]) / sig["close"] * 100, 3) if sig is not None and not pd.isna(sig["prior_high"]) and not pd.isna(sig["prior_low"]) else "",
            "dist_from_ema20_atr": round((sig["close"] - sig["ema_20"]) / atr, 3) if sig is not None and atr and not pd.isna(sig["ema_20"]) else "",
            "dist_from_ema50_atr": round((sig["close"] - sig["ema_50"]) / atr, 3) if sig is not None and atr else "",
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
        return ("n/a", "n/a", "n/a", "n/a")
    return (round(a.mean(), 3), round(a.median(), 3), round(a.min(), 3), round(a.max(), 3))


def render_report(rows):
    lines = []
    n = len(rows)
    wins = [r for r in rows if r["is_winner"]]
    losses = [r for r in rows if not r["is_winner"]]
    lines.append("=" * 92)
    lines.append("ETH BREAKOUT QUALITY AUDIT — 6H, ETH-long-only, strict regime (DIAGNOSTIC ONLY)")
    lines.append("=" * 92)
    lines.append(f"  trades={n}  winners={len(wins)}  losers={len(losses)}")
    if len(wins) < LOW_SAMPLE or len(losses) < LOW_SAMPLE:
        lines.append(f"  LOW_SAMPLE WARNING: winners or losers < {LOW_SAMPLE}; "
                     f"differences below are anecdotal, not statistically meaningful.")
    if n < 30:
        lines.append("  NOTE: < 30 trades total — below the §9 sample gate; descriptive only.")
    lines.append("")
    lines.append(f"  {'feature':<22}{'win_avg':>10}{'win_med':>10}{'los_avg':>10}"
                 f"{'los_med':>10}{'win_min':>9}{'win_max':>9}{'los_min':>9}{'los_max':>9}")
    lines.append("  " + "-" * 90)
    for col in NUMERIC:
        wa, wm, wmin, wmax = _stats([r[col] for r in wins])
        la, lm, lmin, lmax = _stats([r[col] for r in losses])
        lines.append(f"  {col:<22}{str(wa):>10}{str(wm):>10}{str(la):>10}{str(lm):>10}"
                     f"{str(wmin):>9}{str(wmax):>9}{str(lmin):>9}{str(lmax):>9}")
    # Categorical splits.
    lines.append("")
    for cat in ("volatility_regime", "time_bucket_utc", "day_of_week", "exit_reason", "rs_rank"):
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
    lines.append("  This audit recommends NO filter and NO deployment. It only describes "
                 "differences to inform later, separately-tested hypotheses.")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()

    rows = build_quality_rows(args.days)
    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, "eth_breakout_quality.csv")
    if rows:
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        print(f"Exported {len(rows)} ETH breakout trades to {csv_path}")
    else:
        print("No ETH breakout trades in this window — nothing to audit.")
        return
    report = render_report(rows)
    print("\n" + report)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote summary to {os.path.join(OUT_DIR, 'summary.txt')}")


if __name__ == "__main__":
    main()
