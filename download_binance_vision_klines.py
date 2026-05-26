"""
DOWNLOAD BINANCE VISION KLINES — data tooling only, no strategy change.

Downloads Binance Data Vision SPOT monthly kline ZIPs for a symbol/interval,
combines them, and writes a normalized CSV (timestamp,open,high,low,close,
volume) ready for the validation harness. Network is required at run time; this
file only orchestrates downloads — it does not touch strategy logic.

Usage:
    python3 download_binance_vision_klines.py --symbol BTCUSDT --interval 1h \\
        --start 2020-01 --end 2026-05 --market spot --output ~/btc.csv

Notes:
  - market 'spot' is supported. Futures (USD-M) is left as a TODO (Layer 2);
    pass --market futures to see the documented stub (it will exit, not guess).
  - Raw ZIPs are cached under data_external/raw/binance_vision/<market>/<symbol>/<interval>/.
  - Missing monthly files fail CLEARLY (Binance Vision sometimes lacks the
    current partial month, or very early months for a symbol).
  - Prefer 1h: the harness resamples to 6h/8h/12h/1d.

Binance Vision URL pattern (spot monthly):
  https://data.binance.vision/data/spot/monthly/klines/<SYM>/<INT>/<SYM>-<INT>-<YYYY-MM>.zip
"""

import argparse
import io
import os
import sys
import zipfile
import urllib.request
import urllib.error
import pandas as pd

RAW_DIR = os.path.join("data_external", "raw", "binance_vision")
BASE = "https://data.binance.vision/data"
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume",
              "close_time", "quote_volume", "trades", "taker_base",
              "taker_quote", "ignore"]


def _months(start, end):
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _url(market, symbol, interval, ym):
    if market != "spot":
        raise SystemExit("ERROR: only --market spot is implemented. Futures (USD-M) "
                         "klines are a Layer-2 TODO — not scaffolded yet to avoid "
                         "guessing the schema. Use spot for now.")
    return f"{BASE}/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{ym}.zip"


def _download_month(market, symbol, interval, ym):
    cache_dir = os.path.join(RAW_DIR, market, symbol, interval)
    os.makedirs(cache_dir, exist_ok=True)
    zpath = os.path.join(cache_dir, f"{symbol}-{interval}-{ym}.zip")
    if not os.path.exists(zpath):
        url = _url(market, symbol, interval, ym)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            return None, f"{ym}: HTTP {e.code} ({url})"
        except Exception as e:
            return None, f"{ym}: {e.__class__.__name__} ({url})"
        with open(zpath, "wb") as f:
            f.write(data)
    try:
        with zipfile.ZipFile(zpath) as z:
            name = z.namelist()[0]
            with z.open(name) as fh:
                head = fh.read(64).decode("utf-8", "replace")
                hdr = 0 if head[:1].isdigit() else "infer"
        with zipfile.ZipFile(zpath) as z:
            with z.open(z.namelist()[0]) as fh:
                df = pd.read_csv(fh, header=hdr, names=KLINE_COLS if hdr == 0 else None)
    except Exception as e:
        return None, f"{ym}: bad zip/csv ({e})"
    return df, None


def _detect_unit(median_abs):
    """Detect epoch unit from open_time magnitude (Binance Vision has shipped
    seconds, ms, and more recently MICROSECONDS)."""
    if median_abs > 1e17:
        return "ns"
    if median_abs > 1e14:
        return "us"
    if median_abs > 1e11:
        return "ms"
    return "s"


