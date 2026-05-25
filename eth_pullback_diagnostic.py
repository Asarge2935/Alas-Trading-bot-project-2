"""
ETH pullback-continuation DIAGNOSTIC — adds a second entry setup alongside the
existing breakout, WITHOUT modifying or deleting the breakout. Every trade is
tagged setup_type. Diagnostic only — not for deployment.

Thesis: the ETH-long breakout edge looks promising but is too rare. Test
whether the SAME momentum/regime thesis (BTC strict risk_on, ETH self-uptrend,
ETH RS rank 1) produces more sample via pullback-to-EMA20 continuation entries.

The engine (backtest.run_backtest) gained a `setup` selector: "breakout"
(default, canonical, unchanged), "pullback" (pullback_continuation), or "both".
This harness runs all three on the same 6H BTC/ETH/SOL data, ETH-USD long-only,
strict-regime, and reports:
    - breakout only      (setup="breakout")
    - pullback only      (setup="pullback")
    - combined           (setup="both"; breakout has priority for the 1 slot)

Entry/exit/cost/sizing/risk are reused unchanged; breakout parameters are not
touched. Pullback entry conditions are defined in backtest.evaluate_pullback.

Run:
    python3 eth_pullback_diagnostic.py [--days N]

Outputs (separate dirs so attribution is unambiguous):
    backtest_output_pullback/combined/{trades,equity_curve,loss_autopsy,
                                       regime_behavior_report}.csv   (setup="both")
    backtest_output_pullback/standalone_breakout/{trades,loss_autopsy}.csv
    backtest_output_pullback/standalone_pullback/{trades,loss_autopsy}.csv
    + console 3-way report, count reconciliation, and five labeled robustness
      reports (2 standalone, combined-all, 2 combined-run attributions).
"""

import argparse
import os
import subprocess
import sys
import numpy as np
import pandas as pd

import backtest as bt

OUT_DIR = "backtest_output_pullback"
ETH_ARGS = dict(trade_assets=["ETH-USD"], long_only=True, strict_regime=True)


