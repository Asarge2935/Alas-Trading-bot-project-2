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


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--symbol", required=True)
    p.add_argument("--interval", default="1h")
    p.add_argument("--start", required=True, help="YYYY-MM")
    p.add_argument("--end", required=True, help="YYYY-MM")
    p.add_argument("--market", default="spot", choices=["spot", "futures"])
    p.add_argument("--output", required=True)
    args = p.parse_args()

    months = _months(args.start, args.end)
    print(f"Binance Vision {args.market} {args.symbol} {args.interval}: "
          f"{len(months)} months {args.start}..{args.end}")
    frames, missing = [], []
    for ym in months:
        df, err = _download_month(args.market, args.symbol, args.interval, ym)
        if err:
            missing.append(err)
            print(f"  MISSING {err}")
        else:
            frames.append(df)
            print(f"  ok {ym}: {len(df)} rows")
    if not frames:
        print("ERROR: no monthly files downloaded. Check symbol/interval/dates. "
              "Binance Vision may not have the current partial month or very early months.")
        sys.exit(1)

    allk = pd.concat(frames, ignore_index=True)
    out = pd.DataFrame({
        "timestamp": pd.to_datetime(allk["open_time"].astype("int64"),
                                    unit="ms" if allk["open_time"].astype("int64").median() > 1e12 else "s",
                                    utc=True),
        "open": allk["open"].astype(float), "high": allk["high"].astype(float),
        "low": allk["low"].astype(float), "close": allk["close"].astype(float),
        "volume": allk["volume"].astype(float),
    }).sort_values("timestamp").drop_duplicates("timestamp", keep="first").reset_index(drop=True)

    written = out.copy()
    written["timestamp"] = written["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    written.to_csv(args.output, index=False)
    print(f"\nWrote {args.output}: {len(out)} rows  "
          f"{out['timestamp'].min()} -> {out['timestamp'].max()}")
    if missing:
        print(f"NOTE: {len(missing)} month(s) missing (listed above) — coverage has gaps; "
              f"NOT forward-filled.")
    print(f"Next: python3 ohlcv_csv_validation_harness.py --csv {args.output} --resample 6h")


if __name__ == "__main__":
    main()
