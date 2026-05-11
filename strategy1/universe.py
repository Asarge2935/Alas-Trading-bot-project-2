"""Point-in-time universe construction for Strategy 1.

At each rebalance date, we need the top-N spot pairs by trailing
dollar volume *as of that date* — not as of today. Building the
universe with today's top-N would inject survivorship bias and is
the single biggest source of fake edge in this kind of strategy.

This module exposes:
- load_all(in_dir): load every cached CSV into a {product_id: DataFrame}.
- top_n_at(date, all_data, n, lookback_days, min_history_days):
    return the products eligible at `date` ranked by trailing dollar
    volume, top-N. Filters out products whose listing is too recent.
"""

from __future__ import annotations

from datetime import date as Date
from pathlib import Path

import pandas as pd


DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_MIN_HISTORY_DAYS = 120


def load_all(in_dir: Path) -> dict[str, pd.DataFrame]:
    """Load every <product>.csv under `in_dir` into a dict.

    Each frame is indexed by tz-aware UTC daily timestamps and has
    columns open/high/low/close/volume/dollar_volume.
    """
    out: dict[str, pd.DataFrame] = {}
    for path in sorted(Path(in_dir).glob("*.csv")):
        df = pd.read_csv(path, parse_dates=["time"], index_col="time")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        out[path.stem] = df
    return out


def _as_utc_ts(d: Date | pd.Timestamp | str) -> pd.Timestamp:
    ts = pd.Timestamp(d)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    return ts.normalize()


def top_n_at(
    as_of: Date | pd.Timestamp | str,
    all_data: dict[str, pd.DataFrame],
    n: int = 5,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
) -> list[tuple[str, float]]:
    """Return up to N (product_id, trailing_dollar_volume) pairs.

    Eligibility rules (point-in-time, no look-ahead):
    - Product must have at least `min_history_days` daily bars on or
      before `as_of`. This filters newly-listed pump windows.
    - Trailing dollar volume is the mean over the last
      `lookback_days` bars ending at `as_of` (inclusive).
    - Products without `lookback_days` of history yet are excluded.
    - Products with no bar dated `as_of` (e.g. delisted, or `as_of`
      is before the data starts) are excluded.

    Result is sorted descending by dollar volume; ties broken by
    product_id for determinism.
    """
    cutoff = _as_utc_ts(as_of)
    scored: list[tuple[str, float]] = []

    for pid, df in all_data.items():
        history = df.loc[df.index <= cutoff]
        if len(history) < min_history_days:
            continue
        if len(history) < lookback_days:
            continue
        if history.index.max() != cutoff:
            # No bar on the as-of date — likely a missing day or the
            # product wasn't yet listed. Either way, ineligible to
            # avoid stale-price inclusion.
            continue
        window = history.iloc[-lookback_days:]
        score = float(window["dollar_volume"].mean())
        if pd.isna(score) or score <= 0:
            continue
        scored.append((pid, score))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:n]


def universe_at(
    as_of: Date | pd.Timestamp | str,
    all_data: dict[str, pd.DataFrame],
    n: int = 5,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
) -> list[str]:
    """Convenience wrapper: just the product IDs from `top_n_at`."""
    return [pid for pid, _ in top_n_at(as_of, all_data, n, lookback_days, min_history_days)]
