"""
4H ETH-long-only strict-regime DIAGNOSTIC — does NOT touch the deployed 6H strategy.

Purpose: test whether the promising 6H ETH-long-only behavior survives at 4H,
which yields more sample. This is a separate diagnostic harness that reuses
backtest.py's engine unchanged; it only (a) builds 4H candles and (b) overrides
the four timeframe-dependent globals for the duration of the 4H run, then
restores them. The canonical 6H constants in backtest.py are never edited.

Coinbase Exchange granularities are {60,300,900,3600,21600,86400}; 4H (14400)
is NOT native, so 4H bars are AGGREGATED from 1H (3600) candles:
    open   = first 1H open in the bucket
    high   = max 1H high
    low    = min 1H low
    close  = last 1H close
    volume = sum 1H volume
Timestamp alignment: a 4H bar is labeled by its START and aligned to
00/04/08/12/16/20 UTC (pandas floor('4h'), which is epoch-aligned). Bar T
aggregates 1H candles with start in [T, T+4h). This matches Coinbase's
time=start convention, so backtest.drop_incomplete_candles (close = start +
timeframe) works unchanged.

Mechanical 6H->4H conversions (time-preserving, ratio 1.5x; nothing loosened):
    TIMEFRAME_SECONDS 21600 -> 14400
    BARS_PER_DAY          4 -> 6
    TIME_STOP_BARS       12 -> 18   (still ~3 days)
    ATR_REGIME_PERIOD   120 -> 180  (still 30 days)
EMA50 / ATR14 / 20-bar breakout / strong-close / costs / 1% risk / max-1-open
are IDENTICAL to the deployed strategy. The run is ETH-USD long-only,
strict-regime (BTC strict risk_on), ETH must be RS rank 1, ETH self-uptrend.

Run:
    python3 eth_4h_diagnostic.py            # default ~1460 days (1H fetch is large first run)
    python3 eth_4h_diagnostic.py --days 730

Outputs (4H artifacts, kept separate from the 6H run):
    backtest_output_4h/{trades,equity_curve,loss_autopsy,regime_behavior_report}.csv
    + console 6H-vs-4H comparison + robustness report on the 4H trades.
"""

import argparse
import os
import subprocess
import sys
import numpy as np
import pandas as pd

import backtest as bt

OUT_4H = "backtest_output_4h"
TF_4H_SECONDS = 14400
GRAN_1H = 3600

# 4H overrides (mechanical; see module docstring).
OVERRIDES_4H = {
    "TIMEFRAME_SECONDS": TF_4H_SECONDS,
    "BARS_PER_DAY": 86400 // TF_4H_SECONDS,   # 6
    "TIME_STOP_BARS": 18,                      # 12 * 1.5  (~3 days)
    "ATR_REGIME_PERIOD": 180,                  # 120 * 1.5 (30 days)
}


def aggregate_1h_to_4h(df_1h):
    """Aggregate 1H OHLCV candles into 4H bars (see module docstring for rules
    and timestamp alignment). Returns columns [time, low, high, open, close,
    volume], time = bar START aligned to 00/04/08/12/16/20 UTC."""
    if df_1h.empty:
        return df_1h.copy()
    df = df_1h.sort_values("time").copy()
    bucket = df["time"].dt.floor("4h")
    bucket.name = "time"
    agg = (df.groupby(bucket)
             .agg(open=("open", "first"), high=("high", "max"),
                  low=("low", "min"), close=("close", "last"),
                  volume=("volume", "sum"))
             .reset_index())
    return agg[["time", "low", "high", "open", "close", "volume"]]


