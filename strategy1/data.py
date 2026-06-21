"""Daily candle fetcher for the Strategy 1 candidate universe.

Source: Coinbase Exchange public REST (`api.exchange.coinbase.com`).
- US-eligible, no API key required for public market data.
- Granularity 86400 (1 day).
- Returns up to 300 candles per request → paginated.

Output: one CSV per product under <out_dir>/<product_id>.csv with
columns: time (UTC ISO), open, high, low, close, volume, dollar_volume.

Usage:
    python -m strategy1.data --days 1500 --out data_cache/
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests


COINBASE_BASE = "https://api.exchange.coinbase.com"
USER_AGENT = "AlasStrategy1/0.1"
DAY_SECONDS = 86_400
MAX_CANDLES_PER_REQUEST = 300

# Granularities the Coinbase Exchange candles endpoint serves NATIVELY.
# These six are the only values the API accepts; any other value is
# rejected. The 30m / 2h / 4h / 1w intervals shown in the Coinbase
# trading UI are aggregated client-side — derive them with
# resample_ohlcv() from a native base (see RESAMPLE_RULES).
NATIVE_GRANULARITIES = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}

# Non-native intervals from the UI: (pandas resample rule, recommended
# native base to aggregate from). Built by resample_ohlcv(), not fetched.
RESAMPLE_RULES = {
    "30m": ("30min", "15m"),
    "2h":  ("2h",    "1h"),
    "4h":  ("4h",    "1h"),
    "1w":  ("1W",    "1d"),
}

# Candidate universe for Strategy 1 RS ranking.
# Selection criterion: liquid USD spot pairs on Coinbase that have
# existed long enough to provide a backtest sample. This is the *pool*
# from which the point-in-time top-N is drawn at each rebalance — see
# strategy1/universe.py. Listing-date filtering happens there, not here.
#
# Known rebrands and delistings (kept here, not silently dropped, so the
# point-in-time universe still uses each ticker for the period it was
# active):
#   MATIC-USD: Coinbase data ends ~2025-10. Migrated to POL-USD.
#   RNDR-USD:  Not listed (404). Migrated to RENDER-USD.
#   MKR-USD:   Coinbase data ends ~2026-01. Status to confirm.
# The successor tickers are included alongside the originals.
#
# Coinbase Exchange's public candles endpoint serves at most ~4 years of
# history per product. That is the upper bound on the backtest window
# from this data source. See strategy1/README.md for the implication.
CANDIDATE_PRODUCTS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD",
    "AVAX-USD", "DOT-USD", "LINK-USD", "LTC-USD", "BCH-USD",
    "ATOM-USD", "UNI-USD", "AAVE-USD", "COMP-USD", "ALGO-USD",
    "FIL-USD", "NEAR-USD", "APT-USD", "ARB-USD", "OP-USD",
    "INJ-USD", "SUI-USD", "TIA-USD", "SEI-USD", "FET-USD",
    "GRT-USD", "IMX-USD",
    # Predecessors kept for historical periods; will fail freshness
    # check today, which is the correct behavior:
    "MATIC-USD", "MKR-USD",
    # Successors of rebranded tickers:
    "POL-USD", "RENDER-USD",
]


def _safe_get(url: str, params: dict, max_retries: int = 5) -> requests.Response:
    """GET with exponential backoff on 429s. Raises on other HTTP errors."""
    headers = {"User-Agent": USER_AGENT}
    last = None
    for attempt in range(max_retries):
        r = requests.get(url, params=params, headers=headers, timeout=20)
        last = r
        if r.status_code == 429:
            time.sleep(0.5 * (2 ** attempt))
            continue
        r.raise_for_status()
        return r
    last.raise_for_status()
    return last


def fetch_candles(product_id: str, granularity_seconds: int, days_back: int) -> pd.DataFrame:
    """Pull `days_back` days of OHLCV at a NATIVE granularity for a product.

    `granularity_seconds` must be one of NATIVE_GRANULARITIES' values
    (60/300/900/3600/21600/86400); other values are rejected by the API.
    For 30m/2h/4h/1w, fetch a native base and call resample_ohlcv().

    Returns a tz-aware UTC DatetimeIndex frame with columns
    open/high/low/close/volume/dollar_volume. The still-forming current
    bar is dropped. Returns an empty frame if the product is unknown.

    Note: for sub-hourly granularities over long windows this makes many
    paginated requests (300 candles each). Keep `days_back` small for
    1m/5m/15m pulls.
    """
    if granularity_seconds not in NATIVE_GRANULARITIES.values():
        raise ValueError(
            f"granularity {granularity_seconds}s is not natively served by Coinbase; "
            f"valid: {sorted(NATIVE_GRANULARITIES.values())}. "
            f"For 30m/2h/4h/1w fetch a native base and use resample_ohlcv()."
        )
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    rows: list[list] = []
    cursor = end

    while cursor > start:
        chunk_start = max(start, cursor - timedelta(seconds=granularity_seconds * MAX_CANDLES_PER_REQUEST))
        params = {
            "start": chunk_start.isoformat(),
            "end": cursor.isoformat(),
            "granularity": granularity_seconds,
        }
        url = f"{COINBASE_BASE}/products/{product_id}/candles"
        try:
            r = _safe_get(url, params)
        except requests.HTTPError as e:
            # 404 = product not listed on Coinbase. Surface and stop.
            if e.response is not None and e.response.status_code == 404:
                return pd.DataFrame()
            raise
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        cursor = chunk_start
        time.sleep(0.3)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df["dollar_volume"] = df["close"] * df["volume"]

    # Drop the still-forming current bar: a bar is closed once
    # (bar_start + granularity) <= now.
    now = pd.Timestamp.now(tz="UTC")
    closed = df.index + pd.Timedelta(seconds=granularity_seconds) <= now
    return df[closed]


def fetch_daily_candles(product_id: str, days_back: int) -> pd.DataFrame:
    """Backward-compatible daily wrapper around fetch_candles()."""
    return fetch_candles(product_id, DAY_SECONDS, days_back)


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate native OHLCV bars to a coarser interval (e.g. '4h', '1W').

    Used to build the UI-only intervals (30m/2h/4h/1w) that the Coinbase
    API does not serve directly. `rule` is a pandas offset alias; pass
    bars finer than the target (see RESAMPLE_RULES for the recommended
    native base per target).
    """
    agg = {
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }
    out = df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open"])
    out["dollar_volume"] = out["close"] * out["volume"]
    return out


