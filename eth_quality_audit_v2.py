"""
ETH QUALITY AUDIT v2 — diagnostic only. Analyzes the EXISTING ETH-USD long-only
6H strict-regime BREAKOUT setup. No pullbacks, no rule changes, no filters, no
deployment. Descriptive / hypothesis-generating only.

Because the ETH breakout sample is small, every section prints LOW_SAMPLE
warnings; differences are ANECDOTAL and must not drive a filter or deployment.

Run:
    python3 eth_quality_audit_v2.py [--days N]

Outputs (backtest_output_eth_quality_v2/):
    trades.csv            full ETH breakout trade list (incl. setup_type)
    features.csv          per-trade audit features (excursion path, context)
    monthly.csv           monthly net P&L table
    summary.txt           the full printed report
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt

OUT_DIR = "backtest_output_eth_quality_v2"
LOW_SAMPLE = 10
WINNER_GATE = 12


def _pf(trades):
    gw = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
    gl = abs(sum(t.net_pnl_usd for t in trades if t.net_pnl_usd <= 0))
    return gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)


def _fpf(x):
    return "inf" if x == float("inf") else ("n/a" if x != x else f"{x:.2f}")


def _excursion_path(trade, sym_indexed):
    idx = sym_indexed.index
    bars = sym_indexed.loc[(idx >= trade.entry_time) & (idx <= trade.exit_time)]
    R = trade.initial_stop_distance
    res = {"MFE_R": 0.0, "MAE_R": 0.0, "bars_to_MFE": 0, "bars_to_MAE": 0,
           "bars_to_0_5R": "", "bars_to_1R": "", "bars_to_adv_0_5R": ""}
    if bars.empty or R <= 0:
        return res
    best_fav = best_adv = None
    for i, (_, row) in enumerate(bars.iterrows()):
        favR = (row["high"] - trade.entry_price) / R       # long-only setup
        advR = max(trade.entry_price - row["low"], 0.0) / R
        if best_fav is None or favR > best_fav:
            best_fav, res["bars_to_MFE"] = favR, i
        if best_adv is None or advR > best_adv:
            best_adv, res["bars_to_MAE"] = advR, i
        if res["bars_to_0_5R"] == "" and favR >= 0.5:
            res["bars_to_0_5R"] = i
        if res["bars_to_1R"] == "" and favR >= 1.0:
            res["bars_to_1R"] = i
        if res["bars_to_adv_0_5R"] == "" and advR >= 0.5:
            res["bars_to_adv_0_5R"] = i
    res["MFE_R"], res["MAE_R"] = round(best_fav, 3), round(best_adv, 3)
    return res


def _btc_regime_by_day(btc_indexed):
    s = btc_indexed["close"].resample("1D").last().dropna()
    e50 = s.ewm(span=50, adjust=False).mean()
    e200 = s.ewm(span=200, adjust=False).mean()
    sl50 = e50 / e50.shift(bt.REGIME_SLOPE_DAYS) - 1.0
    out = {}
    for day in s.index:
        c, e2, sl = s[day], e200.get(day, np.nan), sl50.get(day, np.nan)
        if pd.isna(e2) or pd.isna(sl):
            out[day] = "unknown"
        elif c > e2 and sl > 0:
            out[day] = "bull"
        elif c < e2 and sl < 0:
            out[day] = "bear"
        else:
            out[day] = "range"
    return pd.Series(out).shift(1)   # prior completed day, no look-ahead


def build(days):
    data = {}
    for sym in bt.ASSETS:
        df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)
        data[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
    trades, eq = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                                 strict_regime=True, setup="breakout")
    indexed = {s: d.set_index("time") for s, d in data.items()}
    eth = indexed["ETH-USD"]
    btc_reg = _btc_regime_by_day(indexed["BTC-USD"])
    daily_close = {s: bt._daily_close(indexed[s]) for s in indexed}
    rs_by_day = bt.compute_rs(daily_close)
    dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    rows = []
    for t in trades:
        ex = _excursion_path(t, eth)
        s = eth.loc[t.signal_candle_time] if t.signal_candle_time in eth.index else None
        day = t.signal_candle_time.normalize()
        rs_row = rs_by_day.loc[day] if day in rs_by_day.index else None
        atr = t.atr_at_signal
        rng = (s["high"] - s["low"]) if s is not None else np.nan
        rs_rank = ""
        eth20 = btc20 = np.nan
        if rs_row is not None:
            v = rs_row.dropna()
            if t.symbol in v.index:
                rs_rank = int(v.rank(ascending=False)[t.symbol])
            eth20, btc20 = rs_row.get("ETH-USD", np.nan), rs_row.get("BTC-USD", np.nan)
        atr_ratio = (s["atr_14"] / s["atr_regime_avg"]) if (s is not None and s["atr_regime_avg"]) else np.nan
        vol_state = "unknown" if atr_ratio != atr_ratio else ("low" if atr_ratio < 0.75 else "high" if atr_ratio > 1.25 else "normal")
        b0 = ex["bars_to_0_5R"]
        ba = ex["bars_to_adv_0_5R"]
        b1 = ex["bars_to_1R"]
        reached_05_before_mae = isinstance(b0, int) and (ba == "" or b0 <= ba)
        reached_1_before_mae = isinstance(b1, int) and (ba == "" or b1 <= ba)
        hour = t.entry_time.hour
        rows.append({
            "setup_type": t.setup_type, "entry_time": t.entry_time.isoformat(),
            "exit_time": t.exit_time.isoformat() if t.exit_time is not None else "",
            "exit_reason": t.exit_reason, "net_pnl_usd": round(t.net_pnl_usd, 4),
            "r_multiple": round(t.r_multiple, 3), "bars_held": t.bars_held,
            "is_winner": t.net_pnl_usd > 0,
            "MFE_R": ex["MFE_R"], "MAE_R": ex["MAE_R"],
            "bars_to_MFE": ex["bars_to_MFE"], "bars_to_MAE": ex["bars_to_MAE"],
            "bars_to_0_5R": b0, "bars_to_1R": b1, "bars_to_adv_0_5R": ba,
            "reached_0_5R_before_mae": reached_05_before_mae,
            "reached_1R_before_mae": reached_1_before_mae,
            "btc_regime": btc_reg.get(day, "unknown"),
            "btc_strict_state": t.regime_state,
            "volatility_state": vol_state,
            "atr14_atr120": round(float(atr_ratio), 3) if atr_ratio == atr_ratio else "",
            "eth_btc_rs": round(float(eth20 - btc20), 4) if (eth20 == eth20 and btc20 == btc20) else "",
            "btc_20d_return": round(float(btc20), 4) if btc20 == btc20 else "",
            "rs_rank": rs_rank,
            "breakout_vol_ratio": round(s["volume"] / s["volume_avg_20"], 3) if s is not None and s["volume_avg_20"] else "",
            "breakout_dist_atr": round((s["close"] - s["prior_high"]) / atr, 3) if s is not None and atr and not pd.isna(s["prior_high"]) else "",
            "breakout_body_pct": round(abs(s["close"] - s["open"]) / rng, 3) if s is not None and rng else "",
            "breakout_close_loc": round((s["close"] - s["low"]) / rng, 3) if s is not None and rng else "",
            "dist_from_ema50_atr": round((s["close"] - s["ema_50"]) / atr, 3) if s is not None and atr else "",
            "dist_from_ema20_atr": round((s["close"] - s["ema_20"]) / atr, 3) if s is not None and atr and not pd.isna(s["ema_20"]) else "",
            "time_bucket_utc": f"{(hour // 4) * 4:02d}:00-{((hour // 4) * 4 + 4) % 24:02d}:00",
            "day_of_week": dow[t.entry_time.dayofweek],
        })
    return trades, eq, rows, eth


def _stats(vals):
    a = pd.to_numeric(pd.Series(vals), errors="coerce").dropna()
    if a.empty:
        return ("n/a", "n/a")
    return (round(a.mean(), 3), round(a.median(), 3))


def render(trades, eq, rows):
    L = []
    n = len(trades)
    wins = [t for t in trades if t.net_pnl_usd > 0]
    losses = [t for t in trades if t.net_pnl_usd <= 0]
    net = sum(t.net_pnl_usd for t in trades)
    L.append("=" * 92)
    L.append("ETH QUALITY AUDIT v2 — 6H, ETH-USD long-only, strict regime, BREAKOUT (DIAGNOSTIC ONLY)")
    L.append("=" * 92)
    if n == 0:
        L.append("  No ETH breakout trades in this window.")
        return "\n".join(L)
    if n < 30 or len(wins) < WINNER_GATE:
        L.append(f"  *** LOW_SAMPLE: {n} trades, {len(wins)} winners (gates: >=30 trades, "
                 f">=12-15 winners). Everything below is ANECDOTAL / hypothesis-generating. ***")
        L.append("  *** No filter and no deployment is recommended or implied. ***")

    # --- Core baseline metrics ---
    by_exit = sorted(trades, key=lambda t: (t.exit_time or t.entry_time))
    mcl = cur = 0
    for t in by_exit:
        cur = cur + 1 if t.net_pnl_usd <= 0 else 0
        mcl = max(mcl, cur)
    t0 = min(t.entry_time for t in trades)
    t1 = max(t.entry_time for t in trades)
    split = t0 + (t1 - t0) * bt.OOS_SPLIT_FRAC
    is_t = [t for t in trades if t.entry_time <= split]
    oos_t = [t for t in trades if t.entry_time > split]
    nets = sorted((t.net_pnl_usd for t in trades), reverse=True)
    best = max(trades, key=lambda t: t.net_pnl_usd)
    ex_best = [t for t in trades if t is not best]
    max_dd = max((p["drawdown_pct"] for p in eq), default=0.0)
    L.append("\n-- CORE BASELINE --")
    L.append(f"  trades={n}  W/L={len(wins)}/{len(losses)}  win%={len(wins)/n*100:.1f}")
    L.append(f"  PF={_fpf(_pf(trades))}  net=${net:+.2f}  avgR={np.mean([t.r_multiple for t in trades]):+.3f}  maxDD={max_dd:.2f}%")
    L.append(f"  max consec losses={mcl}")
    L.append(f"  IS PF={_fpf(_pf(is_t))} ({len(is_t)})   OOS PF={_fpf(_pf(oos_t))} ({len(oos_t)})")
    L.append(f"  ex-best: net=${sum(t.net_pnl_usd for t in ex_best):+.2f}  PF={_fpf(_pf(ex_best))}  "
             f"avgR={np.mean([t.r_multiple for t in ex_best]):+.3f}" if ex_best else "  ex-best: n/a")
    if net > 0:
        L.append(f"  top trade % of net={best.net_pnl_usd/net*100:.1f}%   top-3 % of net={sum(nets[:3])/net*100:.1f}%")
    years = {}
    for t in trades:
        years.setdefault(t.entry_time.year, []).append(t)
    L.append("  yearly: " + "  ".join(f"{y}: n={len(v)} net=${sum(x.net_pnl_usd for x in v):+.1f} PF={_fpf(_pf(v))}"
                                       for y, v in sorted(years.items())))

    # --- Time-to-expansion / acceleration ---
    L.append("\n-- TIME-TO-EXPANSION / BREAKOUT ACCELERATION (win vs loss avg|median) --")
    for col in ("bars_to_0_5R", "bars_to_1R", "bars_to_MFE", "bars_to_MAE", "MFE_R", "MAE_R", "bars_held"):
        wa, wm = _stats([r[col] for r in rows if r["is_winner"]])
        la, lm = _stats([r[col] for r in rows if not r["is_winner"]])
        L.append(f"  {col:<16} win {wa}|{wm}   loss {la}|{lm}")
    w05 = sum(1 for r in rows if r["is_winner"] and r["reached_0_5R_before_mae"])
    l05 = sum(1 for r in rows if not r["is_winner"] and r["reached_0_5R_before_mae"])
    L.append(f"  reached +0.5R before meaningful (0.5R) MAE: winners {w05}/{len(wins)}, losers {l05}/{len(losses)}")
    w1 = sum(1 for r in rows if r["is_winner"] and r["reached_1R_before_mae"])
    L.append(f"  reached +1R before meaningful MAE: winners {w1}/{len(wins)}")
    wbar = _stats([r["bars_to_MFE"] for r in rows if r["is_winner"]])[0]
    lbar = _stats([r["bars_to_MFE"] for r in rows if not r["is_winner"]])[0]
    L.append(f"  HYPOTHESIS (anecdotal): winners bars_to_MFE avg={wbar} vs losers={lbar} "
             f"-> {'winners separate faster' if isinstance(wbar,(int,float)) and isinstance(lbar,(int,float)) and wbar<lbar else 'no clear separation'}")

    # --- Distribution stability ---
    L.append("\n-- DISTRIBUTION STABILITY --")
    mser = {}
    for t in trades:
        key = (t.exit_time or t.entry_time).strftime("%Y-%m")
        mser[key] = mser.get(key, 0.0) + t.net_pnl_usd
    best_month = max(mser.values()) if mser else 0.0
    if net > 0:
        L.append(f"  top trade % of net={best.net_pnl_usd/net*100:.1f}%   top-3={sum(nets[:3])/net*100:.1f}%   best month % of net={best_month/net*100:.1f}%")
    seq = [t.net_pnl_usd for t in by_exit]
    def worst_window(k):
        return min((sum(seq[i:i+k]) for i in range(0, max(1, len(seq)-k+1))), default=0.0)
    L.append(f"  worst rolling 3-trade net=${worst_window(3):+.2f}   worst rolling 5-trade net=${worst_window(5):+.2f}")
    L.append("  monthly net P&L:")
    for k in sorted(mser):
        L.append(f"    {k}: ${mser[k]:+.2f}")

    # --- Regime segmentation ---
    L.append("\n-- REGIME SEGMENTATION (anecdotal) --")
    for dim in ("btc_regime", "volatility_state", "btc_strict_state"):
        L.append(f"  by {dim}:")
        g = {}
        for r in rows:
            g.setdefault(r[dim], []).append(r)
        for k in sorted(g, key=str):
            grp = g[k]
            w = sum(1 for x in grp if x["is_winner"])
            netk = sum(x["net_pnl_usd"] for x in grp)
            flag = "  LOW_SAMPLE" if len(grp) < LOW_SAMPLE else ""
            L.append(f"    {str(k):<10} n={len(grp):<3} win%={w/len(grp)*100:.0f}% net=${netk:+.2f}{flag}")

    # --- Feature audit ---
    L.append("\n-- FEATURE AUDIT (winners vs losers, avg|median; ANECDOTAL) --")
    for col in ("breakout_vol_ratio", "breakout_dist_atr", "breakout_body_pct", "breakout_close_loc",
                "eth_btc_rs", "btc_20d_return", "atr14_atr120", "dist_from_ema50_atr", "dist_from_ema20_atr"):
        wa, wm = _stats([r[col] for r in rows if r["is_winner"]])
        la, lm = _stats([r[col] for r in rows if not r["is_winner"]])
        L.append(f"  {col:<20} win {wa}|{wm}   loss {la}|{lm}")
    L.append("\n  NO filter and NO deployment is recommended. ETH breakout remains the primary")
    L.append("  research lead and is NOT deployable (sample/winner gates fail). Differences are")
    L.append("  hypothesis-generating only and must be tested one at a time (no feature stacking).")
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()

    trades, eq, rows, _ = build(args.days)
    os.makedirs(OUT_DIR, exist_ok=True)
    bt.export_trades_csv(trades, os.path.join(OUT_DIR, "trades.csv"))
    if rows:
        pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, "features.csv"), index=False)
        mser = {}
        for t in trades:
            k = (t.exit_time or t.entry_time).strftime("%Y-%m")
            mser[k] = mser.get(k, 0.0) + t.net_pnl_usd
        pd.DataFrame(sorted(mser.items()), columns=["month", "net_pnl_usd"]).to_csv(
            os.path.join(OUT_DIR, "monthly.csv"), index=False)
    report = render(trades, eq, rows)
    print("\n" + report)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote {OUT_DIR}/summary.txt")


if __name__ == "__main__":
    main()
