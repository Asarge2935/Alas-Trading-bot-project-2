"""
BTC HTF-ALIGNMENT DIAGNOSTIC — tests ONE structural filter on the BTC 12H
compression breakout. Diagnostic only; no strategy/live/paper changes, no
threshold optimization.

Base setup: the EXISTING BTC 12H compression breakout
(btc_compression_diagnostic.py), used exactly. The only thing varied is a
single higher-timeframe alignment filter applied on top:

  1. baseline                  — 12H compression breakout, no extra filter
  2. close > daily EMA200      — require bar close above the prior-day daily EMA200
  3. daily EMA50 slope > 0     — require the prior-day daily EMA50 to be rising
  4. both                      — close > daily EMA200 AND daily EMA50 slope > 0

Daily EMA200 / EMA50-slope are computed from the 12H series, resampled to 1D,
and shifted one day (prior completed day) so the filter has no look-ahead.
Each variant re-runs the full loop (faithful under the 1-position constraint),
changing nothing else.

Run:
    python3 btc_htf_alignment_diagnostic.py [--days N]

Outputs (backtest_output_btc_htf_alignment/):
    trades_{baseline,ema200,slope,both}.csv + equity/loss_autopsy/regime_behavior
    + console 4-variant comparison.
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt
import btc_compression_diagnostic as bc

OUT_DIR = "backtest_output_btc_htf_alignment"
TF_OVERRIDES = dict(bc.TF_CONFIGS[0][4])   # 12H overrides
ENTRY_SECONDS, FETCH_GRAN, AGG = 43200, 21600, "12h"

VARIANTS = [
    ("baseline", None),
    ("ema200", lambda r: not pd.isna(r["d_ema200"]) and r["close"] > r["d_ema200"]),
    ("slope", lambda r: not pd.isna(r["d_ema50_slope"]) and r["d_ema50_slope"] > 0),
    ("both", lambda r: not pd.isna(r["d_ema200"]) and not pd.isna(r["d_ema50_slope"])
             and r["close"] > r["d_ema200"] and r["d_ema50_slope"] > 0),
]


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


def _verdict(m, baseline_exbest_pf):
    n = m.get("trades", 0)
    if n == 0:
        return "no trades"
    notes = []
    if n < 30:
        notes.append("NOT deployable (<30)")
    if m["exbest_pf"] < 1.0 or (m["pf"] != float("inf") and m["exbest_pf"] < 0.7 * m["pf"]):
        notes.append("FRAGILE (ex-best collapses)")
    if m["top_pct"] == m["top_pct"] and m["top_pct"] > 50:
        notes.append("top-trade dependent")
    return "; ".join(notes) if notes else "survives ex-best (still validate gates)"


def print_comparison(m_by_v):
    labels = list(m_by_v)
    print("\n" + "=" * 86)
    print("BTC 12H compression breakout + HTF alignment — 4 variants (DIAGNOSTIC ONLY)")
    print("=" * 86)
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
    print(f"{'Metric':<22}" + "".join(f"{lab:>15}" for lab in labels))
    print("-" * 86)
    for label, fn in rows:
        print(f"{label:<22}" + "".join(f"{fn(m_by_v[lab]):>15}" for lab in labels))
    print("\nPerformance by year (net $ | PF | avgR | n):")
    yrs = sorted(set().union(*[set(m.get("by_year", {})) for m in m_by_v.values()]))
    for y in yrs:
        line = f"  {y}: "
        for lab in labels:
            a = m_by_v[lab].get("by_year", {}).get(y)
            cell = f"{a['net']:+.2f}|{_f(a['pf'])}|{a['avg_r']:+.2f}|{a['n']}" if a else "-"
            line += f"  {lab}={cell}"
        print(line)
    print()
    base_exbest = m_by_v["baseline"].get("exbest_pf", 0.0)
    for lab in labels:
        print(f"  {lab:<10}: {m_by_v[lab].get('trades', 0)} trades -> {_verdict(m_by_v[lab], base_exbest)}")
    print("\nNOTE: diagnostic only — not for deployment, not paper/live, no parameter tuning.")


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
        daily = bc.daily_htf_features(df)
        norm = df["time"].dt.normalize()
        for col in ("d_ema200", "d_ema50_slope"):
            df[col] = norm.map(daily[col])
        btc_indexed = df.set_index("time")
        m_by_v = {}
        for label, filt in VARIANTS:
            trades, eq = bc._run_btc_compression(btc_indexed, extra_filter=filt)
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
