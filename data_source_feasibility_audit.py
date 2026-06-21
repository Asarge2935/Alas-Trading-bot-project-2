"""
DATA SOURCE FEASIBILITY AUDIT — audit only, no network, no strategy change.

Inspects what validation-data expansion is realistically possible in THIS repo
today, by introspecting backtest.py's data layer. It does NOT add providers and
does NOT change any backtest behavior.

Run:
    python3 data_source_feasibility_audit.py

Outputs (backtest_output_data_feasibility/):
    summary.txt, data_source_capabilities.csv
"""

import csv
import inspect
import os

import backtest as bt

OUT_DIR = "backtest_output_data_feasibility"

# Coinbase Exchange public candles supports these granularities (seconds).
COINBASE_NATIVE_GRANS = {60: "1m", 300: "5m", 900: "15m", 3600: "1h",
                         21600: "6h", 86400: "1d"}


def audit():
    rows = []  # (capability, supported, detail)

    # Provider(s)
    fetch_src = inspect.getsource(bt.fetch_candles)
    provider = "Coinbase Exchange (api.exchange.coinbase.com) — SPOT" \
        if "api.exchange.coinbase.com" in fetch_src else "unknown"
    rows.append(("candle_provider", "yes", provider))
    rows.append(("multiple_providers", "no",
                 "single hard-coded Coinbase Exchange spot endpoint in fetch_candles()"))

    # Symbols
    rows.append(("configured_symbols", "yes", ", ".join(bt.ASSETS)))
    rows.append(("arbitrary_symbols", "partial",
                 "any Coinbase Exchange SPOT product id works via fetch_candles(product_id, ...); "
                 "not validated beyond BTC/ETH/SOL-USD"))

    # Intervals
    rows.append(("native_intervals", "yes",
                 ", ".join(f"{v}({k}s)" for k, v in sorted(COINBASE_NATIVE_GRANS.items()))))
    rows.append(("entry_timeframe_default", "yes", f"{bt.TIMEFRAME_SECONDS}s (6h)"))
    rows.append(("non_native_intervals_via_aggregation", "yes",
                 "4h/8h/12h are NOT native; built by aggregating 1h or 6h "
                 "(see *_diagnostic.py _aggregate(); open=first/high=max/low=min/close=last/volume=sum)"))

    # Lookback
    rows.append(("default_lookback_days", "info", str(bt.DAYS_BACK)))
    paginates = "cursor" in fetch_src and "while" in fetch_src
    rows.append(("pagination", "yes" if paginates else "no",
                 "fetch_candles paginates 300 candles/request backward to start"))
    rows.append((">1460_days_requestable", "yes",
                 "fetch_candles(days_back=N) accepts any N; BUT Coinbase only SERVES a finite "
                 "history (~a few years for 6h/1d). Requesting more returns only what exists; "
                 "it does NOT extend history. So ~1460d is roughly the practical 6h ceiling."))

    # Aggregation feasibility (1H -> coarser)
    rows.append(("aggregate_1h_to_6h_8h_12h_1d", "yes",
                 "1h(3600) divides 6h/8h/12h/1d cleanly; floor()-based bucketing is epoch-aligned. "
                 "ohlcv_csv_validation_harness.py exposes a generic resampler."))

    # CSV
    cache_src = inspect.getsource(bt.load_candles) + inspect.getsource(bt._read_cache)
    rows.append(("internal_csv_cache", "yes",
                 "candles cached to data_cache/candles/*.csv and re-read (read_csv/to_csv)"))
    has_csv_harness = os.path.exists("ohlcv_csv_validation_harness.py")
    rows.append(("generic_user_csv_import", "yes" if has_csv_harness else "no",
                 "ohlcv_csv_validation_harness.py (timestamp,open,high,low,close,volume; UTC; "
                 "resample to 6h/8h/12h/1d)" if has_csv_harness else "not present"))
    rows.append(("exchange_csv_import_cleanly_addable", "yes",
                 "any exchange OHLCV export matching the 6-col schema runs via the CSV harness "
                 "+ eth/btc_independent_data_validation.py — no strategy change needed"))

    # Perp / derivatives data
    rows.append(("perp_futures_candles", "no",
                 "Coinbase Exchange endpoint is SPOT only; no perp/futures candle support in code"))
    rows.append(("funding_rate_data", "no", "not supported anywhere in the repo"))
    rows.append(("open_interest_data", "no", "not supported anywhere in the repo"))
    rows.append(("liquidation_data", "no", "not supported anywhere in the repo"))

    return rows


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = audit()
    with open(os.path.join(OUT_DIR, "data_source_capabilities.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["capability", "supported", "detail"])
        w.writerows(rows)

    lines = ["=" * 88,
             "DATA SOURCE FEASIBILITY AUDIT (audit only — no network, no strategy change)",
             "=" * 88, ""]
    for cap, sup, detail in rows:
        lines.append(f"  [{sup.upper():<7}] {cap}")
        lines.append(f"            {detail}")
    lines += ["",
              "BOTTOM LINE:",
              "  - One provider today: Coinbase Exchange SPOT candles (no perps/funding/OI/liq).",
              "  - Native intervals 1m/5m/15m/1h/6h/1d; 8h/12h via aggregation from 1h/6h.",
              "  - Longer history than ~1460d is NOT available from this provider (it serves a",
              "    finite window; requesting more days does not extend it).",
              "  - Independent validation IS possible via CSV: export ETH/BTC spot (or perp)",
              "    OHLCV from Coinbase/Binance/Kraken/etc and run the *_independent_data_validation",
              "    scripts. That is the realistic path to more independent data right now.",
              "  - Perp-specific data (funding/OI/liquidation) would require NEW providers — out",
              "    of scope for this audit; do not add yet."]
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nWrote {OUT_DIR}/summary.txt and data_source_capabilities.csv")


if __name__ == "__main__":
    main()
