"""
ETH INDEPENDENT-DATA VALIDATION — runs the EXISTING ETH 6H strict-regime
breakout diagnostic against built-in data OR a user-supplied OHLCV CSV, to test
structural persistence on independent data. No strategy-rule changes, no
optimization.

Modes:
    python3 eth_independent_data_validation.py                       # built-in data (all 3 assets)
    python3 eth_independent_data_validation.py --csv eth.csv --label binance_eth_spot

IMPORTANT — strict-regime needs cross-asset context:
  The ETH strict breakout requires (a) the BTC daily regime and (b) ETH being
  relative-strength rank 1 among BTC/ETH/SOL. A SINGLE ETH CSV is therefore
  NOT sufficient on its own. This script handles CSV mode as follows:
    - ETH bars come from the CSV (resampled to 6H).
    - BTC and SOL context come from the CURRENT built-in provider.
    - Trades are simulated only on the time-INTERSECTION of ETH-CSV and the
      built-in BTC/SOL bars. Non-overlapping ETH-CSV history is skipped.
  So: it CANNOT run strict-regime from ETH alone; it USES current BTC/SOL data
  only where timestamps align. If you want fully independent strict-regime
  validation, supply matching BTC (and SOL) CSVs (future extension).

Outputs (backtest_output_eth_independent/<label>/): trades.csv, summary.txt
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt
from eth_quality_audit_v2 import _excursion_path, _pf, _fpf
from ohlcv_csv_validation_harness import load_ohlcv_csv, resample_ohlcv

OUT_BASE = "backtest_output_eth_independent"
SAMPLE_GATE, WINNER_GATE = 30, 12


def _built_in_6h(days):
    out = {}
    for sym in bt.ASSETS:
        df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)
        out[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
    return out


def build(days, csv_path, label):
    note = ""
    if csv_path:
        raw, errs = load_ohlcv_csv(csv_path)
        eth_6h = resample_ohlcv(raw, "6h")
        eth = bt.add_indicators(bt.drop_incomplete_candles(eth_6h, bt.TIMEFRAME_SECONDS))
        # cross-asset context from current provider:
        ctx = {}
        for sym in ("BTC-USD", "SOL-USD"):
            df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)
            ctx[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
        data = {"BTC-USD": ctx["BTC-USD"], "ETH-USD": eth, "SOL-USD": ctx["SOL-USD"]}
        note = (f"CSV mode ({label}): ETH from CSV ({len(eth)} 6H bars after resample); "
                f"BTC/SOL from built-in provider; simulated on time-intersection. "
                + ("CSV warnings: " + "; ".join(errs) if errs else "no CSV warnings."))
    else:
        data = _built_in_6h(days)
        note = "built-in mode: BTC/ETH/SOL from current provider."
    trades, eq = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                                 strict_regime=True, setup="breakout")
    return trades, eq, data["ETH-USD"].set_index("time"), note


def render(trades, eq, eth_indexed, label, note):
    L = ["=" * 88,
         f"ETH INDEPENDENT-DATA VALIDATION — 6H strict breakout — label='{label}' (DIAGNOSTIC ONLY)",
         "=" * 88, f"  {note}"]
    n = len(trades)
    if n == 0:
        L.append("  No ETH breakout trades (check timestamp overlap / regime context). NOT DEPLOYABLE.")
        return "\n".join(L)
    wins = [t for t in trades if t.net_pnl_usd > 0]
    net = sum(t.net_pnl_usd for t in trades)
    nets = sorted((t.net_pnl_usd for t in trades), reverse=True)
    best = max(trades, key=lambda t: t.net_pnl_usd)
    ex_best = [t for t in trades if t is not best]
    max_dd = max((p["drawdown_pct"] for p in eq), default=0.0)
    months = {}
    for t in trades:
        k = (t.exit_time or t.entry_time).strftime("%Y-%m")
        months[k] = months.get(k, 0.0) + t.net_pnl_usd
    warns = []
    if n < SAMPLE_GATE:
        warns.append(f"LOW_SAMPLE({n}<30)/NOT_DEPLOYABLE")
    if len(wins) < WINNER_GATE:
        warns.append(f"LOW_WINNER_COUNT({len(wins)}<12)")
    top_pct = best.net_pnl_usd / net * 100 if net > 0 else float("nan")
    top3 = sum(nets[:3]) / net * 100 if net > 0 else float("nan")
    bm = (max(months.values()) / net * 100) if (months and net > 0) else float("nan")
    if (top_pct == top_pct and top_pct > 30) or (top3 == top3 and top3 > 80) or (bm == bm and bm > 45):
        warns.append("DISTRIBUTION_CONCENTRATION")
    warns.append("HYPOTHESIS_ONLY")

    L.append("\n-- CORE --")
    L.append(f"  trades={n}  winners={len(wins)}  losers={n-len(wins)}  win%={len(wins)/n*100:.1f}")
    L.append(f"  PF={_fpf(_pf(trades))}  avgR={np.mean([t.r_multiple for t in trades]):+.3f}  "
             f"net=${net:+.2f}  maxDD={max_dd:.2f}%")
    if ex_best:
        L.append(f"  ex-best PF={_fpf(_pf(ex_best))}  ex-best net=${sum(t.net_pnl_usd for t in ex_best):+.2f}")
    L.append(f"  top trade %={top_pct:.1f}  top-3 %={top3:.1f}  best month %={bm:.1f}")
    L.append(f"  WARNINGS: {', '.join(warns)}")

    def grp(fmt_or):
        d = {}
        for t in trades:
            dt = (t.exit_time or t.entry_time)
            k = dt.strftime(fmt_or) if isinstance(fmt_or, str) else f"{dt.year}-Q{(dt.month-1)//3+1}"
            d.setdefault(k, []).append(t)
        return d
    L.append("\n-- YEARLY --")
    for k, v in sorted(grp("%Y").items()):
        L.append(f"  {k}: n={len(v)} net=${sum(x.net_pnl_usd for x in v):+.1f} PF={_fpf(_pf(v))} "
                 f"avgR={np.mean([x.r_multiple for x in v]):+.2f}")
    L.append("-- QUARTERLY --")
    for k, v in sorted(grp(None).items()):
        L.append(f"  {k}: n={len(v)} net=${sum(x.net_pnl_usd for x in v):+.1f} PF={_fpf(_pf(v))}")

    # failure-to-separate
    L.append("\n-- FAILURE-TO-SEPARATE (descriptive; NOT a rule) --")
    ex = {id(t): _excursion_path(t, eth_indexed) for t in trades}
    def fast(t):
        b = ex[id(t)]["bars_to_0_5R"]
        return isinstance(b, int) and b <= 2
    def before_mae(t, thr):
        e = ex[id(t)]
        b = e["bars_to_0_5R"] if thr == 0.5 else e["bars_to_1R"]
        a = e["bars_to_adv_0_5R"]
        return isinstance(b, int) and (a == "" or b <= a)
    fa = [t for t in trades if fast(t)]
    sl = [t for t in trades if not fast(t)]
    def sub(g):
        return (f"n={len(g)} avgR={np.mean([t.r_multiple for t in g]):+.2f} "
                f"win%={sum(1 for t in g if t.net_pnl_usd>0)/len(g)*100:.0f}") if g else "n=0"
    L.append(f"  fast starters (+0.5R within 2 bars): {sub(fa)}")
    L.append(f"  slow starters: {sub(sl)}")
    L.append(f"  reached +0.5R before 0.5R MAE: winners {sum(1 for t in wins if before_mae(t,0.5))}/{len(wins)}, "
             f"losers {sum(1 for t in trades if t.net_pnl_usd<=0 and before_mae(t,0.5))}/{n-len(wins)}")
    L.append(f"  reached +1R before 0.5R MAE: winners {sum(1 for t in wins if before_mae(t,1.0))}/{len(wins)}")
    L.append("\n  DIAGNOSTIC ONLY. Structural-persistence validation, not optimization. "
             "Nothing here is deployable.")
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    p.add_argument("--csv", default=None)
    p.add_argument("--label", default="builtin")
    args = p.parse_args()

    trades, eq, eth_indexed, note = build(args.days, args.csv, args.label)
    out_dir = os.path.join(OUT_BASE, args.label)
    os.makedirs(out_dir, exist_ok=True)
    bt.export_trades_csv(trades, os.path.join(out_dir, "trades.csv"))
    report = render(trades, eq, eth_indexed, args.label, note)
    print("\n" + report)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote {out_dir}/summary.txt")


if __name__ == "__main__":
    main()
