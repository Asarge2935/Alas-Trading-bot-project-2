"""
BTC-specific DIAGNOSTIC — BTC trend-continuation pullback. Does NOT modify the
ETH breakout strategy or backtest.py's engine; it reuses backtest.py's
low-level helpers (sizing, exits, cost model, autopsy/report exporters) in a
standalone BTC-only loop.

Why standalone: the canonical run_backtest gates entries on the BTC 3-state
regime + cross-asset relative-strength selection. The BTC model deliberately
uses NEITHER — it trades BTC on its OWN trend (self-regime), with no RS rank.
Bolting that onto run_backtest would risk the ETH path, so this is separate.

Diagnostic only. No live/paper/adapter/order changes.

Universe : BTC-USD only.   Direction: LONG only (shorts NOT tested yet).
Timeframes: 12H and 1D. 1D native (Coinbase 86400). 12H not native ->
  AGGREGATED from 6H (21600): open=first, high=max, low=min, close=last,
  volume=sum. A 12H bar is labeled by its START, aligned to 00:00/12:00 UTC
  (epoch-aligned floor('12h')); bar T aggregates 6H candles with start in
  [T, T+12h). Matches the time=start convention so drop_incomplete_candles is
  unchanged.

BTC self-regime (entry-timeframe): close > EMA50 AND EMA50 slope up.
  RS rank is NOT required. close > EMA200 is computed REPORT-ONLY.

Setup — BTC trend-continuation pullback (long), no look-ahead:
  1. BTC self-uptrend (above).
  2. Pullback toward the EMA20/EMA50 zone: this or the prior bar dipped to
     <= EMA20*1.01 OR <= EMA50*1.01.
  3. Structure intact: close > EMA50 AND close > recent swing low
     (min low of the prior 10 bars).
  4. Confirmation: close reclaimed above EMA20 OR close > prior bar high.
  5. Enter next bar open.

Stop (single documented default): the WIDER (further) of (a) the recent swing
  low and (b) entry - 1.5*ATR  ->  stop = min(swing_low, entry - 1.5*ATR).
Exits/costs/risk: reuse backtest.py unchanged — partial +1.5R, breakeven,
  2.5*ATR trail, time stop, same fees/slippage/funding, 1% risk, max-1-open.
Time stop preserves ~72h clock intent: 12 bars @6H -> 6 @12H -> 3 @1D.
ATR_REGIME_PERIOD (30-day vol baseline) rescaled 120 @6H -> 60 @12H -> 30 @1D.
Bar-based indicators (EMA20/50/200, ATR14, swing-10) kept identical.

Run:
    python3 btc_diagnostic.py [--days N]

Outputs (backtest_output_btc_diagnostic/):
    trades_{12h,1d}.csv, equity_{12h,1d}.csv,
    loss_autopsy_{12h,1d}.csv, regime_behavior_{12h,1d}.csv
    + console 12H/1D comparison report.
"""

import argparse
import os
from datetime import timedelta
import numpy as np
import pandas as pd

import backtest as bt

OUT_DIR = "backtest_output_btc_diagnostic"
PB_TOL = 1.01          # within 1% of EMA20/EMA50 counts as a pullback touch
SWING_BARS = 10        # recent swing low = min low of the prior N bars
BTC_EMA_LONG = 200     # report-only

# (label, entry_seconds, fetch_granularity, agg_freq_or_None, overrides)
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


def _build_btc_df(entry_seconds, fetch_gran, agg_freq, days):
    raw = bt.load_candles("BTC-USD", fetch_gran, days)
    if agg_freq:
        raw = _aggregate(raw, agg_freq)
    df = bt.add_indicators(bt.drop_incomplete_candles(raw, entry_seconds))
    # Extra BTC-model inputs (additive; do not touch backtest.add_indicators).
    df["ema_200"] = df["close"].ewm(span=BTC_EMA_LONG, adjust=False).mean()
    df["prev_high"] = df["high"].shift(1)
    df["swing_low"] = df["low"].rolling(SWING_BARS).min().shift(1)
    return df