def open_time_to_utc(open_time):
    """Convert a Binance open_time column to UTC timestamps with robust unit
    detection. Returns (timestamps, unit). Raises SystemExit on impossible
    values (so a misdetected unit fails clearly instead of writing garbage)."""
    v = pd.to_numeric(open_time, errors="coerce")
    if v.isna().any():
        raise SystemExit("ERROR: non-numeric open_time values in downloaded klines.")
    v = v.astype("int64")
    unit = _detect_unit(float(v.abs().median()))
    try:
        ts = pd.to_datetime(v, unit=unit, utc=True)
    except Exception as e:
        raise SystemExit(f"ERROR: could not parse open_time as {unit}: {e}")
    yr_min, yr_max = int(ts.dt.year.min()), int(ts.dt.year.max())
    if yr_min < 2009 or yr_max > 2100:
        raise SystemExit(f"ERROR: timestamp unit detection failed (unit='{unit}' -> "
                         f"years {yr_min}..{yr_max}). Refusing to write impossible timestamps.")
    return ts, unit


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--symbol", required=True)
    p.add_argument("--interval", default="1h")
    p.add_argument("--start", required=True, help="YYYY-MM")
    p.add_argument("--end", required=True, help="YYYY-MM")
    p.add_argument("--market", default="spot", choices=["spot", "futures"])
    p.add_argument("--output", required=True)
    p.add_argument("--allow-missing", action="store_true",
                   help="permit missing HISTORICAL (non-trailing) months instead of failing")
    args = p.parse_args()

    months = _months(args.start, args.end)
    print(f"Binance Vision {args.market} {args.symbol} {args.interval}: "
          f"{len(months)} months {args.start}..{args.end}")
    frames, ok_months, missing_idx = [], [], []
    for i, ym in enumerate(months):
        df, err = _download_month(args.market, args.symbol, args.interval, ym)
        if err:
            missing_idx.append(i)
            print(f"  MISSING {err}")
        else:
            frames.append(df)
            ok_months.append(ym)
            print(f"  ok {ym}: {len(df)} rows")
    if not frames:
        print("ERROR: no monthly files downloaded. Check symbol/interval/dates. "
              "Binance Vision may not have the current partial month or very early months.")
        sys.exit(1)

    # Classify missing months: trailing (after the last success) vs historical gaps.
    last_ok = max(i for i, ym in enumerate(months) if ym in ok_months)
    trailing_missing = [months[i] for i in missing_idx if i > last_ok]
    historical_missing = [months[i] for i in missing_idx if i < last_ok]
    if historical_missing and not args.allow_missing:
        print(f"\nERROR: {len(historical_missing)} HISTORICAL month(s) missing "
              f"(gaps before the last available month): {historical_missing}. "
              f"Refusing to write a file with silent gaps. Re-run with --allow-missing "
              f"if you accept the gaps, or adjust --start/--end.")
        sys.exit(1)

    allk = pd.concat(frames, ignore_index=True)
    ts, unit = open_time_to_utc(allk["open_time"])
    out = pd.DataFrame({
        "timestamp": ts,
        "open": allk["open"].astype(float), "high": allk["high"].astype(float),
        "low": allk["low"].astype(float), "close": allk["close"].astype(float),
        "volume": allk["volume"].astype(float),
    })
    if out[["open", "high", "low", "close", "volume"]].isna().any().any():
        print("ERROR: missing/non-numeric OHLCV in downloaded klines — not forward-filling.")
        sys.exit(1)
    out = out.sort_values("timestamp")
    dups = int(out["timestamp"].duplicated().sum())
    if dups:
        print(f"WARNING: {dups} duplicate timestamps dropped (kept first).")
        out = out.drop_duplicates("timestamp", keep="first")
    out = out.reset_index(drop=True)

    written = out.copy()
    written["timestamp"] = written["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    written.to_csv(args.output, index=False)

    print("\n" + "-" * 60)
    print(f"  source            : Binance Vision ({args.market})")
    print(f"  symbol            : {args.symbol}")
    print(f"  interval          : {args.interval}")
    print(f"  rows              : {len(out)}")
    print(f"  detected ts unit  : {unit}")
    print(f"  start timestamp   : {out['timestamp'].min()}")
    print(f"  end timestamp     : {out['timestamp'].max()}")
    print(f"  skipped trailing  : {len(trailing_missing)}"
          + (f" ({trailing_missing})" if trailing_missing else ""))
    if historical_missing:
        print(f"  historical gaps   : {len(historical_missing)} (allowed via --allow-missing; NOT filled)")
    print(f"  output            : {args.output}")
    print("-" * 60)
    print(f"Next: python3 ohlcv_csv_validation_harness.py --csv {args.output} --resample 6h")


if __name__ == "__main__":
    main()
