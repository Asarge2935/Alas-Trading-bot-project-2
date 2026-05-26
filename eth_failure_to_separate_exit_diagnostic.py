"""
ETH FAILURE-TO-SEPARATE EXIT DIAGNOSTIC — diagnostic only, no deployed-rule change.

Tests ONE deterministic exit hypothesis on the existing ETH 6H strict-regime
breakout, on BOTH datasets, baseline vs with-exit:

  RULE (single, fixed, no tuning): if the trade has NOT reached +0.5R favorable
  (intrabar high) by the close of bar 2 after entry, exit at that bar-2 close
  (reason "failure_to_separate"). The normal stop/partial/trail/time-stop keep
  PRIORITY; this only fires when none of those did and the trade hasn't
  separated by bar 2.

It is applied via run_backtest's opt-in early_exit_fn hook (default None =
unchanged baseline). The deployed ETH strategy is NOT modified.

Datasets:
  - coinbase_builtin            : current built-in provider (needs network)
  - binance_fully_indep         : ETH/BTC/SOL all from CSV (--eth-csv/--btc-csv/--sol-csv)

Judged on (NOT PF alone): ex-best PF, max drawdown, top-trade %, top-3 %,
best-month %, yearly stability — and whether the exit helps on BOTH datasets.

Run:
    python3 eth_failure_to_separate_exit_diagnostic.py
    python3 eth_failure_to_separate_exit_diagnostic.py --eth-csv ~/eth.csv \\
        --btc-csv ~/btc.csv --sol-csv ~/sol.csv --label binance_fully_indep

Outputs (backtest_output_eth_fts_exit/<dataset>/): trades_baseline.csv, trades_exit.csv, summary.txt
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt
from ohlcv_csv_validation_harness import load_ohlcv_csv, resample_ohlcv

OUT_BASE = "backtest_output_eth_fts_exit"
K_BARS = 2          # bar offset after entry (fixed; do NOT tune)
R_THRESH = 0.5      # required favorable excursion by bar K (fixed)


def make_fts_exit(eth_indexed):
    """Exit at the close of bar K after entry if max favorable excursion (high)
    has not reached +R_THRESH by then. Long-only (the ETH setup is long-only)."""
    idx = eth_indexed.index

    def f(trade, bar, current_time):
        offset = int(round((current_time - trade.entry_time).total_seconds() / bt.TIMEFRAME_SECONDS))
        if offset != K_BARS:
            return None
        R = trade.initial_stop_distance
        if R <= 0:
            return None
        window = eth_indexed.loc[(idx >= trade.entry_time) & (idx <= current_time)]
        if window.empty:
            return None
        max_fav_r = (window["high"].max() - trade.entry_price) / R   # long
        if max_fav_r < R_THRESH:
            return {"exit_time": current_time, "exit_price": bar["close"],
                    "reason": "failure_to_separate", "bars_held": offset,
                    "is_high_vol": bt.is_high_vol_bar(bar)}
        return None
    return f


def _csv_6h(path):
    raw, _ = load_ohlcv_csv(path)
    return bt.add_indicators(bt.drop_incomplete_candles(resample_ohlcv(raw, "6h"), bt.TIMEFRAME_SECONDS))


def build_dataset(days, eth_csv, btc_csv, sol_csv):
    if eth_csv:
        return {"ETH-USD": _csv_6h(eth_csv), "BTC-USD": _csv_6h(btc_csv), "SOL-USD": _csv_6h(sol_csv)}
    data = {}
    for sym in bt.ASSETS:
        df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)
        data[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
    return data


def _pf(trades):
    gw = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
    gl = abs(sum(t.net_pnl_usd for t in trades if t.net_pnl_usd <= 0))
    return gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)


def _fpf(x):
    return "inf" if x == float("inf") else ("n/a" if x != x else f"{x:.2f}")


def metrics(trades, eq):
    n = len(trades)
    if n == 0:
        return {"n": 0}
    wins = [t for t in trades if t.net_pnl_usd > 0]
    net = sum(t.net_pnl_usd for t in trades)
    nets = sorted((t.net_pnl_usd for t in trades), reverse=True)
    best = max(trades, key=lambda t: t.net_pnl_usd)
    exb = [t for t in trades if t is not best]
    by_exit = sorted(trades, key=lambda t: (t.exit_time or t.entry_time))
    mcl = cur = 0
    for t in by_exit:
        cur = cur + 1 if t.net_pnl_usd <= 0 else 0
        mcl = max(mcl, cur)
    months = {}
    for t in trades:
        k = (t.exit_time or t.entry_time).strftime("%Y-%m")
        months[k] = months.get(k, 0.0) + t.net_pnl_usd
    years = {}
    for t in trades:
        years.setdefault(t.entry_time.year, []).append(t)
    by_year = {y: (sum(x.net_pnl_usd for x in v), _pf(v), len(v)) for y, v in years.items()}
    pos_years = sum(1 for v in by_year.values() if v[0] > 0)
    return {
        "n": n, "w": len(wins), "l": n - len(wins), "winr": len(wins) / n * 100,
        "pf": _pf(trades), "avg_r": float(np.mean([t.r_multiple for t in trades])), "net": net,
        "max_dd": max((p["drawdown_pct"] for p in eq), default=0.0), "mcl": mcl,
        "exbest_pf": _pf(exb), "exbest_net": sum(t.net_pnl_usd for t in exb),
        "top_pct": best.net_pnl_usd / net * 100 if net > 0 else float("nan"),
        "top3_pct": sum(nets[:3]) / net * 100 if net > 0 else float("nan"),
        "best_month_pct": (max(months.values()) / net * 100) if (months and net > 0) else float("nan"),
        "by_year": by_year, "pos_years": pos_years, "n_years": len(by_year),
    }


def _row(label, a, b, fmt):
    return f"  {label:<20}{fmt(a):>14}{fmt(b):>14}"


def run_dataset(name, data, out_dir):
    eth_indexed = data["ETH-USD"].set_index("time")
    tb, eqb = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                              strict_regime=True, setup="breakout")
    te, eqe = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                              strict_regime=True, setup="breakout",
                              early_exit_fn=make_fts_exit(eth_indexed))
    os.makedirs(out_dir, exist_ok=True)
    bt.export_trades_csv(tb, os.path.join(out_dir, "trades_baseline.csv"))
    bt.export_trades_csv(te, os.path.join(out_dir, "trades_exit.csv"))
    mb, me = metrics(tb, eqb), metrics(te, eqe)

    L = ["=" * 60, f"DATASET: {name}  (baseline vs +failure-to-separate exit)", "=" * 60]
    if mb["n"] == 0:
        L.append("  baseline produced 0 trades (check overlap/network).")
        return "\n".join(L), mb, me
    n_fts = sum(1 for t in te if t.exit_reason == "failure_to_separate")
    L.append(f"  exit rule: not +{R_THRESH}R by bar {K_BARS} -> exit at close. "
             f"fts-exits fired: {n_fts}/{me['n']} trades")
    L.append(f"  {'metric':<20}{'baseline':>14}{'+fts_exit':>14}")
    L.append("  " + "-" * 48)
    f2 = lambda x: f"{x:.2f}" if isinstance(x, float) else str(x)
    fpct = lambda x: ("n/a" if (isinstance(x, float) and x != x) else f"{x:.1f}%")
    L.append(_row("trades", mb["n"], me["n"], str))
    L.append(_row("winners", mb["w"], me["w"], str))
    L.append(_row("win rate", mb["winr"], me["winr"], fpct))
    L.append(_row("PF", _fpf(mb["pf"]), _fpf(me["pf"]), str))
    L.append(_row("avg R", f"{mb['avg_r']:+.3f}", f"{me['avg_r']:+.3f}", str))
    L.append(_row("net $", f"{mb['net']:+.2f}", f"{me['net']:+.2f}", str))
    L.append(_row("max drawdown %", mb["max_dd"], me["max_dd"], f2))
    L.append(_row("max consec losses", mb["mcl"], me["mcl"], str))
    L.append(_row("ex-best PF", _fpf(mb["exbest_pf"]), _fpf(me["exbest_pf"]), str))
    L.append(_row("ex-best net $", f"{mb['exbest_net']:+.2f}", f"{me['exbest_net']:+.2f}", str))
    L.append(_row("top trade %", mb["top_pct"], me["top_pct"], fpct))
    L.append(_row("top-3 %", mb["top3_pct"], me["top3_pct"], fpct))
    L.append(_row("best month %", mb["best_month_pct"], me["best_month_pct"], fpct))
    L.append(_row("positive years", f"{mb['pos_years']}/{mb['n_years']}", f"{me['pos_years']}/{me['n_years']}", str))
    L.append("\n  yearly net$|PF|n  (baseline  ||  +exit):")
    for y in sorted(set(mb["by_year"]) | set(me["by_year"])):
        a = mb["by_year"].get(y)
        c = me["by_year"].get(y)
        ac = f"{a[0]:+.1f}|{_fpf(a[1])}|{a[2]}" if a else "-"
        cc = f"{c[0]:+.1f}|{_fpf(c[1])}|{c[2]}" if c else "-"
        L.append(f"    {y}: {ac:<18} ||  {cc}")
    return "\n".join(L), mb, me


def robustness_delta(mb, me):
    """Per-dataset: did the exit IMPROVE robustness (not PF)? Returns flags."""
    if mb.get("n", 0) == 0 or me.get("n", 0) == 0:
        return ["no-data"]
    out = []
    out.append("ex-best PF " + ("UP" if me["exbest_pf"] > mb["exbest_pf"] else "down/flat"))
    out.append("maxDD " + ("DOWN" if me["max_dd"] < mb["max_dd"] else "up/flat"))
    out.append("top% " + ("DOWN" if me["top_pct"] < mb["top_pct"] else "up/flat"))
    out.append("bestMonth% " + ("DOWN" if me["best_month_pct"] < mb["best_month_pct"] else "up/flat"))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    p.add_argument("--eth-csv", default=None)
    p.add_argument("--btc-csv", default=None)
    p.add_argument("--sol-csv", default=None)
    p.add_argument("--label", default="binance_fully_indep")
    p.add_argument("--no-builtin", action="store_true")
    args = p.parse_args()

    datasets = []
    if not args.no_builtin:
        datasets.append(("coinbase_builtin", lambda: build_dataset(args.days, None, None, None)))
    if args.eth_csv:
        if not (args.btc_csv and args.sol_csv):
            print("ERROR: --eth-csv requires --btc-csv and --sol-csv for fully-independent strict regime.")
            return
        datasets.append((args.label, lambda: build_dataset(args.days, args.eth_csv, args.btc_csv, args.sol_csv)))

    results = {}
    for name, builder in datasets:
        try:
            data = builder()
        except Exception as e:
            print(f"\n[{name}] data build failed: {e.__class__.__name__}: {e}")
            continue
        report, mb, me = run_dataset(name, data, os.path.join(OUT_BASE, name))
        print("\n" + report)
        with open(os.path.join(OUT_BASE, name, "summary.txt"), "w") as f:
            f.write(report + "\n")
        results[name] = (mb, me)
        print(f"\n  ROBUSTNESS DELTA ({name}): " + ", ".join(robustness_delta(mb, me)))

    print("\n" + "=" * 60)
    print("CROSS-DATASET VERDICT (failure-to-separate exit, K=2, +0.5R)")
    print("=" * 60)
    if len(results) < 2:
        print("  Only one dataset available (need both Coinbase built-in AND Binance fully-")
        print("  independent CSVs to judge cross-dataset). Run with --eth-csv/--btc-csv/--sol-csv,")
        print("  and on a machine with network for the built-in Coinbase dataset.")
    else:
        def helped(name):
            mb, me = results[name]
            return (me["exbest_pf"] > mb["exbest_pf"]) and (me["max_dd"] <= mb["max_dd"]) \
                and (me["best_month_pct"] <= mb["best_month_pct"])
        both = all(helped(n) for n in results)
        print(f"  improves ex-best & dd & concentration on ALL datasets: {both}")
        for n in results:
            print(f"    {n}: {'helped' if helped(n) else 'did NOT clearly help'}")
        print("  -> A real effect should help on BOTH. One-sided improvement = likely noise.")
    print("\nDIAGNOSTIC ONLY. Judged on robustness, not PF. Nothing here is deployable; no "
          "tuning, no paper, no live. Research/analysis only — not financial advice.")


if __name__ == "__main__":
    main()
