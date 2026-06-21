"""
BTC FULL-COMPRESSION DIAGNOSTIC — tests one structural variant of the BTC 12H
compression breakout: require FULL compression (comp_count == 3) vs the existing
baseline (comp_count >= 2). Diagnostic only; no ETH/live/paper changes, no
threshold optimization.

Motivation: the compression quality audit (LOW_SAMPLE) hinted comp_count == 3
won more often than comp_count == 2 — but on a tiny sample. This isolates full
compression as a standalone variant by re-running the EXACT existing loop with
one extra entry condition (comp_count_prev == 3). Nothing else changes:
breakout trigger, volume threshold, exits, stops, fees, slippage, funding, and
risk model are all reused unchanged from btc_compression_diagnostic.py.

Run:
    python3 btc_full_compression_diagnostic.py [--days N]

Outputs (backtest_output_btc_full_compression/):
    trades_{baseline,full}.csv + equity/loss_autopsy/regime_behavior
    + console baseline-vs-full comparison.
"""

import argparse
import os

import backtest as bt
import btc_compression_diagnostic as bc
from btc_htf_alignment_diagnostic import _metrics, _f, _verdict

OUT_DIR = "backtest_output_btc_full_compression"
TF_OVERRIDES = dict(bc.TF_CONFIGS[0][4])   # 12H overrides
ENTRY_SECONDS, FETCH_GRAN, AGG = 43200, 21600, "12h"

VARIANTS = [
    ("baseline", None),                                    # comp_count >= 2 (existing gate)
    ("full", lambda r: r["comp_count_prev"] == 3),         # require all 3 compression conditions
]


def print_comparison(m_by_v):
    labels = list(m_by_v)
    print("\n" + "=" * 64)
    print("BTC 12H compression breakout — baseline vs full (comp_count==3)")
    print("=" * 64)
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
    print(f"{'Metric':<22}" + "".join(f"{lab:>16}" for lab in labels))
    print("-" * 64)
    for label, fn in rows:
        print(f"{label:<22}" + "".join(f"{fn(m_by_v[lab]):>16}" for lab in labels))
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
    print("\nNOTE: diagnostic only — not for deployment, not paper/live, no parameter tuning. "
          "Full-compression sample is expected to be very small; treat any gain as indicative.")


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