def _btc_pullback_entry(row):
    """BTC trend-continuation pullback (long), evaluated on the confirmation
    bar with no look-ahead. See module docstring."""
    needed = ["ema_20", "ema_50", "ema_50_slope", "prev_high", "swing_low", "atr_14"]
    for c in needed:
        if pd.isna(row[c]):
            return False
    rng = row["high"] - row["low"]
    if rng <= 0:
        return False
    self_uptrend = row["close"] > row["ema_50"] and row["ema_50_slope"] > 0
    dip = min(row["low"], row["prev_low"]) if not pd.isna(row["prev_low"]) else row["low"]
    pulled_back = dip <= row["ema_20"] * PB_TOL or dip <= row["ema_50"] * PB_TOL
    structure_ok = row["close"] > row["ema_50"] and row["close"] > row["swing_low"]
    confirm = row["close"] > row["ema_20"] or row["close"] > row["prev_high"]
    return self_uptrend and pulled_back and structure_ok and confirm


def _run_btc_pullback(btc_indexed):
    """Standalone BTC-only long backtest. Mirrors run_backtest's MTM / loss-halt
    / drawdown / single-position management, reusing backtest.py's helpers."""
    bdf = btc_indexed.reset_index()
    pos_of = {t: i for i, t in enumerate(bdf["time"])}

    open_trades = {}
    closed = []
    equity_curve = []
    cum_pnl = 0.0
    peak = bt.ACCOUNT_SIZE_USD
    trades_today = 0
    last_day = last_week = None
    day_start = week_start = bt.ACCOUNT_SIZE_USD
    weekly_times = []
    dd_pause_until = None

    for current_time in btc_indexed.index:
        bar = btc_indexed.loc[current_time]
        # 1. exits
        for sym in list(open_trades):
            tr = open_trades[sym]
            ev = bt.check_exit(tr, bar, current_time)
            if ev:
                bt.finalize_trade(tr, ev)
                closed.append(tr)
                cum_pnl += tr.net_pnl_usd
                del open_trades[sym]
        # 2. MTM
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
        # 3. entry gates (same as run_backtest)
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
        # 4. BTC pullback entry
        if not _btc_pullback_entry(bar):
            continue
        i = pos_of[current_time]
        if i + 1 >= len(bdf):
            continue
        nb = bdf.iloc[i + 1]
        entry_price, entry_time = nb["open"], nb["time"]
        atr = bar["atr_14"]
        if pd.isna(atr) or atr <= 0:
            continue
        # Stop = wider of recent swing low and 1.5*ATR.
        stop_price = min(bar["swing_low"], entry_price - bt.STOP_ATR_MULT * atr)
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
            atr_at_signal=float(atr),
            regime_state="risk_on",          # BTC self-uptrend == long-aligned regime
            rs_return=float("nan"),           # no RS in the BTC model
            setup_type="btc_trend_pullback",
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
        "exbest_pf": _pf(ex_best), "by_year": by_year,
        "loss_n": len(autopsy_rows),
        "mfe_avg": float(np.mean(mfe)) if mfe else float("nan"),
        "mae_avg": float(np.mean(mae)) if mae else float("nan"),
        "mfe_05": sum(1 for x in mfe if x >= 0.5), "mfe_10": sum(1 for x in mfe if x >= 1.0),
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
    checks = [m["pf"] >= 1.3, m["avg_r"] > 0.2, m["exbest_net"] > 0,
              m["max_dd"] < 25, len(m["by_year"]) > 1 and
              sum(1 for y in m["by_year"].values() if y["net"] > 0) > 1]
    return "passes diagnostic gates (paper-trade candidate)" if all(checks) else "fails one or more gates"


