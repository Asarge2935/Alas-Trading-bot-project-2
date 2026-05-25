"""
BTC COMPRESSION NEXT DIAGNOSTIC — compares the BTC 12H compression breakout
baseline against structured hypothesis variants. Diagnostic only. No ETH/live/
paper changes. No parameter optimization. One early-failure exit rule only.

Variants:
  1. baseline                         — existing compression gate (comp_count >= 2)
  2. full_compression_only            — require comp_count == 3
  3. baseline_early_failure_exit      — baseline + the early-failure exit
  4. full_compression_early_failure_exit — comp_count == 3 + the early-failure exit

EARLY-FAILURE EXIT RULE (single, deterministic, documented):
  After entry, if a bar CLOSES back INSIDE the prior compression range — i.e.
  the close falls back below the 20-bar high the breakout cleared
  (prior_high at the signal bar) — exit at that bar's close
  (reason "early_failure_reenter_range"). The stop/partial/trail/time-stop
  logic keeps priority; this only fires when none of those did. Nothing else
  changes: breakout trigger, volume threshold, stops, exits, fees, slippage,
  funding, and risk model are reused unchanged.

Run:
    python3 btc_compression_next_diagnostic.py [--days N]

Outputs (backtest_output_btc_compression_next/):
    trades_{variant}.csv + equity/loss_autopsy/regime_behavior
    + console comparison with robustness verdicts. ALL DIAGNOSTIC ONLY.
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt
import btc_compression_diagnostic as bc

OUT_DIR = "backtest_output_btc_compression_next"
TF_OVERRIDES = dict(bc.TF_CONFIGS[0][4])   # 12H
ENTRY_SECONDS, FETCH_GRAN, AGG = 43200, 21600, "12h"
WINNER_GATE = 12
MONTH_DOMINANCE = 0.45   # one month > ~45% of net -> MONTH-DEPENDENT


def _make_early_exit(btc_indexed):
    """Exit if a bar closes back inside the prior compression range (below the
    20-bar high the breakout cleared at the signal bar)."""
    def f(trade, bar, current_time):
        if trade.signal_candle_time not in btc_indexed.index:
            return None
        comp_high = btc_indexed.loc[trade.signal_candle_time]["prior_high"]
        if pd.isna(comp_high) or bar["close"] >= comp_high:
            return None
        bh = int((current_time - trade.entry_time).total_seconds() / bt.TIMEFRAME_SECONDS)
        return {"exit_time": current_time, "exit_price": bar["close"],
                "reason": "early_failure_reenter_range", "bars_held": max(bh, 0),
                "is_high_vol": bt.is_high_vol_bar(bar)}
    return f


def _pf(trades):
    gw = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
    gl = abs(sum(t.net_pnl_usd for t in trades if t.net_pnl_usd <= 0))
    return gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)


def _metrics(trades, eq, autopsy_rows):
    n = len(trades)
    if n == 0:
        return {"trades": 0, "winners": 0, "by_year": {}, "by_month": {}, "loss_n": 0}
    by_exit = sorted(trades, key=lambda t: (t.exit_time or t.entry_time))
    wins = [t for t in trades if t.net_pnl_usd > 0]
    net = sum(t.net_pnl_usd for t in trades)
    mcl = cur = 0
    for t in by_exit:
        cur = cur + 1 if t.net_pnl_usd <= 0 else 0
        mcl = max(mcl, cur)
    nets = sorted((t.net_pnl_usd for t in trades), reverse=True)
    best = max(trades, key=lambda t: t.net_pnl_usd)
    ex_best = [t for t in trades if t is not best]
    months = {}
    for t in trades:
        k = (t.exit_time or t.entry_time).strftime("%Y-%m")
        months[k] = months.get(k, 0.0) + t.net_pnl_usd
    best_month = max(months.values()) if months else 0.0
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
        "trades": n, "winners": len(wins), "losers": n - len(wins),
        "win_rate": len(wins) / n * 100, "pf": _pf(trades),
        "avg_r": float(np.mean([t.r_multiple for t in trades])), "net": net,
        "max_dd": max((p["drawdown_pct"] for p in eq), default=0.0),
        "max_consec_losses": mcl,
        "top_pct": (best.net_pnl_usd / net * 100) if net > 0 else float("nan"),
        "top3_pct": (sum(nets[:3]) / net * 100) if net > 0 else float("nan"),
        "best_month_pct": (best_month / net * 100) if net > 0 else float("nan"),
        "exbest_net": sum(t.net_pnl_usd for t in ex_best),
        "exbest_avg_r": float(np.mean([t.r_multiple for t in ex_best])) if ex_best else float("nan"),
        "exbest_pf": _pf(ex_best),
        "is_pf": _pf(is_t), "oos_pf": _pf(oos_t), "is_n": len(is_t), "oos_n": len(oos_t),
        "by_year": by_year, "by_month": months, "loss_n": len(autopsy_rows),
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


def _flags(m):
    out = []
    n = m.get("trades", 0)
    if n == 0:
        return ["no trades"]
    out.append("DIAGNOSTIC ONLY")
    if n < 30:
        out.append("NOT DEPLOYABLE(<30)")
    if m.get("winners", 0) < WINNER_GATE:
        out.append(f"LOW_WINNER_COUNT({m.get('winners',0)})")
    if m["exbest_pf"] < 1.0 or (m["pf"] != float("inf") and m["exbest_pf"] < 0.7 * m["pf"]):
        out.append("FRAGILE(ex-best)")
    if m["top_pct"] == m["top_pct"] and m["top_pct"] > 50:
        out.append("TOP-TRADE DEPENDENT")
    if m["best_month_pct"] == m["best_month_pct"] and m["best_month_pct"] > MONTH_DOMINANCE * 100:
        out.append("MONTH-DEPENDENT")
    return out


def print_comparison(m_by_v):
    labels = list(m_by_v)
    print("\n" + "=" * 100)
    print("BTC 12H compression breakout — baseline / full / +early-failure-exit (DIAGNOSTIC ONLY)")
    print("=" * 100)
    rows = [
        ("Trades", lambda m: str(m.get("trades", 0))),
        ("Winners", lambda m: str(m.get("winners", 0))),
        ("Losers", lambda m: str(m.get("losers", 0))),
        ("Win rate", lambda m: _f(m.get("win_rate", float("nan")), pct=True)),
        ("Profit factor", lambda m: _f(m.get("pf", float("nan")))),
        ("Avg R", lambda m: _f(m.get("avg_r", float("nan")), pos=True)),
        ("Net P&L $", lambda m: _f(m.get("net", 0.0), pos=True)),
        ("Max drawdown %", lambda m: _f(m.get("max_dd", 0.0))),
        ("Max consec losses", lambda m: str(m.get("max_consec_losses", 0))),
        ("Top trade % of net", lambda m: _f(m.get("top_pct", float("nan")), pct=True)),
        ("Top-3 % of net", lambda m: _f(m.get("top3_pct", float("nan")), pct=True)),
        ("Best month % of net", lambda m: _f(m.get("best_month_pct", float("nan")), pct=True)),
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
    w = 26
    print(f"{'Metric':<22}" + "".join(f"{lab[:w]:>{w}}" for lab in labels))
    print("-" * 100)
    for label, fn in rows:
        print(f"{label:<22}" + "".join(f"{fn(m_by_v[lab]):>{w}}" for lab in labels))
    print("\nPerformance by year (net $ | PF | n):")
    yrs = sorted(set().union(*[set(m.get("by_year", {})) for m in m_by_v.values()]))
    for y in yrs:
        cells = []
        for lab in labels:
            a = m_by_v[lab].get("by_year", {}).get(y)
            cells.append(f"{lab}={a['net']:+.1f}|{_f(a['pf'])}|{a['n']}" if a else f"{lab}=-")
        print(f"  {y}: " + "  ".join(cells))
    print("\nVerdicts:")
    for lab in labels:
        print(f"  {lab:<34}: {len(m_by_v[lab].get('by_month', {}))} months -> {', '.join(_flags(m_by_v[lab]))}")
    print("\nEarly-failure rule: exit if a bar closes back inside the prior compression range")
    print("(close < signal-bar prior 20-bar high). NOTE: diagnostic only — no deploy/paper/live,")
    print("no parameter tuning. Do not advance any variant on PF alone.")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    saved = {k: getattr(bt, k) for k in TF_OVERRIDES}
    for k, v in TF_OVERRIDES.items():
        setattr(bt, k, v)
    try:
        df = bc._build_btc_df(ENTRY_SECONDS, FETCH_GRAN, AGG, args.days)
        btc_indexed = df.set_index("time")
        early = _make_early_exit(btc_indexed)
        full = lambda r: r["comp_count_prev"] == 3
        variants = [
            ("baseline", None, None),
            ("full_compression_only", full, None),
            ("baseline_early_failure_exit", None, early),
            ("full_compression_early_failure_exit", full, early),
        ]
        m_by_v = {}
        for label, filt, ex in variants:
            trades, eq = bc._run_btc_compression(btc_indexed, extra_filter=filt, early_exit_fn=ex)
            autopsy = bt.build_loss_autopsy(trades, {"BTC-USD": df})
            bt.export_trades_csv(trades, os.path.join(OUT_DIR, f"trades_{label}.csv"))
            bt.export_equity_csv(eq, os.path.join(OUT_DIR, f"equity_{label}.csv"))
            bt.export_loss_autopsy_csv(autopsy, os.path.join(OUT_DIR, f"loss_autopsy_{label}.csv"))
            bt.export_regime_behavior_csv(bt.build_regime_behavior_report(trades, {"BTC-USD": df}),
                                          os.path.join(OUT_DIR, f"regime_behavior_{label}.csv"))
            m_by_v[label] = _metrics(trades, eq, autopsy)
            print(f"variant {label}: {len(trades)} trades")
    finally:
        for k, v in saved.items():
            setattr(bt, k, v)

    print_comparison(m_by_v)


if __name__ == "__main__":
    main()
