"""Point-in-time universe construction and RS ranking for Strategy 1.

Two distinct rankings live here:

- `top_n_at`: ranks by **trailing dollar volume**. Used to select the
  eligible universe — i.e., which assets are liquid enough to consider
  at all. Enforces minimum history and a present-on-date bar.

- `rank_by_return`: ranks by **trailing return**. Used to pick the
  long basket *from* the eligible universe at each rebalance. This is
  the relative-strength signal from spec §4.

Splitting eligibility (volume) from selection (return) is what stops
the strategy from picking a freshly-listed pumper that happens to be
up 400% on the week. The volume filter says "we'd actually trade
this"; the return filter says "among those, this is strong."

At each rebalance date, we need the rankings *as of that date* — not
as of today. Building either ranking with today's data injects
survivorship/look-ahead bias and is the single biggest source of
fake edge in this kind of strategy.
"""

from __future__ import annotations

from datetime import date as Date
from pathlib import Path

import pandas as pd


DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_MIN_HISTORY_DAYS = 120
DEFAULT_RS_LOOKBACK_DAYS = 30


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


def rank_by_return(
    as_of: Date | pd.Timestamp | str,
    all_data: dict[str, pd.DataFrame],
    eligible: list[str],
    n: int,
    lookback_days: int = DEFAULT_RS_LOOKBACK_DAYS,
) -> list[tuple[str, float]]:
    """Rank `eligible` products by trailing `lookback_days` return as of `as_of`.

    Return is computed close-to-close: close[as_of] / close[as_of - L] - 1.
    Products lacking enough history on `as_of` are silently excluded.
    Result is sorted descending by return; top N returned.

    This is the relative-strength selection signal. It runs *after*
    `top_n_at` has produced the eligible pool, so it cannot pick an
    illiquid or freshly-listed asset.
    """
    cutoff = _as_utc_ts(as_of)
    scored: list[tuple[str, float]] = []

    for pid in eligible:
        df = all_data.get(pid)
        if df is None:
            continue
        history = df.loc[df.index <= cutoff]
        if len(history) <= lookback_days:
            continue
        if history.index.max() != cutoff:
            continue
        end_px = float(history["close"].iloc[-1])
        start_px = float(history["close"].iloc[-(lookback_days + 1)])
        if start_px <= 0:
            continue
        ret = end_px / start_px - 1.0
        scored.append((pid, ret))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:n]
