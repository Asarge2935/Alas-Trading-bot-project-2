"""
GENERIC OHLCV CSV VALIDATION HARNESS — optional path, no strategy change.

Loads a user-supplied OHLCV CSV for independent validation, validates it
strictly, and (optionally) resamples to a coarser timeframe. Importable by the
independent-data validation scripts; runnable directly to validate a file.

Expected CSV columns (header names, case-insensitive; order-independent):
    timestamp, open, high, low, close, volume
  - timestamp: ISO-8601 (e.g. 2023-01-02T06:00:00Z) OR epoch seconds OR epoch ms.
    Parsed timezone-safe to UTC. Naive timestamps are ASSUMED UTC.
  - open/high/low/close/volume: numeric.

Validation (malformed data is REJECTED clearly, never silently fixed):
  - required columns present; numeric OHLCV; finite values
  - high >= max(open,close) and low <= min(open,close); high >= low
  - strictly increasing, de-duplicated UTC timestamps (sorted)
  - NO forward-fill of missing candles (gaps are reported, NOT filled)

Resampling (open=first, high=max, low=min, close=last, volume=sum), labeled by
bar START, epoch-aligned: 6h / 8h / 12h / 1d.

Run:
    python3 ohlcv_csv_validation_harness.py --csv path/to/file.csv [--resample 6h|8h|12h|1d]
    (no --csv prints usage and exits 0)

Outputs (backtest_output_csv_validation/): validation_report.txt
"""

import argparse
import os
import sys
import numpy as np
import pandas as pd

OUT_DIR = "backtest_output_csv_validation"
RESAMPLE_MAP = {"6h": "6h", "8h": "8h", "12h": "12h", "1d": "1D"}
_CANON = {"timestamp": "time", "time": "time", "date": "time", "datetime": "time",
          "open": "open", "high": "high", "low": "low", "close": "close",
          "volume": "volume", "vol": "volume"}


class CSVValidationError(Exception):
    pass


def _parse_time(series):
    """Timezone-safe UTC parse: epoch s/ms or ISO; naive assumed UTC."""
    s = series
    if pd.api.types.is_numeric_dtype(s):
        v = s.astype("int64")
        unit = "ms" if v.abs().median() > 10_000_000_000 else "s"
        return pd.to_datetime(v, unit=unit, utc=True)
    t = pd.to_datetime(s, utc=True, errors="coerce")
    return t


def load_ohlcv_csv(path):
    """Return (df, errors). df has columns time(UTC), low, high, open, close,
    volume, sorted/deduped. Raises CSVValidationError on fatal problems."""
    errors = []
    if not os.path.exists(path):
        raise CSVValidationError(f"file not found: {path}")
    raw = pd.read_csv(path)
    cols = {c: _CANON.get(c.strip().lower()) for c in raw.columns}
    raw = raw.rename(columns={c: cols[c] for c in raw.columns if cols[c]})
    missing = [c for c in ("time", "open", "high", "low", "close", "volume") if c not in raw.columns]
    if missing:
        raise CSVValidationError(f"missing required column(s): {missing}; "
                                 f"need timestamp/open/high/low/close/volume")

    raw["time"] = _parse_time(raw["time"])
    if raw["time"].isna().any():
        bad = int(raw["time"].isna().sum())
        raise CSVValidationError(f"{bad} unparseable timestamps")
    for c in ("open", "high", "low", "close", "volume"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    df = raw[["time", "low", "high", "open", "close", "volume"]].copy()

    if df[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise CSVValidationError("non-numeric / NaN OHLCV values present")
    if not np.isfinite(df[["open", "high", "low", "close", "volume"]].to_numpy()).all():
        raise CSVValidationError("non-finite OHLCV values present")
    if (df["volume"] < 0).any() or (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise CSVValidationError("non-positive price or negative volume present")

    df = df.sort_values("time")
    dups = int(df["time"].duplicated().sum())
    if dups:
        errors.append(f"{dups} duplicate timestamps -> dropped (kept first)")
        df = df.drop_duplicates(subset="time", keep="first")
    df = df.reset_index(drop=True)

    hi_ok = (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9) & (df["high"] >= df["low"])
    lo_ok = (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9)
    bad_bars = int((~(hi_ok & lo_ok)).sum())
    if bad_bars:
        raise CSVValidationError(f"{bad_bars} bars violate OHLC sanity (high/low vs open/close)")

    # gap report (NOT filled)
    if len(df) > 2:
        deltas = df["time"].diff().dropna().dt.total_seconds()
        med = deltas.median()
        gaps = int((deltas > med * 1.5).sum())
        if gaps:
            errors.append(f"{gaps} gaps > 1.5x median spacing ({med:.0f}s) — NOT forward-filled")
    return df, errors


def resample_ohlcv(df, pandas_freq):
    """Aggregate to a coarser timeframe (bar labeled by START)."""
    d = df.sort_values("time").copy()
    bucket = d["time"].dt.floor(pandas_freq)
    bucket.name = "time"
    agg = (d.groupby(bucket).agg(open=("open", "first"), high=("high", "max"),
                                 low=("low", "min"), close=("close", "last"),
                                 volume=("volume", "sum")).reset_index())
    return agg[["time", "low", "high", "open", "close", "volume"]]


def _usage():
    print(__doc__)


def main():
    p = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[1])
    p.add_argument("--csv", default=None)
    p.add_argument("--resample", choices=list(RESAMPLE_MAP), default=None)
    args = p.parse_args()
    if not args.csv:
        _usage()
        print("\nNo --csv provided. Nothing to validate; exiting cleanly.")
        sys.exit(0)

    os.makedirs(OUT_DIR, exist_ok=True)
    lines = ["=" * 80, f"OHLCV CSV VALIDATION — {args.csv}", "=" * 80]
    try:
        df, errs = load_ohlcv_csv(args.csv)
    except CSVValidationError as e:
        lines.append(f"  REJECTED: {e}")
        report = "\n".join(lines)
        print(report)
        with open(os.path.join(OUT_DIR, "validation_report.txt"), "w") as f:
            f.write(report + "\n")
        sys.exit(1)

    lines.append(f"  OK: {len(df)} valid bars  {df['time'].min()} -> {df['time'].max()}")
    med = df["time"].diff().dropna().dt.total_seconds().median() if len(df) > 1 else float("nan")
    lines.append(f"  median spacing ~{med:.0f}s")
    for w in errs:
        lines.append(f"  WARNING: {w}")
    if args.resample:
        r = resample_ohlcv(df, RESAMPLE_MAP[args.resample])
        lines.append(f"  resampled to {args.resample}: {len(r)} bars "
                     f"({r['time'].min()} -> {r['time'].max()})")
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(OUT_DIR, "validation_report.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote {OUT_DIR}/validation_report.txt")


if __name__ == "__main__":
    main()