def _run_6h(days):
    data = {}
    for sym in bt.ASSETS:
        df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)   # 21600 default
        data[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
    trades, eq = bt.run_backtest(data, trade_assets=["ETH-USD"],
                                 long_only=True, strict_regime=True)
    return trades, eq, data


def _run_4h(days):
    saved = {k: getattr(bt, k) for k in OVERRIDES_4H}
    for k, v in OVERRIDES_4H.items():
        setattr(bt, k, v)
    try:
        data = {}
        for sym in bt.ASSETS:
            raw_1h = bt.load_candles(sym, GRAN_1H, days)
            df_4h = aggregate_1h_to_4h(raw_1h)
            data[sym] = bt.add_indicators(bt.drop_incomplete_candles(df_4h, TF_4H_SECONDS))
        trades, eq = bt.run_backtest(data, trade_assets=["ETH-USD"],
                                     long_only=True, strict_regime=True)
        return trades, eq, data
    finally:
        for k, v in saved.items():
            setattr(bt, k, v)


def _pf(trades):
    gw = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
    gl = abs(sum(t.net_pnl_usd for t in trades if t.net_pnl_usd <= 0))
    return gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)


def _acceptance_metrics(trades, equity_curve, autopsy_rows):
    n = len(trades)
    if n == 0:
        return {"trades": 0, "by_year": {}, "loss_n": 0}
    by_exit = sorted(trades, key=lambda t: (t.exit_time or t.entry_time))
    wins = [t for t in trades if t.net_pnl_usd > 0]
    net = sum(t.net_pnl_usd for t in trades)

    mcl = cur = 0
    for t in by_exit:
        if t.net_pnl_usd <= 0:
            cur += 1
            mcl = max(mcl, cur)
        else:
            cur = 0

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
    by_year = {y: {"n": len(v), "net": sum(x.net_pnl_usd for x in v),
                   "pf": _pf(v), "avg_r": float(np.mean([x.r_multiple for x in v]))}
               for y, v in years.items()}

    mfe = [r["MFE_R"] for r in autopsy_rows]
    mae = [r["MAE_R"] for r in autopsy_rows]
    nl = len(autopsy_rows)
    return {
        "trades": n,
        "win_rate": len(wins) / n * 100,
        "pf": _pf(trades),
        "avg_r": float(np.mean([t.r_multiple for t in trades])),
        "net": net,
        "max_dd": max((p["drawdown_pct"] for p in equity_curve), default=0.0),
        "max_consec_losses": mcl,
        "top_pct": top_pct,
        "is_pf": _pf(is_t), "oos_pf": _pf(oos_t), "is_n": len(is_t), "oos_n": len(oos_t),
        "exbest_net": sum(t.net_pnl_usd for t in ex_best),
        "exbest_avg_r": float(np.mean([t.r_multiple for t in ex_best])) if ex_best else float("nan"),
        "exbest_pf": _pf(ex_best),
        "by_year": by_year,
        "loss_n": nl,
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


def print_comparison(m6, m4):
    print("\n" + "=" * 70)
    print("6H vs 4H — ETH-USD long-only, strict-regime (DIAGNOSTIC ONLY)")
    print("=" * 70)
    rows = [
        ("Trades", str(m6.get("trades", 0)), str(m4.get("trades", 0))),
        ("Win rate", _f(m6.get("win_rate", float("nan")), pct=True), _f(m4.get("win_rate", float("nan")), pct=True)),
        ("Profit factor", _f(m6.get("pf", float("nan"))), _f(m4.get("pf", float("nan")))),
        ("Avg R", _f(m6.get("avg_r", float("nan")), pos=True), _f(m4.get("avg_r", float("nan")), pos=True)),
        ("Net P&L $", _f(m6.get("net", 0.0), pos=True), _f(m4.get("net", 0.0), pos=True)),
        ("Max drawdown %", _f(m6.get("max_dd", 0.0)), _f(m4.get("max_dd", 0.0))),
        ("Max consec losses", str(m6.get("max_consec_losses", 0)), str(m4.get("max_consec_losses", 0))),
        ("Top trade % of net", _f(m6.get("top_pct", float("nan")), pct=True), _f(m4.get("top_pct", float("nan")), pct=True)),
        ("IS PF (n)", f"{_f(m6.get('is_pf', float('nan')))} ({m6.get('is_n',0)})",
                       f"{_f(m4.get('is_pf', float('nan')))} ({m4.get('is_n',0)})"),
        ("OOS PF (n)", f"{_f(m6.get('oos_pf', float('nan')))} ({m6.get('oos_n',0)})",
                        f"{_f(m4.get('oos_pf', float('nan')))} ({m4.get('oos_n',0)})"),
        ("Ex-best net $", _f(m6.get("exbest_net", 0.0), pos=True), _f(m4.get("exbest_net", 0.0), pos=True)),
        ("Ex-best avg R", _f(m6.get("exbest_avg_r", float("nan")), pos=True), _f(m4.get("exbest_avg_r", float("nan")), pos=True)),
        ("Ex-best PF", _f(m6.get("exbest_pf", float("nan"))), _f(m4.get("exbest_pf", float("nan")))),
        ("Losers MFE_R avg", _f(m6.get("mfe_avg", float("nan"))), _f(m4.get("mfe_avg", float("nan")))),
        ("Losers MAE_R avg", _f(m6.get("mae_avg", float("nan"))), _f(m4.get("mae_avg", float("nan")))),
        ("Losers >=+0.5R MFE", f"{m6.get('mfe_05',0)}/{m6.get('loss_n',0)}", f"{m4.get('mfe_05',0)}/{m4.get('loss_n',0)}"),
        ("Losers >=+1.0R MFE", f"{m6.get('mfe_10',0)}/{m6.get('loss_n',0)}", f"{m4.get('mfe_10',0)}/{m4.get('loss_n',0)}"),
    ]
    print(f"{'Metric':<22}{'6H':>14}{'4H':>14}")
    print("-" * 70)
    for label, a, b in rows:
        print(f"{label:<22}{a:>14}{b:>14}")

    print("\nPerformance by year (net $ | PF | avgR | n):")
    yrs = sorted(set(m6.get("by_year", {})) | set(m4.get("by_year", {})))
    print(f"  {'year':<6}{'6H':>28}{'4H':>28}")
    for y in yrs:
        a = m6.get("by_year", {}).get(y)
        b = m4.get("by_year", {}).get(y)
        af = f"{a['net']:+.2f} | {_f(a['pf'])} | {a['avg_r']:+.2f} | {a['n']}" if a else "-"
        bf = f"{b['net']:+.2f} | {_f(b['pf'])} | {b['avg_r']:+.2f} | {b['n']}" if b else "-"
        print(f"  {y:<6}{af:>28}{bf:>28}")

    n4 = m4.get("trades", 0)
    print("\nNOTE: this is a timeframe DIAGNOSTIC, not a strategy change. "
          + ("4H has < 30 trades — still below the §9 sample gate; "
             "treat as indicative." if n4 < 30 else
             "4H clears 30 trades — examine OOS PF and ex-best stability before any claim."))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()

    print("=== 6H baseline (ETH-long-only, strict) ===")
    t6, eq6, data6 = _run_6h(args.days)
    print(f"6H trades: {len(t6)}")

    print("\n=== 4H diagnostic (aggregated from 1H; ETH-long-only, strict) ===")
    t4, eq4, data4 = _run_4h(args.days)
    print(f"4H trades: {len(t4)}")

    # 4H outputs (kept separate from the 6H backtest_output/).
    os.makedirs(OUT_4H, exist_ok=True)
    bt.export_trades_csv(t4, os.path.join(OUT_4H, "trades.csv"))
    bt.export_equity_csv(eq4, os.path.join(OUT_4H, "equity_curve.csv"))
    autopsy4 = bt.build_loss_autopsy(t4, data4)
    bt.export_loss_autopsy_csv(autopsy4, os.path.join(OUT_4H, "loss_autopsy.csv"))
    rbr4 = bt.build_regime_behavior_report(t4, data4)
    bt.export_regime_behavior_csv(rbr4, os.path.join(OUT_4H, "regime_behavior_report.csv"))

    autopsy6 = bt.build_loss_autopsy(t6, data6)
    print_comparison(_acceptance_metrics(t6, eq6, autopsy6),
                     _acceptance_metrics(t4, eq4, autopsy4))

    print("\n=== 4H robustness report ===")
    if t4:
        subprocess.run([sys.executable, "robustness_report.py", "--dir", OUT_4H], check=False)
    else:
        print("No 4H trades — skipping robustness report. (On real data the 4H "
              "strict ETH-long set should be non-empty; 0 here means no signal "
              "fired in the window.)")


if __name__ == "__main__":
    main()
