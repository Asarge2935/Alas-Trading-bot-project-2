"""
BTC COMPRESSION-BREAKOUT DIAGNOSTIC — does NOT modify the ETH strategy or
backtest.py's engine. Standalone BTC-only loop reusing backtest.py's low-level
helpers (sizing, exits, cost model, autopsy/report exporters) and risk
framework, exactly like btc_diagnostic.py.

Hypothesis (roadmap §4.1): the passive BTC pullback model had no edge because
entry timing/regime/volatility context was wrong, not exits. This tests a
STRUCTURAL idea instead: trade BTC out of a volatility SQUEEZE on the expansion
breakout (compression -> expansion), in a BTC self-uptrend.

Diagnostic only. No live/paper/adapter/order changes. Long only first.

Universe : BTC-USD only.   Direction: LONG only.   Timeframes: 12H and 1D.
  1D native (86400); 12H aggregated from 6H (open=first/high=max/low=min/
  close=last/volume=sum; START-labeled, floor('12h') -> 00:00/12:00 UTC).

Regime (BTC self-uptrend): close > EMA50 AND EMA50 slope up.
  close > EMA200 computed REPORT-ONLY (not required).

Compression gate (measured on the PRIOR closed bar; all three reported):
  1. ATR contraction:        ATR14 / ATR120 < 0.85
  2. Range compression:      20-bar range in bottom 30% of last 120 bars
  3. Bollinger width compression: BB width (4*std20/SMA20) in bottom 30% of 120
  DEFAULT GATE: require at least 2 of 3 (COMPRESSION_MIN = 2). BB width is
  implemented cleanly here, so all three are used.

Expansion / breakout trigger (on the confirmation bar):
  - close > prior 20-bar high
  - close in the upper third of the bar range
  - candle body >= 60% of the bar range
  - volume >= 1.2 * volume_avg_20
Entry: next bar open (no look-ahead).

Stop (single documented default): the WIDER of (a) the compression-range low
  (prior 20-bar low) and (b) entry - 1.5*ATR  ->  min(prior_low, entry-1.5*ATR).
Exits/costs/risk reused unchanged (partial +1.5R, breakeven, 2.5*ATR trail,
  time stop, fees/slippage/funding, 1% risk, max-1-open). Time stop preserves
  ~72h: 6 bars @12H, 3 @1D. ATR_REGIME_PERIOD 60 @12H, 30 @1D. Bar-based
  indicators kept identical; nothing tuned.

Run:
    python3 btc_compression_diagnostic.py [--days N]

Outputs (backtest_output_btc_compression/):
    trades_{12h,1d}.csv, equity_{12h,1d}.csv,
    loss_autopsy_{12h,1d}.csv, regime_behavior_{12h,1d}.csv
    + console 12H/1D comparison (incl. compression hit rates + breakout quality).
"""

import argparse
import os
from datetime import timedelta
import numpy as np
import pandas as pd

import backtest as bt

OUT_DIR = "backtest_output_btc_compression"
BTC_EMA_LONG = 200
ATR_CONTRACTION_MAX = 0.85
COMPRESSION_PCTL = 0.30
COMPRESSION_LOOKBACK = 120
COMPRESSION_MIN = 2          # require >= 2 of 3 compression conditions (default)
BREAKOUT_VOL_MULT = 1.2
BREAKOUT_BODY_MIN = 0.60
BREAKOUT_CLOSE_LOC_MIN = 2.0 / 3.0

TF_CONFIGS = [
    ("12h", 43200, 21600, "12h",
     {"TIMEFRAME_SECONDS": 43200, "BARS_PER_DAY": 2, "TIME_STOP_BARS": 6, "ATR_REGIME_PERIOD": 60}),
    ("1d", 86400, 86400, None,
     {"TIMEFRAME_SECONDS": 86400, "BARS_PER_DAY": 1, "TIME_STOP_BARS": 3, "ATR_REGIME_PERIOD": 30}),
]


def _aggregate(df, freq):
    if df.empty:
        return df.copy()
    d = df.sort_values("time").copy()
    bucket = d["time"].dt.floor(freq)
    bucket.name = "time"
    agg = (d.groupby(bucket)
             .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                  close=("close", "last"), volume=("volume", "sum"))
             .reset_index())
    return agg[["time", "low", "high", "open", "close", "volume"]]


