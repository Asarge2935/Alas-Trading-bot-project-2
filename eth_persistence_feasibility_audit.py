"""
ETH PERSISTENCE FEASIBILITY AUDIT — diagnostic only. Tests whether the ETH-USD
long-only 6H strict-regime BREAKOUT behavior (momentum-acceptance /
"failure-to-separate") PERSISTS across available independent segments, and
whether enough natural occurrences exist — WITHOUT changing the strategy.

Does NOT: add entry filters, add exits, optimize parameters, weaken the setup to
manufacture trades, or change backtest.py. Uses the current ETH baseline only.
Descriptive / hypothesis-generating; recommends no filter and no deployment.

Run:
    python3 eth_persistence_feasibility_audit.py [--days N]

Outputs (backtest_output_eth_persistence/):
    trades.csv, segments.csv, summary.txt
"""

import argparse
import os
import numpy as np
import pandas as pd

import backtest as bt
from eth_quality_audit_v2 import _excursion_path, _btc_regime_by_day, _pf, _fpf

OUT_DIR = "backtest_output_eth_persistence"
SAMPLE_GATE = 30
WINNER_GATE = 12
SEG_MIN = 5          # below this, a segment is flagged LOW_SAMPLE (descriptive)


def build(days):
    data = {}
    for sym in bt.ASSETS:
        df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)
        data[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
    trades, eq = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                                 strict_regime=True, setup="breakout")
    indexed = {s: d.set_index("time") for s, d in data.items()}
    eth = indexed["ETH-USD"]
    btc_reg = _btc_regime_by_day(indexed["BTC-USD"])
    daily_close = {s: bt._daily_close(indexed[s]) for s in indexed}
    rs_by_day = bt.compute_rs(daily_close)

    rows = []
    for t in trades:
        ex = _excursion_path(t, eth)
        s = eth.loc[t.signal_candle_time] if t.signal_candle_time in eth.index else None
        day = t.signal_candle_time.normalize()
        rs_row = rs_by_day.loc[day] if day in rs_by_day.index else None
        atr_ratio = (s["atr_14"] / s["atr_regime_avg"]) if (s is not None and s["atr_regime_avg"]) else np.nan
        vol_state = "unknown" if atr_ratio != atr_ratio else ("low" if atr_ratio < 0.75 else "high" if atr_ratio > 1.25 else "normal")
        eth20 = rs_row.get("ETH-USD", np.nan) if rs_row is not None else np.nan
        btc20 = rs_row.get("BTC-USD", np.nan) if rs_row is not None else np.nan
        rs_diff = (eth20 - btc20) if (eth20 == eth20 and btc20 == btc20) else np.nan
        rs_bucket = "unknown" if rs_diff != rs_diff else ("ETH<BTC" if rs_diff < 0 else "ETH+0-10%" if rs_diff < 0.10 else "ETH>+10%")
        b0 = ex["bars_to_0_5R"]; ba = ex["bars_to_adv_0_5R"]; b1 = ex["bars_to_1R"]
        fast = isinstance(b0, int) and b0 <= 2
        dt = (t.exit_time or t.entry_time)
        rows.append({
            "entry_time": t.entry_time.isoformat(),
            "net_pnl_usd": round(t.net_pnl_usd, 4), "r_multiple": round(t.r_multiple, 3),
            "is_winner": t.net_pnl_usd > 0,
            "year": str(dt.year), "quarter": f"{dt.year}-Q{(dt.month-1)//3+1}",
            "month": dt.strftime("%Y-%m"),
            "btc_regime": btc_reg.get(day, "unknown"),
            "btc_strict_state": t.regime_state,
            "vol_state": vol_state, "ethbtc_rs_bucket": rs_bucket,
            "fast_starter": fast,
            "reached_0_5R_before_mae": isinstance(b0, int) and (ba == "" or b0 <= ba),
            "reached_1R_before_mae": isinstance(b1, int) and (ba == "" or b1 <= ba),
        })
    return trades, eq, rows


def _seg_line(grp):
    n = len(grp)
    w = sum(1 for r in grp if r["is_winner"])
    netv = sum(r["net_pnl_usd"] for r in grp)
    pf = _pf([type("T", (), {"net_pnl_usd": r["net_pnl_usd"]})() for r in grp])
    avgr = np.mean([r["r_multiple"] for r in grp])
    fast = [r for r in grp if r["fast_starter"]]
    slow = [r for r in grp if not r["fast_starter"]]
    def sub(g):
        return (f"n={len(g)} avgR={np.mean([r['r_multiple'] for r in g]):+.2f} "
                f"win%={sum(1 for r in g if r['is_winner'])/len(g)*100:.0f}") if g else "n=0"
    r05 = sum(1 for r in grp if r["reached_0_5R_before_mae"])
    r1 = sum(1 for r in grp if r["reached_1R_before_mae"])
    flag = "  LOW_SAMPLE" if n < SEG_MIN else ""
    return (f"n={n:<3} W/L={w}/{n-w:<3} win%={w/n*100:>3.0f} PF={_fpf(pf):<5} avgR={avgr:+.2f} "
            f"net=${netv:+.1f}{flag}\n"
            f"        fast[{sub(fast)}]  slow[{sub(slow)}]  "
            f"+0.5R<MAE {r05}/{n}  +1R<MAE {r1}/{n}")


