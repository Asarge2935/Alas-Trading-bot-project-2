"""
ETH-long-only robustness report — ANALYSIS ONLY.

Reads the CSVs produced by a backtest run and reports robustness slices for
the ETH-long-only subset. It does NOT run the strategy, fetch data, or change
any rule — it only summarizes existing output.

Intended workflow (strict-regime ETH-long-only):
    python3 backtest.py --trade-assets ETH-USD --long-only --strict-regime
    python3 robustness_report.py

Inputs (default backtest_output/):
    trades.csv         — the trade list to analyze
    loss_autopsy.csv   — MFE_R / MAE_R context for the losers (optional)

Reports: full trade list, per-calendar-year metrics, leave-one-out
(best / worst / both), in-sample vs out-of-sample, ETH loss autopsy, and
whether expectancy survives removing the single best trade.
"""

import argparse
import os
import numpy as np
import pandas as pd

OOS_SPLIT_FRAC = 0.70   # first 70% of the time window = in-sample (matches §9 gate)


def _metrics(df):
    n = len(df)
    if n == 0:
        return dict(trades=0, wins=0, losses=0, net=0.0, pf=float("nan"),
                    avg_r=float("nan"), max_dd=0.0)
    wins = df[df["net_pnl_usd"] > 0]
    losses = df[df["net_pnl_usd"] <= 0]
    gw = wins["net_pnl_usd"].sum()
    gl = abs(losses["net_pnl_usd"].sum())
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    d = df.sort_values("exit_time")
    cum = d["net_pnl_usd"].cumsum()
    max_dd = float((cum.cummax() - cum).max()) if n else 0.0
    return dict(trades=n, wins=len(wins), losses=len(losses),
                net=float(df["net_pnl_usd"].sum()), pf=pf,
                avg_r=float(df["r_multiple"].mean()), max_dd=max_dd)