def compression_indicators(raw, entry_seconds):
    """Drop incomplete candles and attach the compression-breakout indicators
    (EMA50/ATR14/20-bar levels via add_indicators, EMA200, and the prior-bar
    compression flags). Shared by the fetch path and the CSV-validation path so
    both use IDENTICAL logic."""
    df = bt.add_indicators(bt.drop_incomplete_candles(raw, entry_seconds))
    df["ema_200"] = df["close"].ewm(span=BTC_EMA_LONG, adjust=False).mean()

    # Compression measures, each as of a bar's own close.
    atr_ratio = df["atr_14"] / df["atr_regime_avg"]
    range20 = df["high"].rolling(20).max() - df["low"].rolling(20).min()
    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()
    bbw = (4.0 * std20) / sma20

    comp_atr = atr_ratio < ATR_CONTRACTION_MAX
    comp_range = range20 <= range20.rolling(COMPRESSION_LOOKBACK).quantile(COMPRESSION_PCTL)
    comp_bb = bbw <= bbw.rolling(COMPRESSION_LOOKBACK).quantile(COMPRESSION_PCTL)

    # Compression must precede the breakout -> evaluate on the PRIOR closed bar.
    # shift(fill_value=False) keeps bool dtype (no NaN), avoiding the pandas
    # fillna-downcast FutureWarning; values are identical to before.
    df["comp_atr_prev"] = comp_atr.shift(1, fill_value=False)
    df["comp_range_prev"] = comp_range.shift(1, fill_value=False)
    df["comp_bb_prev"] = comp_bb.shift(1, fill_value=False)
    df["comp_count_prev"] = (df["comp_atr_prev"].astype(int)
                             + df["comp_range_prev"].astype(int)
                             + df["comp_bb_prev"].astype(int))
    return df


def _build_btc_df(entry_seconds, fetch_gran, agg_freq, days):
    raw = bt.load_candles("BTC-USD", fetch_gran, days)
    if agg_freq:
        raw = _aggregate(raw, agg_freq)
    return compression_indicators(raw, entry_seconds)


def daily_htf_features(df):
    """Daily higher-timeframe context derived from an entry-timeframe df.

    Returns a DataFrame indexed by day with d_ema50, d_ema200, d_ema50_slope,
    d_ema200_slope, shifted one day so a bar uses the PRIOR completed daily
    value (no look-ahead). Pure helper — not used by this file's main().
    """
    s = df.set_index("time")["close"].resample("1D").last().dropna()
    e50 = s.ewm(span=50, adjust=False).mean()
    e200 = s.ewm(span=BTC_EMA_LONG, adjust=False).mean()
    out = pd.DataFrame({
        "d_ema50": e50, "d_ema200": e200,
        "d_ema50_slope": e50 / e50.shift(bt.REGIME_SLOPE_DAYS) - 1.0,
        "d_ema200_slope": e200 / e200.shift(bt.REGIME_SLOPE_DAYS) - 1.0,
    })
    return out.shift(1)


def _btc_compression_entry(row):
    """Compression (prior bar) + expansion breakout (this bar), long, no look-ahead."""
    needed = ["ema_20", "ema_50", "ema_50_slope", "prior_high", "prior_low",
              "atr_14", "volume_avg_20", "comp_count_prev"]
    for c in needed:
        if pd.isna(row[c]):
            return False
    rng = row["high"] - row["low"]
    if rng <= 0:
        return False
    self_uptrend = row["close"] > row["ema_50"] and row["ema_50_slope"] > 0
    compressed = row["comp_count_prev"] >= COMPRESSION_MIN
    breakout = (row["close"] > row["prior_high"]
                and (row["close"] - row["low"]) / rng >= BREAKOUT_CLOSE_LOC_MIN
                and abs(row["close"] - row["open"]) / rng >= BREAKOUT_BODY_MIN
                and row["volume"] >= BREAKOUT_VOL_MULT * row["volume_avg_20"])
    return self_uptrend and compressed and breakout