def render(trades, eq, rows):
    L = []
    n = len(trades)
    L.append("=" * 90)
    L.append("ETH PERSISTENCE FEASIBILITY AUDIT — 6H strict-regime breakout (DIAGNOSTIC ONLY)")
    L.append("=" * 90)
    if n == 0:
        L.append("  No ETH breakout trades in this window.")
        return "\n".join(L), rows
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
    top_pct = best.net_pnl_usd / net * 100 if net > 0 else float("nan")
    top3_pct = sum(nets[:3]) / net * 100 if net > 0 else float("nan")
    best_month_pct = (max(months.values()) / net * 100) if (months and net > 0) else float("nan")

    warns = []
    if n < SAMPLE_GATE:
        warns.append(f"LOW_SAMPLE({n}<30)")
    if len(wins) < WINNER_GATE:
        warns.append(f"LOW_WINNER_COUNT({len(wins)}<12)")
    if (top_pct == top_pct and top_pct > 30) or (top3_pct == top3_pct and top3_pct > 80) or (best_month_pct == best_month_pct and best_month_pct > 45):
        warns.append("DISTRIBUTION_CONCENTRATION")
    warns.append("NOT_DEPLOYABLE")
    warns.append("HYPOTHESIS_ONLY")

    L.append("\n-- CORE BASELINE --")
    L.append(f"  trades={n}  winners={len(wins)}  losers={n-len(wins)}  win%={len(wins)/n*100:.1f}")
    L.append(f"  PF={_fpf(_pf(trades))}  avgR={np.mean([t.r_multiple for t in trades]):+.3f}  "
             f"net=${net:+.2f}  maxDD={max_dd:.2f}%")
    if ex_best:
        L.append(f"  ex-best PF={_fpf(_pf(ex_best))}  top trade %={top_pct:.1f}  top-3 %={top3_pct:.1f}  best month %={best_month_pct:.1f}")
    L.append(f"  sample gate (>=30): {'PASS' if n>=SAMPLE_GATE else 'FAIL'}   "
             f"winner gate (>=12-15): {'PASS' if len(wins)>=WINNER_GATE else 'FAIL'}")
    L.append(f"  WARNINGS: {', '.join(warns)}")

    # persistence by segment
    L.append("\n-- PERSISTENCE BY SEGMENT (does the behavior repeat? fast/slow + sep stats) --")
    for dim in ("year", "quarter", "btc_regime", "vol_state", "ethbtc_rs_bucket", "btc_strict_state", "month"):
        L.append(f"\n  [{dim}]")
        g = {}
        for r in rows:
            g.setdefault(r[dim], []).append(r)
        for k in sorted(g, key=str):
            L.append(f"    {str(k):<10} " + _seg_line(g[k]))
    if all(r["btc_strict_state"] == "risk_on" for r in rows):
        L.append("\n  (note: btc_strict_state is 'risk_on' for every trade by construction — the")
        L.append("   strict regime only enters in risk-on, so that split is degenerate.)")

    # occurrence-expansion feasibility
    L.append("\n-- OCCURRENCE-EXPANSION FEASIBILITY (no rule weakening) --")
    years = {}
    for t in trades:
        years.setdefault(t.entry_time.year, 0)
        years[t.entry_time.year] += 1
    span_years = (max(t.entry_time for t in trades) - min(t.entry_time for t in trades)).days / 365.25
    rate = n / span_years if span_years > 0 else float("nan")
    L.append(f"  total trades={n} over ~{span_years:.1f}y  =>  ~{rate:.1f} trades/year")
    L.append("  per-year counts: " + "  ".join(f"{y}:{c}" for y, c in sorted(years.items())))
    enough = n >= SAMPLE_GATE and len(wins) >= WINNER_GATE
    L.append(f"  Enough natural occurrences under current rules? {'YES' if enough else 'NO'} — "
             + ("sample/winner gates cleared." if enough else
                f"sample is too small ({n} trades, {len(wins)} winners). At ~{rate:.1f} "
                "trades/yr it would take multiple more years of history to reach the 30-trade "
                "gate. DO NOT weaken the setup to manufacture trades."))

    L.append("\n  VERDICT: DIAGNOSTIC ONLY / HYPOTHESIS_ONLY. The failure-to-separate behavior")
    L.append("  is the candidate signal, but per-segment samples are tiny and any apparent")
    L.append("  persistence is anecdotal. NOT DEPLOYABLE. No filter recommended; no rule change.")
    return "\n".join(L), rows


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    args = p.parse_args()

    trades, eq, rows = build(args.days)
    os.makedirs(OUT_DIR, exist_ok=True)
    bt.export_trades_csv(trades, os.path.join(OUT_DIR, "trades.csv"))
    if rows:
        pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, "segments.csv"), index=False)
    report, _ = render(trades, eq, rows)
    print("\n" + report)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote {OUT_DIR}/summary.txt")


if __name__ == "__main__":
    main()
