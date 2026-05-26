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


CANDIDATE_UNITS = ["ms", "us", "ns", "s"]
_INTERVAL_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                     "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
                     "8h": 28800, "12h": 43200, "1d": 86400}


def _interval_seconds(interval):
    return _INTERVAL_SECONDS.get(interval)


def open_time_to_utc(open_time, interval_seconds, label=""):
    """Robust, SELF-VERIFYING epoch-unit detection for one block of open_time
    values (call PER monthly file — Binance Vision changed units mid-history).

    Tries units ms/us/ns/s; for each, requires: no NaT, years in [2009,2100],
    mostly increasing, and (if interval known) median spacing ~ the requested
    interval. Picks the passing unit whose spacing best matches. On total
    failure, raises SystemExit listing every unit's failure reason + the raw
    min/median/max. Returns (timestamps_utc, unit)."""
    v = pd.to_numeric(open_time, errors="coerce")
    diag = (f"[{label}] first3={list(open_time.head(3))} "
            f"min={v.min():.0f} median={v.median():.0f} max={v.max():.0f} "
            f"nonnumeric={int(v.isna().sum())}")
    if v.isna().any():
        raise SystemExit(f"ERROR: non-numeric open_time values.\n  {diag}")
    vv = v.astype("int64")

    passed, reasons = [], []
    for unit in CANDIDATE_UNITS:
        ts = pd.to_datetime(vv, unit=unit, utc=True, errors="coerce")
        if ts.isna().any():
            reasons.append(f"{unit}: NaT / out-of-bounds")
            continue
        y0, y1 = int(ts.dt.year.min()), int(ts.dt.year.max())
        if y0 < 2009 or y1 > 2100:
            reasons.append(f"{unit}: years {y0}..{y1} outside [2009,2100]")
            continue
        d = ts.diff().dropna().dt.total_seconds()
        inc = float((d > 0).mean()) if len(d) else 1.0
        if inc < 0.9:
            reasons.append(f"{unit}: only {inc*100:.0f}% increasing")
            continue
        med = float(d.median()) if len(d) else float("nan")
        if interval_seconds and not (0.5 * interval_seconds <= med <= 2.0 * interval_seconds):
            reasons.append(f"{unit}: median spacing {med:.0f}s != ~{interval_seconds}s")
            continue
        passed.append((unit, abs(med - (interval_seconds or med))))

    if not passed:
        raise SystemExit("ERROR: could not parse open_time. tried units ms/us/ns/s:\n  "
                         + "\n  ".join(reasons) + f"\n  raw {diag}")
    passed.sort(key=lambda x: x[1])
    unit = passed[0][0]
    return pd.to_datetime(vv, unit=unit, utc=True), unit


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

    interval_seconds = _interval_seconds(args.interval)
    months = _months(args.start, args.end)
    print(f"Binance Vision {args.market} {args.symbol} {args.interval}: "
          f"{len(months)} months {args.start}..{args.end}")
    raw_frames, ok_months, missing_idx = [], [], []
    for i, ym in enumerate(months):
        df, err = _download_month(args.market, args.symbol, args.interval, ym)
        if err:
            missing_idx.append(i)
            print(f"  MISSING {err}")
        else:
            df["_ym"] = ym
            raw_frames.append(df)
            ok_months.append(ym)
            print(f"  ok {ym}: {len(df)} rows")
    if not raw_frames:
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

    # Diagnostic sample of the COMBINED raw open_time (mixed units show up here).
    combined_ot = pd.to_numeric(pd.concat([f["open_time"] for f in raw_frames],
                                          ignore_index=True), errors="coerce")
    print(f"\nopen_time diagnostic: first3={list(combined_ot.head(3))} "
          f"min={combined_ot.min():.0f} median={combined_ot.median():.0f} "
          f"max={combined_ot.max():.0f}")

    # Convert PER MONTH so a mid-history unit switch (ms -> us) is handled.
    norm, unit_counts = [], {}
    for f in raw_frames:
        ts, unit = open_time_to_utc(f["open_time"], interval_seconds, label=f["_ym"].iloc[0])
        unit_counts[unit] = unit_counts.get(unit, 0) + 1
        norm.append(pd.DataFrame({
            "timestamp": ts.values,
            "open": f["open"].astype(float).values, "high": f["high"].astype(float).values,
            "low": f["low"].astype(float).values, "close": f["close"].astype(float).values,
            "volume": f["volume"].astype(float).values,
        }))
    out = pd.concat(norm, ignore_index=True)
    if out[["open", "high", "low", "close", "volume"]].isna().any().any():
        print("ERROR: missing/non-numeric OHLCV in downloaded klines — not forward-filling.")
        sys.exit(1)
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
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

    unit_str = ", ".join(f"{u}x{c}" for u, c in sorted(unit_counts.items()))
    print("\n" + "-" * 60)
    print(f"  source            : Binance Vision ({args.market})")
    print(f"  symbol            : {args.symbol}")
    print(f"  interval          : {args.interval}")
    print(f"  rows              : {len(out)}")
    print(f"  detected ts unit  : {unit_str}  (per-month; mixed = mid-history switch)")
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