def _run_btc_compression(btc_indexed, extra_filter=None, early_exit_fn=None):
    """Standalone BTC-only long loop (mirrors run_backtest's MTM / loss-halt /
    drawdown / single-position management), reusing backtest.py helpers.

    extra_filter: optional callable(row)->bool applied AFTER the compression
    entry. None (default) reproduces the baseline compression breakout exactly.
    early_exit_fn: optional callable(trade, bar, current_time)->exit_event|None,
    checked each bar ONLY when the normal exit logic did not fire (stop has
    priority). None (default) leaves exits unchanged. Both are used by the
    compression-next diagnostic; defaults preserve every existing caller."""
    bdf = btc_indexed.reset_index()
    pos_of = {t: i for i, t in enumerate(bdf["time"])}
    open_trades, closed, equity_curve = {}, [], []
    cum_pnl = 0.0
    peak = bt.ACCOUNT_SIZE_USD
    trades_today = 0
    last_day = last_week = None
    day_start = week_start = bt.ACCOUNT_SIZE_USD
    weekly_times = []
    dd_pause_until = None

    for current_time in btc_indexed.index:
        bar = btc_indexed.loc[current_time]
        for sym in list(open_trades):
            tr = open_trades[sym]
            ev = bt.check_exit(tr, bar, current_time)
            if ev is None and early_exit_fn is not None:
                ev = early_exit_fn(tr, bar, current_time)   # stop has priority
            if ev:
                bt.finalize_trade(tr, ev)
                closed.append(tr)
                cum_pnl += tr.net_pnl_usd
                del open_trades[sym]
        open_pnl = sum(bt.unrealized_pnl(tr, bar["close"]) for tr in open_trades.values())
        mtm = bt.ACCOUNT_SIZE_USD + cum_pnl + open_pnl
        cd, cw = current_time.date(), current_time.isocalendar()[:2]
        if last_day is None or cd != last_day:
            trades_today = 0
            day_start = mtm
            last_day = cd
        if last_week is None or cw != last_week:
            week_start = mtm
            last_week = cw
        weekly_times = [t for t in weekly_times if (current_time - t).days < 7]
        peak = max(peak, mtm)
        dd = (peak - mtm) / peak * 100 if peak > 0 else 0
        if dd >= bt.DRAWDOWN_STOP_PCT:
            equity_curve.append(bt._equity_point(current_time, mtm, cum_pnl, open_trades, dd))
            return closed, equity_curve
        if dd >= bt.DRAWDOWN_PAUSE_PCT and dd_pause_until is None:
            dd_pause_until = current_time + timedelta(days=bt.DRAWDOWN_PAUSE_DAYS)
        equity_curve.append(bt._equity_point(current_time, mtm, cum_pnl, open_trades, dd))
        if dd_pause_until and current_time < dd_pause_until:
            continue
        elif dd_pause_until and current_time >= dd_pause_until:
            dd_pause_until = None
        if len(open_trades) >= bt.MAX_OPEN_POSITIONS:
            continue
        if len(weekly_times) >= bt.MAX_TRADES_PORTFOLIO_PER_WEEK:
            continue
        if day_start > 0 and (mtm - day_start) / day_start * 100 <= -bt.MAX_DAILY_LOSS_PCT:
            continue
        if week_start > 0 and (mtm - week_start) / week_start * 100 <= -bt.MAX_WEEKLY_LOSS_PCT:
            continue
        if trades_today >= bt.MAX_TRADES_PER_ASSET_PER_DAY:
            continue
        if not _btc_compression_entry(bar):
            continue
        if extra_filter is not None and not extra_filter(bar):
            continue
        i = pos_of[current_time]
        if i + 1 >= len(bdf):
            continue
        nb = bdf.iloc[i + 1]
        entry_price, entry_time = nb["open"], nb["time"]
        atr = bar["atr_14"]
        if pd.isna(atr) or atr <= 0:
            continue
        # Stop = wider of compression-range low (prior 20-bar low) and 1.5*ATR.
        stop_price = min(bar["prior_low"], entry_price - bt.STOP_ATR_MULT * atr)
        r = entry_price - stop_price
        if r <= 0:
            continue
        sizing = bt.size_position(entry_price, stop_price, mtm)
        if not sizing:
            continue
        notional, margin, lev = sizing
        tr = bt.Trade(
            symbol="BTC-USD", side="long",
            signal_candle_time=current_time, entry_time=entry_time,
            entry_price=entry_price, stop_price=stop_price,
            target_1_price=entry_price + bt.PARTIAL_R * r,
            initial_stop_distance=abs(entry_price - stop_price),
            notional_usd=notional, margin_usd=margin, leverage=lev,
            atr_at_signal=float(atr), regime_state="risk_on", rs_return=float("nan"),
            setup_type="btc_compression_breakout",
        )
        open_trades["BTC-USD"] = tr
        trades_today += 1
        weekly_times.append(current_time)

    for sym, tr in open_trades.items():
        last_bar = btc_indexed.iloc[-1]
        bh = (last_bar.name - tr.entry_time).total_seconds() / bt.TIMEFRAME_SECONDS
        bt.finalize_trade(tr, {"exit_time": last_bar.name, "exit_price": last_bar["close"],
                               "reason": "backtest_end", "bars_held": int(bh),
                               "is_high_vol": bt.is_high_vol_bar(last_bar)})
        closed.append(tr)
        cum_pnl += tr.net_pnl_usd
    return closed, equity_curve


