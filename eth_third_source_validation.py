"""
ETH THIRD-SOURCE VALIDATION — diagnostic only. Runs the EXACT SAME ETH 6H strict
breakout, baseline vs the FIXED failure-to-separate exit (K=2 / +0.5R), on a
THIRD, unseen venue/instrument supplied as CSV. No tuning, no sweep, no rule
change. The goal is persistence on data the rule was never derived from — NOT
better numbers.

Context handling (strict regime needs BTC + relative strength among BTC/ETH/SOL):
  - all of --eth-csv/--btc-csv/--sol-csv  -> MATCHING-VENUE (faithful strict regime)
  - --eth-csv + --btc-csv, no SOL         -> PARTIAL CONTEXT: RS computed over
                                             BTC/ETH only (NOT the same rule) — clearly flagged
  - no --btc-csv                          -> cannot run strict regime (needs BTC) -> error
Venues are never silently mixed; the context is always labeled.

Run:
    python3 eth_third_source_validation.py --eth-csv ~/eth_third.csv \\
        --btc-csv ~/btc_third.csv --sol-csv ~/sol_third.csv --label kraken_eth_spot

Outputs (backtest_output_eth_third_source/<label>/): trades_baseline.csv, trades_exit.csv, summary.txt
"""

import argparse
import os

import backtest as bt
from eth_failure_to_separate_exit_diagnostic import (
    make_fts_exit, _csv_6h, metrics, _fpf, K_BARS, R_THRESH)

OUT_BASE = "backtest_output_eth_third_source"
SAMPLE_GATE, WINNER_GATE = 30, 12


def _flags(m):
    if m.get("n", 0) == 0:
        return ["DIAGNOSTIC_ONLY", "NOT_DEPLOYABLE", "NO_TRADES"]
    f = ["DIAGNOSTIC_ONLY", "NOT_DEPLOYABLE", "HYPOTHESIS_ONLY"]
    if m["n"] < SAMPLE_GATE:
        f.append(f"LOW_SAMPLE({m['n']}<30)")
    if m["w"] < WINNER_GATE:
        f.append(f"LOW_WINNER_COUNT({m['w']}<12)")
    if m["exbest_pf"] < 1.0 or (m["pf"] != float("inf") and m["exbest_pf"] < 0.7 * m["pf"]):
        f.append("FRAGILE")
    if m["top_pct"] == m["top_pct"] and m["top_pct"] > 50:
        f.append("TOP_TRADE_DEPENDENT")
    if m["best_month_pct"] == m["best_month_pct"] and m["best_month_pct"] > 45:
        f.append("MONTH_DEPENDENT")
    return f


