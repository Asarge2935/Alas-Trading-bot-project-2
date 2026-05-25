"""
ETH breakout HIGHER-TIMEFRAME DIAGNOSTIC — does NOT modify the deployed 6H strategy.

Purpose: the 6H ETH-long breakout has the best evidence; 4H added noise and the
pullback variant failed. Test whether LARGER timeframes (12H, 1D) improve signal
quality vs the 6H baseline. Breakout only, ETH-long-only, strict regime.

Reuses backtest.py's engine unchanged. For each timeframe it (a) builds candles
and (b) overrides only the timeframe-dependent globals for that run, then
restores them. backtest.py's canonical 6H constants are never edited.

Data / aggregation:
  - 6H  : native Coinbase granularity 21600.
  - 12H : NOT native -> AGGREGATED from 6H (two 6H bars):
            open=first, high=max, low=min, close=last, volume=sum.
          Timestamp alignment: a 12H bar is labeled by its START, aligned to
          00:00 / 12:00 UTC (epoch-aligned floor('12h')); bar T aggregates the
          6H candles with start in [T, T+12h). Matches the time=start convention
          so backtest.drop_incomplete_candles works unchanged.
  - 1D  : native Coinbase granularity 86400.

Indicator handling (explicit; nothing silently changed):
  - BAR-BASED, kept IDENTICAL across timeframes: EMA50, ATR14, 20-bar breakout
    lookback, strong-close fraction, volume average. (A "20-bar breakout" stays
    a 20-bar breakout regardless of timeframe; changing it would be loosening,
    not a mechanical timeframe adjustment.)
  - TIME-EQUIVALENT, mechanically rescaled to preserve clock duration:
      TIME_STOP_BARS   : 12 bars @6H (72h) -> 6 @12H -> 3 @1D
      ATR_REGIME_PERIOD: 120 bars @6H (30d) -> 60 @12H -> 30 @1D
      BARS_PER_DAY     : 4 -> 2 -> 1
      TIMEFRAME_SECONDS: 21600 -> 43200 -> 86400

Costs / risk / exits are reused unchanged (same fee, slippage, funding, 1%
risk, max-1-open). Regime + RS are daily-derived (resampled), so identical
across timeframes.

Run:
    python3 eth_htf_diagnostic.py [--days N]

Outputs (backtest_output_eth_htf/):
    trades_{6h,12h,1d}.csv, equity_{6h,12h,1d}.csv,
    loss_autopsy_{6h,12h,1d}.csv, regime_behavior_{6h,12h,1d}.csv
    + console 6H/12H/1D comparison.
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt

OUT_DIR = "backtest_output_eth_htf"
ETH_ARGS = dict(trade_assets=["ETH-USD"], long_only=True, strict_regime=True, setup="breakout")

# Per-timeframe config: (label, entry_seconds, fetch_granularity, agg_freq_or_None, overrides)
TF_CONFIGS = [
    ("6h", 21600, 21600, None,
     {"TIMEFRAME_SECONDS": 21600, "BARS_PER_DAY": 4, "TIME_STOP_BARS": 12, "ATR_REGIME_PERIOD": 120}),
    ("12h", 43200, 21600, "12h",
     {"TIMEFRAME_SECONDS": 43200, "BARS_PER_DAY": 2, "TIME_STOP_BARS": 6, "ATR_REGIME_PERIOD": 60}),
    ("1d", 86400, 86400, None,
     {"TIMEFRAME_SECONDS": 86400, "BARS_PER_DAY": 1, "TIME_STOP_BARS": 3, "ATR_REGIME_PERIOD": 30}),
]


def _aggregate(df, freq):
    """Aggregate candles to a coarser pandas freq (e.g. '12h'). open=first,
    high=max, low=min, close=last, volume=sum; bar labeled by its START."""
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


def _run_tf(entry_seconds, fetch_gran, agg_freq, overrides, days):
    """Run one timeframe with its overrides applied, then restored."""
    saved = {k: getattr(bt, k) for k in overrides}
    for k, v in overrides.items():
        setattr(bt, k, v)
    try:
        data = {}
        for sym in bt.ASSETS:
            raw = bt.load_candles(sym, fetch_gran, days)
            if agg_freq:
                raw = _aggregate(raw, agg_freq)
            data[sym] = bt.add_indicators(bt.drop_incomplete_candles(raw, entry_seconds))
        trades, eq = bt.run_backtest(data, **ETH_ARGS)
        return trades, eq, data
    finally:
        for k, v in saved.items():
            setattr(bt, k, v)


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
                   "avg_r": float(np.mean([x.r_multiple for x in v]))}
               for y, v in years.items()}
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
        "mfe_05": sum(1 for x in mfe if x >= 0.5),
        "mfe_10": sum(1 for x in mfe if x >= 1.0),
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


def print_comparison(metrics_by_tf):
    labels = list(metrics_by_tf)
    print("\n" + "=" * 74)
    print("ETH-long-only strict — BREAKOUT across timeframes (DIAGNOSTIC ONLY)")
    print("=" * 74)
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
    ]
    print(f"{'Metric':<22}" + "".join(f"{lab:>16}" for lab in labels))
    print("-" * 74)
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
        n = metrics_by_tf[lab].get("trades", 0)
        flag = "NOT deployable (<30)" if n < 30 else "clears 30-trade gate (validate OOS/ex-best)"
        print(f"  {lab:<4}: {n} trades -> {flag}")
    print("\nNOTE: diagnostic only — not for deployment, not paper/live, no parameter tuning.")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    metrics_by_tf = {}
    for label, entry_s, gran, agg, ov in TF_CONFIGS:
        print(f"\n=== {label} breakout (ETH-long-only, strict) ===")
        trades, eq, data = _run_tf(entry_s, gran, agg, ov, args.days)
        print(f"{label} trades: {len(trades)}")
        autopsy = bt.build_loss_autopsy(trades, data)
        bt.export_trades_csv(trades, os.path.join(OUT_DIR, f"trades_{label}.csv"))
        bt.export_equity_csv(eq, os.path.join(OUT_DIR, f"equity_{label}.csv"))
        bt.export_loss_autopsy_csv(autopsy, os.path.join(OUT_DIR, f"loss_autopsy_{label}.csv"))
        bt.export_regime_behavior_csv(bt.build_regime_behavior_report(trades, data),
                                      os.path.join(OUT_DIR, f"regime_behavior_{label}.csv"))
        metrics_by_tf[label] = _metrics(trades, eq, autopsy)

    print_comparison(metrics_by_tf)


if __name__ == "__main__":
    main()
