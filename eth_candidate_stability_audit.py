"""
ETH CANDIDATE STABILITY AUDIT — diagnostic only. Analyzes the EXISTING ETH-USD
long-only 6H strict-regime BREAKOUT setup for stability/robustness. No rule
changes, no new entry filters, no early-failure exits, no backtest.py changes.
Descriptive only; recommends no filter and no deployment.

Run:
    python3 eth_candidate_stability_audit.py [--days N] [--mc 1000]

Outputs (backtest_output_eth_stability/):
    trades.csv, monthly.csv, walkforward.csv, montecarlo_summary.txt, summary.txt
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt
from eth_quality_audit_v2 import _excursion_path, _pf, _fpf

OUT_DIR = "backtest_output_eth_stability"
WINNER_GATE = 12


def _eth_trades(days):
    data = {}
    for sym in bt.ASSETS:
        df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)
        data[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
    trades, eq = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                                 strict_regime=True, setup="breakout")
    return trades, eq, data["ETH-USD"].set_index("time")


def _max_dd_pct(net_sequence, start=None):
    """Max % drawdown of equity = start + cumulative net, peak-to-trough."""
    start = start if start is not None else bt.ACCOUNT_SIZE_USD
    eq = start + np.cumsum(net_sequence)
    eq = np.concatenate([[start], eq])
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100.0
    return float(dd.max())


def _worst_window(seq, k):
    return min((sum(seq[i:i + k]) for i in range(0, max(1, len(seq) - k + 1))), default=0.0)


def render(trades, eq, eth_indexed, mc_iters):
    L = []
    n = len(trades)
    L.append("=" * 90)
    L.append("ETH CANDIDATE STABILITY AUDIT — 6H strict-regime breakout (DIAGNOSTIC ONLY)")
    L.append("=" * 90)
    if n == 0:
        L.append("  No ETH breakout trades in this window.")
        return "\n".join(L), {}
    wins = [t for t in trades if t.net_pnl_usd > 0]
    losses = [t for t in trades if t.net_pnl_usd <= 0]
    net = sum(t.net_pnl_usd for t in trades)
    by_exit = sorted(trades, key=lambda t: (t.exit_time or t.entry_time))
    seq = [t.net_pnl_usd for t in by_exit]
    if n < 30 or len(wins) < WINNER_GATE:
        L.append(f"  *** LOW_SAMPLE: {n} trades, {len(wins)} winners (gates: >=30 trades, "
                 f">=12-15 winners). NOT DEPLOYABLE. Everything below is descriptive/anecdotal. ***")

    # core
    mcl = cur = 0
    for t in by_exit:
        cur = cur + 1 if t.net_pnl_usd <= 0 else 0
        mcl = max(mcl, cur)
    nets = sorted((t.net_pnl_usd for t in trades), reverse=True)
    best = max(trades, key=lambda t: t.net_pnl_usd)
    ex_best = [t for t in trades if t is not best]
    max_dd = max((p["drawdown_pct"] for p in eq), default=0.0)
    months = {}
    for t in trades:
        k = (t.exit_time or t.entry_time).strftime("%Y-%m")
        months[k] = months.get(k, 0.0) + t.net_pnl_usd
    best_month = max(months, key=months.get) if months else None
    L.append("\n-- CORE METRICS --")
    L.append(f"  trades={n}  winners={len(wins)}  losers={len(losses)}  win%={len(wins)/n*100:.1f}")
    L.append(f"  PF={_fpf(_pf(trades))}  avgR={np.mean([t.r_multiple for t in trades]):+.3f}  "
             f"net=${net:+.2f}  maxDD={max_dd:.2f}%  maxConsecLosses={mcl}")
    if ex_best:
        L.append(f"  ex-best: net=${sum(t.net_pnl_usd for t in ex_best):+.2f}  PF={_fpf(_pf(ex_best))}  "
                 f"avgR={np.mean([t.r_multiple for t in ex_best]):+.3f}")
    if net > 0:
        L.append(f"  top trade % of net={best.net_pnl_usd/net*100:.1f}%   top-3 % of net={sum(nets[:3])/net*100:.1f}%")
        L.append(f"  best month={best_month} ({months[best_month]/net*100:.1f}% of net); "
                 f"ex-best-month net=${net-months[best_month]:+.2f}, "
                 f"PF(ex-best-month)={_fpf(_pf([t for t in trades if (t.exit_time or t.entry_time).strftime('%Y-%m')!=best_month]))}")

    # periodic P&L
    L.append("\n-- PERIODIC P&L --")
    def grp(fmt):
        d = {}
        for t in trades:
            d[(t.exit_time or t.entry_time).strftime(fmt)] = d.get((t.exit_time or t.entry_time).strftime(fmt), 0.0) + t.net_pnl_usd
        return d
    for label, fmt in (("monthly", "%Y-%m"), ("quarterly", None), ("yearly", "%Y")):
        if label == "quarterly":
            d = {}
            for t in trades:
                dt = (t.exit_time or t.entry_time)
                key = f"{dt.year}-Q{(dt.month-1)//3+1}"
                d[key] = d.get(key, 0.0) + t.net_pnl_usd
        else:
            d = grp(fmt)
        L.append(f"  {label}: " + "  ".join(f"{k}=${v:+.1f}" for k, v in sorted(d.items())))

    # rolling
    L.append("\n-- ROLLING SEQUENCES (by exit order) --")
    L.append(f"  worst rolling 3-trade net=${_worst_window(seq,3):+.2f}   worst rolling 5-trade net=${_worst_window(seq,5):+.2f}")
    def roll_window_dd(k):
        worst = 0.0
        for i in range(0, max(1, len(seq) - k + 1)):
            w = seq[i:i+k]
            c = np.cumsum(w); peak = np.maximum.accumulate(np.concatenate([[0], c]))
            worst = max(worst, float((peak - np.concatenate([[0], c])).max()))
        return worst
    L.append(f"  worst rolling 3-trade drawdown=${roll_window_dd(3):.2f}   worst rolling 5-trade drawdown=${roll_window_dd(5):.2f}")

    # walk-forward
    L.append("\n-- WALK-FORWARD SPLITS (by time) --")
    t0 = min(t.entry_time for t in trades)
    t1 = max(t.entry_time for t in trades)
    wf_rows = []
    for frac in (0.50, 0.60, 0.70):
        split = t0 + (t1 - t0) * frac
        is_t = [t for t in trades if t.entry_time <= split]
        oos_t = [t for t in trades if t.entry_time > split]
        row = dict(split=f"{int(frac*100)}/{int((1-frac)*100)}",
                   IS_trades=len(is_t), OOS_trades=len(oos_t),
                   IS_PF=_pf(is_t), OOS_PF=_pf(oos_t),
                   IS_avgR=float(np.mean([t.r_multiple for t in is_t])) if is_t else float("nan"),
                   OOS_avgR=float(np.mean([t.r_multiple for t in oos_t])) if oos_t else float("nan"),
                   IS_net=sum(t.net_pnl_usd for t in is_t), OOS_net=sum(t.net_pnl_usd for t in oos_t))
        wf_rows.append(row)
        L.append(f"  {row['split']}: IS n={row['IS_trades']} PF={_fpf(row['IS_PF'])} avgR={row['IS_avgR']:+.3f} "
                 f"net=${row['IS_net']:+.1f}  |  OOS n={row['OOS_trades']} PF={_fpf(row['OOS_PF'])} "
                 f"avgR={row['OOS_avgR']:+.3f} net=${row['OOS_net']:+.1f}")
    L.append("  (LOW_SAMPLE: with so few trades these splits are indicative only.)")

    # Monte Carlo (sequence randomization) — variance/stress diagnostic ONLY
    L.append(f"\n-- MONTE CARLO ({mc_iters} shuffles of trade ORDER; returns preserved) --")
    rng = np.random.default_rng(42)
    arr = np.array(seq, dtype=float)
    dds = np.empty(mc_iters)
    for i in range(mc_iters):
        rng.shuffle(arr)
        dds[i] = _max_dd_pct(arr.tolist())
    L.append(f"  max-DD %: median={np.median(dds):.2f}  p75={np.percentile(dds,75):.2f}  "
             f"p90={np.percentile(dds,90):.2f}  p95={np.percentile(dds,95):.2f}  worst={dds.max():.2f}")
    L.append(f"  probability of ending positive = {'100%' if net>0 else '0%'} "
             f"(final equity is order-invariant; MC only stresses path/drawdown, NOT outcome).")
    L.append("  Monte Carlo is a variance/stress diagnostic only — it implies NO predictive certainty.")

    # failure-to-separate
    L.append("\n-- FAILURE-TO-SEPARATE (descriptive; NOT a trading rule) --")
    ex = {id(t): _excursion_path(t, eth_indexed) for t in trades}
    def reached_before_mae(t, thr):
        e = ex[id(t)]
        f = e["bars_to_0_5R"] if thr == 0.5 else e["bars_to_1R"]
        a = e["bars_to_adv_0_5R"]
        return isinstance(f, int) and (a == "" or f <= a)
    w05 = sum(1 for t in wins if reached_before_mae(t, 0.5)); l05 = sum(1 for t in losses if reached_before_mae(t, 0.5))
    w1 = sum(1 for t in wins if reached_before_mae(t, 1.0)); l1 = sum(1 for t in losses if reached_before_mae(t, 1.0))
    L.append(f"  reached +0.5R before 0.5R MAE: winners {w05}/{len(wins)}, losers {l05}/{len(losses)}")
    L.append(f"  reached +1R   before 0.5R MAE: winners {w1}/{len(wins)}, losers {l1}/{len(losses)}")
    for kb in (1, 2, 3):
        slow = [t for t in trades if not (isinstance(ex[id(t)]["bars_to_0_5R"], int) and ex[id(t)]["bars_to_0_5R"] <= kb)]
        L.append(f"  failed to reach +0.5R within {kb} bar(s): {len(slow)}/{n}")
    # fast vs slow starter expectancy (fast = reached +0.5R within 2 bars)
    fast = [t for t in trades if isinstance(ex[id(t)]["bars_to_0_5R"], int) and ex[id(t)]["bars_to_0_5R"] <= 2]
    slow = [t for t in trades if t not in fast]
    def expc(g):
        return (f"n={len(g)} avgR={np.mean([t.r_multiple for t in g]):+.3f} net=${sum(t.net_pnl_usd for t in g):+.1f} "
                f"win%={sum(1 for t in g if t.net_pnl_usd>0)/len(g)*100:.0f}") if g else "n=0"
    L.append(f"  FAST starters (+0.5R within 2 bars): {expc(fast)}")
    L.append(f"  SLOW starters: {expc(slow)}")
    L.append("  *** This is hypothesis-generating only. Do NOT convert into a trading rule yet. ***")

    L.append("\n  VERDICT: DIAGNOSTIC ONLY. ETH 6H breakout is the primary research lead but")
    L.append("  remains NOT DEPLOYABLE (sample and winner-count gates fail; distribution")
    L.append("  concentration high). No new filter is recommended.")
    return "\n".join(L), {"months": months, "walkforward": wf_rows, "mc_dds": dds}


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    p.add_argument("--mc", type=int, default=1000)
    args = p.parse_args()

    trades, eq, eth_indexed = _eth_trades(args.days)
    os.makedirs(OUT_DIR, exist_ok=True)
    bt.export_trades_csv(trades, os.path.join(OUT_DIR, "trades.csv"))
    report, extra = render(trades, eq, eth_indexed, args.mc)
    print("\n" + report)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write(report + "\n")
    if extra:
        pd.DataFrame(sorted(extra["months"].items()), columns=["month", "net_pnl_usd"]).to_csv(
            os.path.join(OUT_DIR, "monthly.csv"), index=False)
        pd.DataFrame(extra["walkforward"]).to_csv(os.path.join(OUT_DIR, "walkforward.csv"), index=False)
        dds = extra["mc_dds"]
        with open(os.path.join(OUT_DIR, "montecarlo_summary.txt"), "w") as f:
            f.write(f"iters={len(dds)} median={np.median(dds):.2f} p75={np.percentile(dds,75):.2f} "
                    f"p90={np.percentile(dds,90):.2f} p95={np.percentile(dds,95):.2f} worst={dds.max():.2f}\n")
    print(f"\nWrote {OUT_DIR}/summary.txt")


if __name__ == "__main__":
    main()