def _fmt(m):
    pf = "inf" if m["pf"] == float("inf") else ("n/a" if m["pf"] != m["pf"] else f"{m['pf']:.2f}")
    avg_r = "n/a" if m["avg_r"] != m["avg_r"] else f"{m['avg_r']:+.3f}"
    return (f"trades={m['trades']:<3} W/L={m['wins']}/{m['losses']:<3} "
            f"net=${m['net']:+.2f}  PF={pf:<5} avgR={avg_r}  maxDD=${m['max_dd']:.2f}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--dir", default="backtest_output", help="backtest output dir")
    p.add_argument("--symbol", default="ETH-USD")
    p.add_argument("--side", default="long")
    p.add_argument("--setup-type", default=None,
                   help="optional: filter to one setup_type (e.g. breakout, "
                        "pullback_continuation) if the column exists")
    p.add_argument("--label", default=None,
                   help="optional header label, e.g. 'STANDALONE breakout' vs "
                        "'COMBINED attribution: breakout'")
    args = p.parse_args()

    trades_path = os.path.join(args.dir, "trades.csv")
    if not os.path.exists(trades_path):
        print(f"ERROR: {trades_path} not found. Run the backtest first.")
        return
    df = pd.read_csv(trades_path)
    for col in ("entry_time", "exit_time"):
        df[col] = pd.to_datetime(df[col], utc=True)

    total = len(df)
    df = df[(df["symbol"] == args.symbol) & (df["side"] == args.side)]
    setup_label = "all setups"
    if args.setup_type and "setup_type" in df.columns:
        df = df[df["setup_type"] == args.setup_type]
        setup_label = f"setup_type={args.setup_type}"
    df = df.sort_values("entry_time").reset_index(drop=True)
    header = args.label if args.label else f"{args.symbol} {args.side}-only, {setup_label}"
    print("=" * 78)
    print(f"ETH ROBUSTNESS REPORT — {header} "
          f"({len(df)} of {total} trades in trades.csv)")
    print("=" * 78)
    if df.empty:
        print("No matching trades. Re-run with the ETH-long-only command shown in the header.")
        return

    # 1. Full trade list.
    print("\n1. FULL TRADE LIST")
    cols = ["entry_time", "exit_time", "exit_reason", "entry_price",
            "exit_price_final", "net_pnl_usd", "r_multiple", "bars_held"]
    show = df[cols].copy()
    show["entry_time"] = show["entry_time"].dt.strftime("%Y-%m-%d %H:%M")
    show["exit_time"] = show["exit_time"].dt.strftime("%Y-%m-%d %H:%M")
    print(show.to_string(index=False))
    print(f"\n  OVERALL: {_fmt(_metrics(df))}")

    # 2. By calendar year.
    print("\n2. METRICS BY CALENDAR YEAR")
    df["year"] = df["entry_time"].dt.year
    for y in sorted(df["year"].unique()):
        print(f"  {y}: {_fmt(_metrics(df[df['year'] == y]))}")

    # 3-5. Leave-one-out.
    best_i = df["net_pnl_usd"].idxmax()
    worst_i = df["net_pnl_usd"].idxmin()
    print("\n3-5. LEAVE-ONE-OUT")
    print(f"  best  trade: {df.loc[best_i,'entry_time'].date()} "
          f"net=${df.loc[best_i,'net_pnl_usd']:+.2f} R={df.loc[best_i,'r_multiple']:+.2f}")
    print(f"  worst trade: {df.loc[worst_i,'entry_time'].date()} "
          f"net=${df.loc[worst_i,'net_pnl_usd']:+.2f} R={df.loc[worst_i,'r_multiple']:+.2f}")
    print(f"  ALL                 : {_fmt(_metrics(df))}")
    print(f"  ex-BEST             : {_fmt(_metrics(df.drop(best_i)))}")
    print(f"  ex-WORST            : {_fmt(_metrics(df.drop(worst_i)))}")
    print(f"  ex-BEST & ex-WORST  : {_fmt(_metrics(df.drop([best_i, worst_i])))}")

    # 6. In-sample vs out-of-sample (time split).
    t0, t1 = df["entry_time"].min(), df["entry_time"].max()
    split = t0 + (t1 - t0) * OOS_SPLIT_FRAC
    is_df = df[df["entry_time"] <= split]
    oos_df = df[df["entry_time"] > split]
    print("\n6. IN-SAMPLE vs OUT-OF-SAMPLE (70/30 by time)")
    print(f"  split at {split.date()}")
    print(f"  IN-SAMPLE  : {_fmt(_metrics(is_df))}")
    print(f"  OUT-SAMPLE : {_fmt(_metrics(oos_df))}")

    # 7. ETH loss autopsy (from loss_autopsy.csv if present).
    print("\n7. ETH LOSS AUTOPSY")
    la_path = os.path.join(args.dir, "loss_autopsy.csv")
    if os.path.exists(la_path):
        la = pd.read_csv(la_path)
        la = la[(la["symbol"] == args.symbol) & (la["side"] == args.side)]
        if args.setup_type:
            if "setup_type" in la.columns:
                la = la[la["setup_type"] == args.setup_type]
            else:
                print("  WARNING: loss_autopsy.csv has no setup_type column — "
                      "cannot filter losers by setup; showing all.")
        if la.empty:
            print("  No matching ETH losers in loss_autopsy.csv.")
        else:
            print(la[["entry_time", "exit_reason", "r_multiple", "MFE_R", "MAE_R",
                      "bars_held"]].to_string(index=False))
            n = len(la)
            for thr in (0.5, 1.0):
                hit = int((la["MFE_R"] >= thr).sum())
                print(f"  reached +{thr}R MFE before failing: {hit}/{n} ({hit/n*100:.0f}%)")
    else:
        print(f"  {la_path} not found — skip (rerun backtest to regenerate).")

    # 8. Expectancy without the top trade.
    print("\n8. EXPECTANCY WITHOUT THE SINGLE BEST TRADE")
    m_ex = _metrics(df.drop(best_i))
    pos = (m_ex["net"] > 0 and m_ex["avg_r"] > 0)
    print(f"  ex-BEST net=${m_ex['net']:+.2f}  avgR={m_ex['avg_r']:+.3f}  "
          f"-> {'POSITIVE expectancy holds' if pos else 'NOT positive — edge depends on the top trade'}")
    if len(df) < 30:
        print(f"\n  NOTE: {len(df)} trades < 30 — below the §9 sample gate; treat all "
              f"of the above as indicative, not deployable.")


if __name__ == "__main__":
    main()
