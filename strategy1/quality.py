"""Data quality report for cached daily candles.

Strategy 1's spec mandates a quality check before any backtest code
runs. This module produces a per-product report of:

- Coverage: first/last bar date, total bar count.
- Gaps: missing daily bars within the covered window.
- Outliers: single-day price moves above an absolute threshold (50%
  by default) — these are usually real (crypto does this) but should
  be eyeballed for exchange data errors.
- Stale prices: runs of identical closes ≥ 3 days, which are usually
  Coinbase fill-forward artifacts on illiquid pairs and disqualify
  the symbol from the universe.

Usage:
    python -m strategy1.quality --in data_cache/

Returns nonzero exit code if any product fails a hard check (gaps
above the threshold or stale-price runs found), so this can gate CI.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from strategy1.universe import load_all


# Hard thresholds. Above these, the product is rejected from the universe.
MAX_GAP_RATIO = 0.02          # >2% missing days → reject
MAX_STALE_RUN_DAYS = 3        # ≥3 identical closes in a row → reject
OUTLIER_DAILY_RETURN = 0.50   # |daily return| ≥ 50% is flagged (warn, not reject)


@dataclass
class ProductReport:
    product_id: str
    first_bar: pd.Timestamp | None
    last_bar: pd.Timestamp | None
    n_bars: int
    expected_bars: int
    n_gaps: int
    gap_ratio: float
    longest_stale_run: int
    n_outliers: int
    passed: bool
    failures: list[str]


def _expected_bar_count(first: pd.Timestamp, last: pd.Timestamp) -> int:
    return int((last.normalize() - first.normalize()).days) + 1


def _gap_count(df: pd.DataFrame) -> int:
    if len(df) < 2:
        return 0
    expected = _expected_bar_count(df.index.min(), df.index.max())
    return max(0, expected - len(df))


def _longest_stale_run(closes: pd.Series) -> int:
    """Length of the longest run of identical consecutive closes."""
    if len(closes) < 2:
        return 0
    same_as_prev = closes.eq(closes.shift())
    if not same_as_prev.any():
        return 1
    # Group consecutive Trues; longest True-run + 1 = run length of identical closes.
    longest = 0
    current = 0
    for v in same_as_prev:
        if v:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest + 1 if longest > 0 else 1


def _outlier_count(closes: pd.Series, threshold: float) -> int:
    if len(closes) < 2:
        return 0
    rets = closes.pct_change().abs()
    return int((rets >= threshold).sum())


def report_product(product_id: str, df: pd.DataFrame) -> ProductReport:
    failures: list[str] = []
    if df.empty:
        return ProductReport(product_id, None, None, 0, 0, 0, 0.0, 0, 0, False,
                             ["empty dataframe"])

    first = df.index.min()
    last = df.index.max()
    expected = _expected_bar_count(first, last)
    n_bars = len(df)
    n_gaps = _gap_count(df)
    gap_ratio = n_gaps / expected if expected else 0.0
    longest_stale = _longest_stale_run(df["close"])
    n_outliers = _outlier_count(df["close"], OUTLIER_DAILY_RETURN)

    if gap_ratio > MAX_GAP_RATIO:
        failures.append(f"gap_ratio={gap_ratio:.3f} > {MAX_GAP_RATIO}")
    if longest_stale >= MAX_STALE_RUN_DAYS:
        failures.append(f"longest_stale_run={longest_stale} ≥ {MAX_STALE_RUN_DAYS}")

    return ProductReport(
        product_id=product_id,
        first_bar=first,
        last_bar=last,
        n_bars=n_bars,
        expected_bars=expected,
        n_gaps=n_gaps,
        gap_ratio=gap_ratio,
        longest_stale_run=longest_stale,
        n_outliers=n_outliers,
        passed=not failures,
        failures=failures,
    )


def report_all(in_dir: Path) -> list[ProductReport]:
    all_data = load_all(in_dir)
    return [report_product(pid, df) for pid, df in sorted(all_data.items())]


def format_table(reports: list[ProductReport]) -> str:
    if not reports:
        return "(no products in cache)"
    header = f"{'product':<12} {'first':<11} {'last':<11} {'bars':>5} {'gaps':>5} {'gap%':>6} {'stale':>5} {'out':>4}  status"
    lines = [header, "-" * len(header)]
    for r in reports:
        first = r.first_bar.date().isoformat() if r.first_bar is not None else "-"
        last = r.last_bar.date().isoformat() if r.last_bar is not None else "-"
        status = "OK" if r.passed else "FAIL: " + "; ".join(r.failures)
        lines.append(
            f"{r.product_id:<12} {first:<11} {last:<11} {r.n_bars:>5d} {r.n_gaps:>5d} "
            f"{r.gap_ratio*100:>5.2f}% {r.longest_stale_run:>5d} {r.n_outliers:>4d}  {status}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--in", dest="in_dir", type=Path, default=Path("data_cache"),
                   help="Input directory containing per-product CSVs (default: data_cache/)")
    args = p.parse_args(argv)

    if not args.in_dir.exists():
        print(f"ERROR: {args.in_dir} does not exist. Run `python -m strategy1.data` first.",
              file=sys.stderr)
        return 2

    reports = report_all(args.in_dir)
    print(format_table(reports))

    n_total = len(reports)
    n_pass = sum(1 for r in reports if r.passed)
    n_fail = n_total - n_pass
    print(f"\n{n_pass}/{n_total} products passed quality gates ({n_fail} failed)")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
