"""
NORMALIZE EXTERNAL OHLCV — data tooling only, no strategy change.

Takes a raw OHLCV CSV from a common public source and writes a clean file in the
EXACT schema the validation harness expects:

    timestamp,open,high,low,close,volume

Sources (--source):
  - cryptodatadownload : CryptoDataDownload Binance CSVs. These often have a
                         one-line title row before the header (auto-skipped),
                         and columns like "Date"/"Unix"/"unix" + Open/High/Low/
                         Close + "Volume BTC"/"Volume USDT"/"Volume".
  - binance_vision     : Binance Data Vision spot kline CSVs (sometimes
                         headerless): open_time, open, high, low, close, volume,
                         close_time, quote_volume, ...
  - auto               : best-effort auto-detection (default).

Behavior:
  - timestamps parsed timezone-safe to UTC (epoch s/ms/us or ISO; naive=UTC)
  - sorted ascending; duplicate timestamps dropped (with a warning)
  - missing required OHLCV -> clear rejection (no silent forward-fill)
  - prints start/end/rows/inferred interval; writes <output>.summary.txt

Usage:
    python3 normalize_external_ohlcv.py --input raw.csv --output clean.csv \\
        --source cryptodatadownload --symbol BTCUSDT
"""

import argparse
import os
import sys
import numpy as np
import pandas as pd

# Candidate source column names -> canonical role.
_TIME_KEYS = ["timestamp", "time", "date", "datetime", "open_time", "unix", "open time"]
_O = ["open"]
_H = ["high"]
_L = ["low"]
_C = ["close"]
_V = ["volume", "volume btc", "volume eth", "volume usdt", "volume usd",
      "vol", "base volume", "volume_(crypto)", "volume crypto"]


class NormalizeError(Exception):
    pass


def _pick(cols_lower, candidates):
    # exact match first, then substring
    for cand in candidates:
        for c in cols_lower:
            if c == cand:
                return cols_lower[c]
    for cand in candidates:
        for c in cols_lower:
            if cand in c:
                return cols_lower[c]
    return None


def _read_raw(path, source):
    if not os.path.exists(path):
        raise NormalizeError(f"input file not found: {path}")
    # CryptoDataDownload prepends a title line (e.g. 'https://www.cryptodatadownload.com').
    skip = 0
    if source in ("cryptodatadownload", "auto"):
        with open(path, "r", errors="replace") as f:
            first = f.readline()
        low = first.lower()
        if ("cryptodatadownload" in low) or (("," not in first) and first.strip()):
            skip = 1
    try:
        df = pd.read_csv(path, skiprows=skip)
    except Exception as e:
        raise NormalizeError(f"could not read CSV: {e}")
    # Binance Vision spot klines may be headerless (first cell is a big int open_time).
    if source in ("binance_vision", "auto"):
        c0 = str(df.columns[0]).strip()
        looks_headerless = c0.isdigit() or c0.replace(".", "", 1).isdigit()
        if looks_headerless:
            df = pd.read_csv(path, header=None)
            names = ["open_time", "open", "high", "low", "close", "volume",
                     "close_time", "quote_volume", "trades", "taker_base",
                     "taker_quote", "ignore"]
            df = df.iloc[:, :len(names)]
            df.columns = names[:df.shape[1]]
    return df


def _parse_time(series):
    if pd.api.types.is_numeric_dtype(series):
        v = series.astype("int64")
        m = v.abs().median()
        unit = "us" if m > 1e15 else "ms" if m > 1e12 else "s"
        return pd.to_datetime(v, unit=unit, utc=True)
    return pd.to_datetime(series, utc=True, errors="coerce")


def normalize(input_path, source, symbol=None):
    df = _read_raw(input_path, source)
    cols_lower = {str(c).strip().lower(): c for c in df.columns}

    tcol = _pick(cols_lower, _TIME_KEYS)
    ocol, hcol, lcol, ccol = (_pick(cols_lower, x) for x in (_O, _H, _L, _C))
    vcol = _pick(cols_lower, _V)
    missing = [n for n, c in (("timestamp", tcol), ("open", ocol), ("high", hcol),
                              ("low", lcol), ("close", ccol), ("volume", vcol)) if c is None]
    if missing:
        raise NormalizeError(
            f"could not find column(s) for: {missing}. Detected columns: {list(df.columns)}. "
            f"Try a different --source, or rename columns to "
            f"timestamp/open/high/low/close/volume.")

    out = pd.DataFrame({
        "timestamp": _parse_time(df[tcol]),
        "open": pd.to_numeric(df[ocol], errors="coerce"),
        "high": pd.to_numeric(df[hcol], errors="coerce"),
        "low": pd.to_numeric(df[lcol], errors="coerce"),
        "close": pd.to_numeric(df[ccol], errors="coerce"),
        "volume": pd.to_numeric(df[vcol], errors="coerce"),
    })

    warnings = []
    if out["timestamp"].isna().any():
        raise NormalizeError(f"{int(out['timestamp'].isna().sum())} unparseable timestamps "
                             f"(column '{tcol}')")
    if out[["open", "high", "low", "close", "volume"]].isna().any().any():
        bad = int(out[["open", "high", "low", "close", "volume"]].isna().any(axis=1).sum())
        raise NormalizeError(f"{bad} rows have missing/non-numeric OHLCV — refusing to forward-fill")
    out = out.sort_values("timestamp")
    dups = int(out["timestamp"].duplicated().sum())
    if dups:
        warnings.append(f"{dups} duplicate timestamps dropped (kept first)")
        out = out.drop_duplicates(subset="timestamp", keep="first")
    out = out.reset_index(drop=True)
    if len(out) < 2:
        raise NormalizeError("fewer than 2 valid rows after cleaning")

    deltas = out["timestamp"].diff().dropna().dt.total_seconds()
    med = float(deltas.median())
    interval = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h",
                21600: "6h", 28800: "8h", 43200: "12h", 86400: "1d"}.get(int(med), f"~{med:.0f}s")
    gaps = int((deltas > med * 1.5).sum())
    if gaps:
        warnings.append(f"{gaps} gaps > 1.5x median spacing — NOT forward-filled")
    return out, dict(start=out["timestamp"].min(), end=out["timestamp"].max(),
                     rows=len(out), interval=interval, symbol=symbol, warnings=warnings)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--source", default="auto",
                   choices=["auto", "cryptodatadownload", "binance_vision"])
    p.add_argument("--symbol", default=None)
    args = p.parse_args()

    try:
        out, info = normalize(args.input, args.source, args.symbol)
    except NormalizeError as e:
        print(f"ERROR: {e}")
        print("Tip: check you unzipped the file, picked the right --source, and that it has "
              "OHLCV columns. See docs/DATA_DOWNLOAD_GUIDE.md.")
        sys.exit(1)

    # Write timestamps as ISO-8601 UTC (Z) — unambiguous for the harness.
    written = out.copy()
    written["timestamp"] = written["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    written.to_csv(args.output, index=False)

    lines = [f"normalized: {args.output}",
             f"source={args.source}  symbol={info['symbol']}",
             f"rows={info['rows']}  inferred_interval={info['interval']}",
             f"start={info['start']}  end={info['end']}"]
    for w in info["warnings"]:
        lines.append(f"WARNING: {w}")
    report = "\n".join(lines)
    print(report)
    with open(args.output + ".summary.txt", "w") as f:
        f.write(report + "\n")
    print(f"\nNext: python3 ohlcv_csv_validation_harness.py --csv {args.output} --resample 6h")


if __name__ == "__main__":
    main()
