"""Multi-asset directional backtester (BTC / ETH / SOL).

Runs the same long/short trend strategy as strategy1.btc_backtest, but
independently on each of several assets, and also reports an
equal-weight portfolio that runs all sleeves at once.

Structure (user's choice): "independent directional, each asset" —
each asset is long / short / flat on its OWN trend signal. There is no
cross-asset rotation and no BTC-regime gating in this v0 (those are
separate hypotheses to test later).

Reuses the validated single-asset engine from btc_backtest:
run_variant, evaluate, gate_report, CostModel, etc.

Usage:
    python -m strategy1.multi_backtest --in data_cache/
    python -m strategy1.multi_backtest --in data_cache/ --fee-preset perp_maker
    python -m strategy1.multi_backtest --in data_cache/ --vol-aware
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from strategy1.regime import directional_signal
from strategy1.universe import load_all
from strategy1.btc_backtest import (
    CostModel, FEE_PRESETS, MODE_MAP, DEFAULT_SLIPPAGE, DEFAULT_FUNDING_DAILY,
    VariantResult, run_variant, evaluate, gate_report, buy_and_hold, _sharpe,
    _max_drawdown,
)

DEFAULT_PRODUCTS = ["BTC-USD", "ETH-USD", "SOL-USD"]


def combine_portfolio(results: dict[str, VariantResult],
                      weights: dict[str, float]) -> VariantResult:
    """Equal-weight (or given-weight) static-allocation portfolio.

    Each sleeve runs independently with its own capital share. The
    portfolio equity is the weighted sum of per-sleeve equity curves,
    each normalized to 1.0 at the common start date so the portfolio
    also starts at 1.0. Trades from all sleeves are pooled (weights are
    equal, so profit-factor and concentration ratios are unaffected by
    the per-sleeve scaling).
    """
    common = None
    for r in results.values():
        common = r.equity.index if common is None else common.intersection(r.equity.index)
    common = common.sort_values()

    port = None
    for asset, r in results.items():
        eq = r.equity.reindex(common).ffill()
        eq = eq / eq.iloc[0]                       # normalize sleeve to 1.0 at common start
        contrib = weights[asset] * eq
        port = contrib if port is None else port + contrib

    pooled_trades = []
    for r in results.values():
        pooled_trades.extend(t for t in r.trades if t.exit_date >= common[0])

    return VariantResult(mode="portfolio", equity=port, trades=pooled_trades, halted_date=None)


def run_multi(all_data: dict, products: list[str], costs: CostModel,
              vol_aware: bool) -> dict:
    """Run all variants on each product; return per-asset and portfolio results."""
    out: dict = {"per_asset": {}, "portfolio": {}, "signals": {}}
    available = [p for p in products if p in all_data]
    for p in available:
        df = all_data[p].sort_index()
        sig = directional_signal(df["close"], vol_aware=vol_aware)
        out["signals"][p] = sig
        out["per_asset"][p] = {m: run_variant(df, sig, m, costs) for m in MODE_MAP}

    weights = {p: 1.0 / len(available) for p in available}
    for mode in MODE_MAP:
        per_mode = {p: out["per_asset"][p][mode] for p in available}
        out["portfolio"][mode] = combine_portfolio(per_mode, weights)
    out["available"] = available
    out["weights"] = weights
    return out


def _gate_block(metrics, hodl_sharpe, indent="    ") -> tuple[list[str], bool]:
    lines, all_pass = [], True
    for name, passed, detail in gate_report(metrics, hodl_sharpe):
        mark = "PASS" if passed else "FAIL"
        all_pass = all_pass and passed
        lines.append(f"{indent}[{mark}] {name:<24} {detail}")
    return lines, all_pass


def format_multi_report(res: dict, all_data: dict, costs: CostModel) -> str:
    available = res["available"]
    lines: list[str] = []
    lines.append("=" * 74)
    lines.append("Multi-Asset Directional Backtest — BTC / ETH / SOL (independent)")
    lines.append(f"Costs: {costs.per_side*100:.2f}%/side  |  short funding {costs.funding_daily*100:.2f}%/day")
    lines.append("=" * 74)

    # Per-asset, per-variant.
    for p in available:
        df = all_data[p].sort_index()
        hodl = buy_and_hold(df)
        lines.append("")
        lines.append("#" * 74)
        lines.append(f"# {p}   (HODL: {(hodl.iloc[-1]-1)*100:+.0f}%  Sharpe {_sharpe(hodl):.2f}  "
                     f"maxDD {_max_drawdown(hodl)*100:.0f}%)")
        lines.append("#" * 74)
        for mode in ("long_flat", "short_flat", "long_short"):
            m = evaluate(res["per_asset"][p][mode], df)
            pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
            lines.append(f"  [{mode}] ret={m.total_return*100:+.0f}% Sharpe={m.sharpe:.2f} "
                         f"maxDD={m.max_dd*100:.0f}% PF={pf} trades={m.n_trades}")
            gl, ok = _gate_block(m, _sharpe(hodl), indent="      ")
            lines.extend(gl)
            lines.append(f"      VERDICT: {'PROMOTE' if ok else 'REJECTED'}")

    # Portfolio (equal-weight across the three sleeves).
    lines.append("")
    lines.append("=" * 74)
    lines.append(f"PORTFOLIO — equal weight {', '.join(f'{p} {w*100:.0f}%' for p,w in res['weights'].items())}")
    lines.append("=" * 74)
    # Benchmark: equal-weight HODL of the three.
    common = res["portfolio"]["long_short"].equity.index
    eqw_hodl = None
    for p in available:
        h = buy_and_hold(all_data[p].sort_index()).reindex(common).ffill()
        h = h / h.iloc[0]
        eqw_hodl = h * res["weights"][p] if eqw_hodl is None else eqw_hodl + h * res["weights"][p]
    lines.append(f"Benchmark equal-weight HODL: ret={(eqw_hodl.iloc[-1]-1)*100:+.0f}%  "
                 f"Sharpe={_sharpe(eqw_hodl):.2f}  maxDD={_max_drawdown(eqw_hodl)*100:.0f}%")
    for mode in ("long_flat", "short_flat", "long_short"):
        m = evaluate(res["portfolio"][mode], all_data[available[0]])
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        lines.append("")
        lines.append(f"  [{mode}] ret={m.total_return*100:+.0f}% Sharpe={m.sharpe:.2f} "
                     f"maxDD={m.max_dd*100:.0f}% PF={pf} trades={m.n_trades}")
        gl, ok = _gate_block(m, _sharpe(eqw_hodl), indent="    ")
        lines.extend(gl)
        lines.append(f"    VERDICT: {'PROMOTE to paper' if ok else 'REJECTED'}")

    lines.append("")
    lines.append("-" * 74)
    lines.append("long_flat is spot-executable on all three. short_flat / long_short")
    lines.append("need Coinbase perps (BTC/ETH/SOL nano perps exist; sizing for a $500")
    lines.append("account split 3 ways needs care — see VENUE_AUDIT_PERP_US.md).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--in", dest="in_dir", type=Path, default=Path("data_cache"))
    p.add_argument("--products", nargs="*", default=DEFAULT_PRODUCTS)
    p.add_argument("--vol-aware", action="store_true")
    p.add_argument("--fee-preset", choices=sorted(FEE_PRESETS), default="perp_taker")
    p.add_argument("--taker-fee", type=float, default=None)
    p.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE)
    p.add_argument("--funding-daily", type=float, default=DEFAULT_FUNDING_DAILY)
    args = p.parse_args(argv)

    all_data = load_all(args.in_dir)
    missing = [p for p in args.products if p not in all_data]
    if missing:
        print(f"WARNING: not in cache, skipping: {missing}", file=sys.stderr)
    available = [p for p in args.products if p in all_data]
    if not available:
        print(f"ERROR: none of {args.products} found in {args.in_dir}", file=sys.stderr)
        return 2

    taker = args.taker_fee if args.taker_fee is not None else FEE_PRESETS[args.fee_preset]
    costs = CostModel(taker_fee=taker, slippage=args.slippage, funding_daily=args.funding_daily)

    res = run_multi(all_data, available, costs, args.vol_aware)
    print(format_multi_report(res, all_data, costs))
    sig_note = "B-dir vol-aware" if args.vol_aware else "A-dir trend-only"
    print(f"\n(signal: {sig_note}  |  fee/side: {costs.per_side*100:.2f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