def print_comparison(metrics_by_tf, ema200_share):
    labels = list(metrics_by_tf)
    print("\n" + "=" * 70)
    print("BTC trend-continuation pullback — 12H vs 1D (DIAGNOSTIC ONLY)")
    print("=" * 70)
    rows = [
        ("Trades", lambda m: str(m.get("trades", 0))),
        ("Win rate", lambda m: _f(m.get("win_rate", float("nan")), pct=True)),
        ("Profit factor", lambda m: _f(m.get("pf", float("nan")))),
        ("Avg R", lambda m: _f(m.get("avg_r", float("nan")), pos=True)),
        ("Net P&L $", lambda m: _f(m.get("net", 0.0), pos=True)),
        ("Max drawdown %", lambda m: _f(m.get("max_dd", 0.0))),
        ("Max consec losses", lambda m: str(m.get("max_consec_losses", 0))),
        ("Top trade % of net", lambda m: _f(m.get("top_pct", float("nan")), pct=True)),
        ("Ex-best net $", lambda m: _f(m.get("exbest_net", 0.0), pos=True)),
        ("Ex-best avg R", lambda m: _f(m.get("exbest_avg_r", float("nan")), pos=True)),
        ("Ex-best PF", lambda m: _f(m.get("exbest_pf", float("nan")))),
        ("IS PF (n)", lambda m: f"{_f(m.get('is_pf', float('nan')))} ({m.get('is_n', 0)})"),
        ("OOS PF (n)", lambda m: f"{_f(m.get('oos_pf', float('nan')))} ({m.get('oos_n', 0)})"),
        ("Losers MFE_R avg", lambda m: _f(m.get("mfe_avg", float("nan")))),
        ("Losers MAE_R avg", lambda m: _f(m.get("mae_avg", float("nan")))),
        ("Losers >=+0.5R", lambda m: f"{m.get('mfe_05', 0)}/{m.get('loss_n', 0)}"),
        ("Losers >=+1.0R", lambda m: f"{m.get('mfe_10', 0)}/{m.get('loss_n', 0)}"),
        ("close>EMA200 (report)", lambda m: ema200_share.get(id(m), "n/a")),
    ]
    print(f"{'Metric':<22}" + "".join(f"{lab:>16}" for lab in labels))
    print("-" * 70)
    for label, fn in rows:
        print(f"{label:<22}" + "".join(f"{fn(metrics_by_tf[lab]):>16}" for lab in labels))
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
    metrics_by_tf = {}
    ema200_share = {}
    for label, entry_s, gran, agg, ov in TF_CONFIGS:
        saved = {k: getattr(bt, k) for k in ov}
        for k, v in ov.items():
            setattr(bt, k, v)
        try:
            df = _build_btc_df(entry_s, gran, agg, args.days)
            btc_indexed = df.set_index("time")
            trades, eq = _run_btc_pullback(btc_indexed)
        finally:
            for k, v in saved.items():
                setattr(bt, k, v)
        print(f"\n=== BTC {label} trend-continuation pullback: {len(trades)} trades ===")
        autopsy = bt.build_loss_autopsy(trades, {"BTC-USD": df})
        bt.export_trades_csv(trades, os.path.join(OUT_DIR, f"trades_{label}.csv"))
        bt.export_equity_csv(eq, os.path.join(OUT_DIR, f"equity_{label}.csv"))
        bt.export_loss_autopsy_csv(autopsy, os.path.join(OUT_DIR, f"loss_autopsy_{label}.csv"))
        bt.export_regime_behavior_csv(bt.build_regime_behavior_report(trades, {"BTC-USD": df}),
                                      os.path.join(OUT_DIR, f"regime_behavior_{label}.csv"))
        m = _metrics(trades, eq, autopsy)
        metrics_by_tf[label] = m
        # report-only: share of entries with close > EMA200 at signal
        above = 0
        for t in trades:
            if t.signal_candle_time in btc_indexed.index:
                s = btc_indexed.loc[t.signal_candle_time]
                if not pd.isna(s["ema_200"]) and s["close"] > s["ema_200"]:
                    above += 1
        ema200_share[id(m)] = f"{above}/{len(trades)}" if trades else "0/0"

    print_comparison(metrics_by_tf, ema200_share)


if __name__ == "__main__":
    main()