def _row(label, a, b, fmt=str):
    return f"  {label:<20}{fmt(a):>14}{fmt(b):>14}"


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--eth-csv", required=True)
    p.add_argument("--btc-csv", default=None)
    p.add_argument("--sol-csv", default=None)
    p.add_argument("--label", default="third_source_eth")
    args = p.parse_args()

    if not args.btc_csv:
        print("ERROR: --btc-csv is required — the ETH strict regime needs the BTC daily "
              "regime. Supply a matching-venue BTC CSV. (SOL optional but recommended.)")
        return

    eth = _csv_6h(args.eth_csv)
    btc = _csv_6h(args.btc_csv)
    if args.sol_csv:
        data = {"BTC-USD": btc, "ETH-USD": eth, "SOL-USD": _csv_6h(args.sol_csv)}
        context = "MATCHING-VENUE (BTC/ETH/SOL all from third source) — faithful strict regime"
    else:
        data = {"BTC-USD": btc, "ETH-USD": eth}
        context = ("PARTIAL CONTEXT — no SOL: relative strength is computed over BTC/ETH only, "
                   "so this is NOT the exact strict-regime rule (RS universe shrank). Results "
                   "are indicative, not directly comparable.")

    eth_indexed = data["ETH-USD"].set_index("time")
    tb, eqb = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                              strict_regime=True, setup="breakout")
    te, eqe = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                              strict_regime=True, setup="breakout",
                              early_exit_fn=make_fts_exit(eth_indexed))
    out_dir = os.path.join(OUT_BASE, args.label)
    os.makedirs(out_dir, exist_ok=True)
    bt.export_trades_csv(tb, os.path.join(out_dir, "trades_baseline.csv"))
    bt.export_trades_csv(te, os.path.join(out_dir, "trades_exit.csv"))
    mb, me = metrics(tb, eqb), metrics(te, eqe)

    L = ["=" * 64,
         f"ETH THIRD-SOURCE VALIDATION — label='{args.label}' (DIAGNOSTIC ONLY)",
         "=" * 64,
         f"  context: {context}",
         f"  fixed rule (no tuning): not +{R_THRESH}R by bar {K_BARS} -> exit at bar-{K_BARS} close"]
    if mb["n"] == 0:
        L.append("  baseline produced 0 trades (check timestamp overlap / regime context).")
        print("\n".join(L))
        with open(os.path.join(out_dir, "summary.txt"), "w") as f:
            f.write("\n".join(L) + "\n")
        return
    n_fts = sum(1 for t in te if t.exit_reason == "failure_to_separate")
    L.append(f"  fts-exits fired: {n_fts}/{me['n']} trades")
    L.append(f"  {'metric':<20}{'baseline':>14}{'+fts_exit':>14}")
    L.append("  " + "-" * 48)
    fpct = lambda x: ("n/a" if (isinstance(x, float) and x != x) else f"{x:.1f}%")
    L.append(_row("trades", mb["n"], me["n"]))
    L.append(_row("winners", mb["w"], me["w"]))
    L.append(_row("win rate", mb["winr"], me["winr"], fpct))
    L.append(_row("PF", _fpf(mb["pf"]), _fpf(me["pf"])))
    L.append(_row("avg R", f"{mb['avg_r']:+.3f}", f"{me['avg_r']:+.3f}"))
    L.append(_row("net $", f"{mb['net']:+.2f}", f"{me['net']:+.2f}"))
    L.append(_row("max drawdown %", f"{mb['max_dd']:.2f}", f"{me['max_dd']:.2f}"))
    L.append(_row("ex-best PF", _fpf(mb["exbest_pf"]), _fpf(me["exbest_pf"])))
    L.append(_row("ex-best net $", f"{mb['exbest_net']:+.2f}", f"{me['exbest_net']:+.2f}"))
    L.append(_row("top trade %", mb["top_pct"], me["top_pct"], fpct))
    L.append(_row("top-3 %", mb["top3_pct"], me["top3_pct"], fpct))
    L.append(_row("best month %", mb["best_month_pct"], me["best_month_pct"], fpct))
    L.append(_row("positive years", f"{mb['pos_years']}/{mb['n_years']}", f"{me['pos_years']}/{me['n_years']}"))
    L.append("\n  yearly net$|PF|n  (baseline  ||  +exit):")
    for y in sorted(set(mb["by_year"]) | set(me["by_year"])):
        a = mb["by_year"].get(y)
        c = me["by_year"].get(y)
        ac = f"{a[0]:+.1f}|{_fpf(a[1])}|{a[2]}" if a else "-"
        cc = f"{c[0]:+.1f}|{_fpf(c[1])}|{c[2]}" if c else "-"
        L.append(f"    {y}: {ac:<18} ||  {cc}")
    L.append(f"\n  VERDICT FLAGS (+exit run): {', '.join(_flags(me))}")
    L.append("  A real effect should hold here too (ex-best up, DD down, concentration down).")
    L.append("  One-source-only improvement = noise. Judged on robustness, not PF.")
    L.append("  DIAGNOSTIC ONLY — nothing deployable; no tuning, no paper, no live. "
             "Research/analysis only, not financial advice.")
    report = "\n".join(L)
    print("\n" + report)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote {out_dir}/summary.txt")


if __name__ == "__main__":
    main()