# --- metrics / reporting (self-contained) ---

def _pf(trades):
    gw = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
    gl = abs(sum(t.net_pnl_usd for t in trades if t.net_pnl_usd <= 0))
    return gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)


def _metrics(trades, equity_curve, autopsy_rows):
    n = len(trades)
    if n == 0:
        return {"trades": 0, "by_year": {}, "loss_n": 0}
    by_exit = sorted(trades, key=lambda t: (t.exit_time or t.entry_time))
    wins = [t for t in trades if t.net_pnl_usd > 0]
    net = sum(t.net_pnl_usd for t in trades)
    mcl = cur = 0
    for t in by_exit:
        cur = cur + 1 if t.net_pnl_usd <= 0 else 0
        mcl = max(mcl, cur)
    best = max(trades, key=lambda t: t.net_pnl_usd)
    ex_best = [t for t in trades if t is not best]
    top_pct = (best.net_pnl_usd / net * 100) if net > 0 else float("nan")
    t0 = min(t.entry_time for t in trades)
    t1 = max(t.entry_time for t in trades)
    split = t0 + (t1 - t0) * bt.OOS_SPLIT_FRAC
    is_t = [t for t in trades if t.entry_time <= split]
    oos_t = [t for t in trades if t.entry_time > split]
    years = {}
    for t in trades:
        years.setdefault(t.entry_time.year, []).append(t)
    by_year = {y: {"n": len(v), "net": sum(x.net_pnl_usd for x in v), "pf": _pf(v),
                   "avg_r": float(np.mean([x.r_multiple for x in v]))} for y, v in years.items()}
    mfe = [r["MFE_R"] for r in autopsy_rows]
    mae = [r["MAE_R"] for r in autopsy_rows]
    return {
        "trades": n, "win_rate": len(wins) / n * 100, "pf": _pf(trades),
        "avg_r": float(np.mean([t.r_multiple for t in trades])), "net": net,
        "max_dd": max((p["drawdown_pct"] for p in equity_curve), default=0.0),
        "max_consec_losses": mcl, "top_pct": top_pct,
        "is_pf": _pf(is_t), "oos_pf": _pf(oos_t), "is_n": len(is_t), "oos_n": len(oos_t),
        "exbest_net": sum(t.net_pnl_usd for t in ex_best),
        "exbest_avg_r": float(np.mean([t.r_multiple for t in ex_best])) if ex_best else float("nan"),
        "exbest_pf": _pf(ex_best), "by_year": by_year, "loss_n": len(autopsy_rows),
        "mfe_avg": float(np.mean(mfe)) if mfe else float("nan"),
        "mae_avg": float(np.mean(mae)) if mae else float("nan"),
        "mfe_05": sum(1 for x in mfe if x >= 0.5), "mfe_10": sum(1 for x in mfe if x >= 1.0),
    }