def _load_6h(days):
    data = {}
    for sym in bt.ASSETS:
        df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)
        data[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
    return data


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


def print_three_way(mb, mp, mc):
    print("\n" + "=" * 82)
    print("ETH-long-only strict — BREAKOUT vs PULLBACK_CONTINUATION vs COMBINED "
          "(DIAGNOSTIC)")
    print("=" * 82)
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
    print(f"{'Metric':<22}{'breakout':>14}{'pullback':>16}{'combined':>14}")
    print("-" * 82)
    for label, fn in rows:
        print(f"{label:<22}{fn(mb):>14}{fn(mp):>16}{fn(mc):>14}")

    print("\nPerformance by year (net $ | PF | avgR | n):")
    yrs = sorted(set(mb.get("by_year", {})) | set(mp.get("by_year", {})) | set(mc.get("by_year", {})))
    for y in yrs:
        def yf(m):
            a = m.get("by_year", {}).get(y)
            return f"{a['net']:+.2f}|{_f(a['pf'])}|{a['avg_r']:+.2f}|{a['n']}" if a else "-"
        print(f"  {y}:  breakout {yf(mb):<22} pullback {yf(mp):<22} combined {yf(mc)}")

    for name, m in (("breakout", mb), ("pullback_continuation", mp), ("combined", mc)):
        n = m.get("trades", 0)
        flag = "LOW/!deployable (<30)" if n < 30 else "clears 30-trade gate (still validate OOS)"
        print(f"  {name:<22}: {n} trades -> {flag}")
    print("\nNOTE: diagnostic only — not for deployment, not a live-trading change.")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()

    data = _load_6h(args.days)
    tb, eqb = bt.run_backtest(data, setup="breakout", **ETH_ARGS)
    tp, eqp = bt.run_backtest(data, setup="pullback", **ETH_ARGS)
    tc, eqc = bt.run_backtest(data, setup="both", **ETH_ARGS)
    print(f"breakout-only: {len(tb)} | pullback-only: {len(tp)} | combined: {len(tc)}")

    autopsy_c = bt.build_loss_autopsy(tc, data)
    mb = _metrics(tb, eqb, bt.build_loss_autopsy(tb, data))
    mp = _metrics(tp, eqp, bt.build_loss_autopsy(tp, data))
    mc = _metrics(tc, eqc, autopsy_c)
    print_three_way(mb, mp, mc)
    _print_count_reconciliation(tb, tp, tc)

    # Write three independent trade sets so robustness can attribute correctly.
    # STANDALONE dirs: every trade in them is that one setup. COMBINED dir: the
    # "both" run, where breakout has priority and the two setups compete for the
    # single position slot (so per-setup counts here differ from standalone).
    combined_dir = os.path.join(OUT_DIR, "combined")
    sb_dir = os.path.join(OUT_DIR, "standalone_breakout")
    sp_dir = os.path.join(OUT_DIR, "standalone_pullback")
    for d in (combined_dir, sb_dir, sp_dir):
        os.makedirs(d, exist_ok=True)
        for stale in ("trades.csv", "loss_autopsy.csv"):  # avoid reusing a prior run's file
            fp = os.path.join(d, stale)
            if os.path.exists(fp):
                os.remove(fp)
    # Combined outputs (trades.csv carries setup_type for every trade).
    bt.export_trades_csv(tc, os.path.join(combined_dir, "trades.csv"))
    bt.export_equity_csv(eqc, os.path.join(combined_dir, "equity_curve.csv"))
    bt.export_loss_autopsy_csv(autopsy_c, os.path.join(combined_dir, "loss_autopsy.csv"))
    bt.export_regime_behavior_csv(bt.build_regime_behavior_report(tc, data),
                                  os.path.join(combined_dir, "regime_behavior_report.csv"))
    # Standalone outputs (trades + losers needed by robustness_report).
    bt.export_trades_csv(tb, os.path.join(sb_dir, "trades.csv"))
    bt.export_loss_autopsy_csv(bt.build_loss_autopsy(tb, data), os.path.join(sb_dir, "loss_autopsy.csv"))
    bt.export_trades_csv(tp, os.path.join(sp_dir, "trades.csv"))
    bt.export_loss_autopsy_csv(bt.build_loss_autopsy(tp, data), os.path.join(sp_dir, "loss_autopsy.csv"))

    # Five clearly-labeled robustness reports: 2 standalone, combined-all, and
    # 2 combined-run attributions (filtered by actual setup_type).
    runs = [
        (sb_dir, None, "STANDALONE breakout run"),
        (sp_dir, None, "STANDALONE pullback run"),
        (combined_dir, None, "COMBINED run (all setups)"),
        (combined_dir, "breakout", "COMBINED-run attribution: breakout"),
        (combined_dir, "pullback_continuation", "COMBINED-run attribution: pullback_continuation"),
    ]
    print("\n=== robustness reports (standalone vs combined attribution) ===")
    for d, st, label in runs:
        if not os.path.exists(os.path.join(d, "trades.csv")):
            print("=" * 78)
            print(f"ETH ROBUSTNESS REPORT — {label}: 0 trades (no file written). Skipped.")
            continue
        cmd = [sys.executable, "robustness_report.py", "--dir", d, "--label", label]
        if st:
            cmd += ["--setup-type", st]
        subprocess.run(cmd, check=False)


def _print_count_reconciliation(tb, tp, tc):
    from collections import Counter
    by = Counter(t.setup_type for t in tc)
    cb, cp = by.get("breakout", 0), by.get("pullback_continuation", 0)
    print("\n" + "-" * 82)
    print("COUNT RECONCILIATION (why standalone != combined-attribution)")
    print("-" * 82)
    print(f"  standalone breakout : {len(tb):>3}   |  in COMBINED run: {cb:>3}  "
          f"(blocked: {len(tb) - cb})")
    print(f"  standalone pullback : {len(tp):>3}   |  in COMBINED run: {cp:>3}  "
          f"(blocked: {len(tp) - cp})")
    print(f"  combined total      : {len(tc):>3}   ({cb} breakout + {cp} pullback_continuation)")
    print("  CAUSE: not a bug. The combined ('both') run holds MAX_OPEN_POSITIONS=1")
    print("  and gives BREAKOUT priority, so the two setups compete for the single")
    print("  slot: a position open from one setup blocks signals from the other,")
    print("  and on a bar where both fire, breakout wins. Hence combined-attribution")
    print(f"  counts ({cb}/{cp}) differ from standalone counts ({len(tb)}/{len(tp)}), and")
    print(f"  combined total ({len(tc)}) != standalone sum ({len(tb) + len(tp)}).")


if __name__ == "__main__":
    main()