def save_candles(df: pd.DataFrame, product_id: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{product_id}.csv"
    df.to_csv(path, index_label="time")
    return path


def pull_all(products: list[str], days_back: int, out_dir: Path) -> dict[str, int]:
    """Fetch and save daily candles for each product. Returns row counts."""
    results: dict[str, int] = {}
    for i, pid in enumerate(products, 1):
        print(f"[{i:>2}/{len(products)}] {pid} ... ", end="", flush=True)
        try:
            df = fetch_daily_candles(pid, days_back)
        except Exception as e:
            print(f"ERROR ({type(e).__name__}: {e})")
            results[pid] = -1
            continue
        if df.empty:
            print("not listed on Coinbase, skipped")
            results[pid] = 0
            continue
        save_candles(df, pid, out_dir)
        results[pid] = len(df)
        print(f"{len(df)} rows ({df.index.min().date()} → {df.index.max().date()})")
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--days", type=int, default=1500,
                   help="Days of history to request (default: 1500 ≈ 4y)")
    p.add_argument("--out", type=Path, default=Path("data_cache"),
                   help="Output directory (default: data_cache/)")
    p.add_argument("--products", nargs="*", default=None,
                   help="Override the candidate product list")
    args = p.parse_args(argv)

    products = args.products or CANDIDATE_PRODUCTS
    print(f"Fetching {len(products)} products, {args.days} days, → {args.out}/")
    results = pull_all(products, args.days, args.out)

    ok = sum(1 for v in results.values() if v > 0)
    skipped = sum(1 for v in results.values() if v == 0)
    failed = sum(1 for v in results.values() if v < 0)
    print(f"\nSummary: {ok} fetched, {skipped} not listed, {failed} errored")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