def _entry_quality(trades, btc_indexed):
    """Compression hit rates + breakout-quality stats over the entry signal bars."""
    n = len(trades)
    if n == 0:
        return {}
    catr = crange = cbb = 0
    bdist, bbody, bcloc, bvol = [], [], [], []
    for t in trades:
        if t.signal_candle_time not in btc_indexed.index:
            continue
        s = btc_indexed.loc[t.signal_candle_time]
        catr += int(bool(s["comp_atr_prev"]))
        crange += int(bool(s["comp_range_prev"]))
        cbb += int(bool(s["comp_bb_prev"]))
        rng = s["high"] - s["low"]
        atr = t.atr_at_signal
        if rng > 0:
            bcloc.append((s["close"] - s["low"]) / rng)
            bbody.append(abs(s["close"] - s["open"]) / rng)
        if not pd.isna(s["volume_avg_20"]) and s["volume_avg_20"] > 0:
            bvol.append(s["volume"] / s["volume_avg_20"])
        if atr and not pd.isna(s["prior_high"]):
            bdist.append((s["close"] - s["prior_high"]) / atr)
    return {
        "comp_atr_rate": catr / n, "comp_range_rate": crange / n, "comp_bb_rate": cbb / n,
        "brk_dist_atr_avg": float(np.mean(bdist)) if bdist else float("nan"),
        "brk_body_avg": float(np.mean(bbody)) if bbody else float("nan"),
        "brk_closeloc_avg": float(np.mean(bcloc)) if bcloc else float("nan"),
        "brk_volratio_avg": float(np.mean(bvol)) if bvol else float("nan"),
    }


def _f(x, pos=False, pct=False):
    if isinstance(x, str):
        return x
    if x != x:
        return "n/a"
    if x == float("inf"):
        return "inf"
    if pct:
        return f"{x:.1f}%"
    return f"{x:+.2f}" if pos else f"{x:.2f}"


def _gate_verdict(m):
    n = m.get("trades", 0)
    if n < 30:
        return "NOT deployable (<30 trades)"
    checks = [m["pf"] >= 1.3, m["avg_r"] > 0.2, m["exbest_net"] > 0, m["max_dd"] < 25,
              len(m["by_year"]) > 1 and sum(1 for y in m["by_year"].values() if y["net"] > 0) > 1]
    return "passes diagnostic gates (paper-trade candidate)" if all(checks) else "fails one or more gates"


def print_comparison(metrics_by_tf, quality_by_tf, ema200_share):
    labels = list(metrics_by_tf)
    print("\n" + "=" * 70)
    print("BTC compression breakout — 12H vs 1D (DIAGNOSTIC ONLY)")
    print("=" * 70)
    rows = [
        ("Trades", lambda lab: str(metrics_by_tf[lab].get("trades", 0))),
        ("Win rate", lambda lab: _f(metrics_by_tf[lab].get("win_rate", float("nan")), pct=True)),
        ("Profit factor", lambda lab: _f(metrics_by_tf[lab].get("pf", float("nan")))),
        ("Avg R", lambda lab: _f(metrics_by_tf[lab].get("avg_r", float("nan")), pos=True)),
        ("Net P&L $", lambda lab: _f(metrics_by_tf[lab].get("net", 0.0), pos=True)),
        ("Max drawdown %", lambda lab: _f(metrics_by_tf[lab].get("max_dd", 0.0))),
        ("Max consec losses", lambda lab: str(metrics_by_tf[lab].get("max_consec_losses", 0))),
        ("Top trade % of net", lambda lab: _f(metrics_by_tf[lab].get("top_pct", float("nan")), pct=True)),
        ("Ex-best net $", lambda lab: _f(metrics_by_tf[lab].get("exbest_net", 0.0), pos=True)),
        ("Ex-best avg R", lambda lab: _f(metrics_by_tf[lab].get("exbest_avg_r", float("nan")), pos=True)),
        ("Ex-best PF", lambda lab: _f(metrics_by_tf[lab].get("exbest_pf", float("nan")))),
        ("IS PF (n)", lambda lab: f"{_f(metrics_by_tf[lab].get('is_pf', float('nan')))} ({metrics_by_tf[lab].get('is_n', 0)})"),
        ("OOS PF (n)", lambda lab: f"{_f(metrics_by_tf[lab].get('oos_pf', float('nan')))} ({metrics_by_tf[lab].get('oos_n', 0)})"),
        ("Losers MFE_R avg", lambda lab: _f(metrics_by_tf[lab].get("mfe_avg", float("nan")))),
        ("Losers MAE_R avg", lambda lab: _f(metrics_by_tf[lab].get("mae_avg", float("nan")))),
        ("Losers >=+0.5R", lambda lab: f"{metrics_by_tf[lab].get('mfe_05', 0)}/{metrics_by_tf[lab].get('loss_n', 0)}"),
        ("Losers >=+1.0R", lambda lab: f"{metrics_by_tf[lab].get('mfe_10', 0)}/{metrics_by_tf[lab].get('loss_n', 0)}"),
        ("ATR-contract hit", lambda lab: _f(quality_by_tf[lab].get("comp_atr_rate", float("nan")) * 100, pct=True) if quality_by_tf[lab] else "n/a"),
        ("Range-compress hit", lambda lab: _f(quality_by_tf[lab].get("comp_range_rate", float("nan")) * 100, pct=True) if quality_by_tf[lab] else "n/a"),
        ("BB-width hit", lambda lab: _f(quality_by_tf[lab].get("comp_bb_rate", float("nan")) * 100, pct=True) if quality_by_tf[lab] else "n/a"),
        ("Brk dist (ATR) avg", lambda lab: _f(quality_by_tf[lab].get("brk_dist_atr_avg", float("nan"))) if quality_by_tf[lab] else "n/a"),
        ("Brk body avg", lambda lab: _f(quality_by_tf[lab].get("brk_body_avg", float("nan"))) if quality_by_tf[lab] else "n/a"),
        ("Brk close-loc avg", lambda lab: _f(quality_by_tf[lab].get("brk_closeloc_avg", float("nan"))) if quality_by_tf[lab] else "n/a"),
        ("Brk vol-ratio avg", lambda lab: _f(quality_by_tf[lab].get("brk_volratio_avg", float("nan"))) if quality_by_tf[lab] else "n/a"),
        ("close>EMA200 (report)", lambda lab: ema200_share.get(lab, "n/a")),
    ]
    print(f"{'Metric':<22}" + "".join(f"{lab:>16}" for lab in labels))
    print("-" * 70)
    for label, fn in rows:
        print(f"{label:<22}" + "".join(f"{fn(lab):>16}" for lab in labels))
    print("\nPerformance by year (net $ | PF | avgR | n):")
    yrs = sorted(set().union(*[set(m.get("by_year", {})) for m in metrics_by_tf.values()]))
    for y in yrs:
        line = f"  {y}: "
        for lab in labels:
            a = metrics_by_tf[lab].get("by_year", {}).get(y)
            cell = f"{a['net']:+.2f}|{_f(a['pf'])}|{a['avg_r']:+.2f}|{a['n']}" if a else "-"
            line += f"  {lab}={cell}"
        print(line)
    print()
    for lab in labels:
        print(f"  {lab:<4}: {metrics_by_tf[lab].get('trades', 0)} trades -> {_gate_verdict(metrics_by_tf[lab])}")
    print("\nNOTE: diagnostic only — not for deployment, not paper/live, no parameter tuning.")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    metrics_by_tf, quality_by_tf, ema200_share = {}, {}, {}
    for label, entry_s, gran, agg, ov in TF_CONFIGS:
        saved = {k: getattr(bt, k) for k in ov}
        for k, v in ov.items():
            setattr(bt, k, v)
        try:
            df = _build_btc_df(entry_s, gran, agg, args.days)
            btc_indexed = df.set_index("time")
            trades, eq = _run_btc_compression(btc_indexed)
        finally:
            for k, v in saved.items():
                setattr(bt, k, v)
        print(f"\n=== BTC {label} compression breakout: {len(trades)} trades ===")
        autopsy = bt.build_loss_autopsy(trades, {"BTC-USD": df})
        bt.export_trades_csv(trades, os.path.join(OUT_DIR, f"trades_{label}.csv"))
        bt.export_equity_csv(eq, os.path.join(OUT_DIR, f"equity_{label}.csv"))
        bt.export_loss_autopsy_csv(autopsy, os.path.join(OUT_DIR, f"loss_autopsy_{label}.csv"))
        bt.export_regime_behavior_csv(bt.build_regime_behavior_report(trades, {"BTC-USD": df}),
                                      os.path.join(OUT_DIR, f"regime_behavior_{label}.csv"))
        metrics_by_tf[label] = _metrics(trades, eq, autopsy)
        quality_by_tf[label] = _entry_quality(trades, btc_indexed)
        above = sum(1 for t in trades if t.signal_candle_time in btc_indexed.index
                    and not pd.isna(btc_indexed.loc[t.signal_candle_time]["ema_200"])
                    and btc_indexed.loc[t.signal_candle_time]["close"] > btc_indexed.loc[t.signal_candle_time]["ema_200"])
        ema200_share[label] = f"{above}/{len(trades)}" if trades else "0/0"

    print_comparison(metrics_by_tf, quality_by_tf, ema200_share)


if __name__ == "__main__":
    main()
