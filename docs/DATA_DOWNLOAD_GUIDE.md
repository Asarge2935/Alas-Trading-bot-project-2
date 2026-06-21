# Data Download Guide — Independent OHLCV for Validation

**This is for VALIDATION ONLY. Nothing here deploys, paper-trades, or live-trades.**
The goal is to get independent BTC/ETH OHLCV history and check whether the
existing ETH/BTC behaviors persist on data from another exchange.

All scripts live in the repo, so always run them from there:

```bash
cd ~/Alas-Trading-bot-project-2
```

## Normalized schema (the only thing the tools require)

Every CSV that feeds the harness must end up with exactly these columns:

```
timestamp,open,high,low,close,volume
```

- `timestamp`: ISO-8601 UTC (e.g. `2023-01-02T06:00:00Z`) or epoch seconds/ms.
- prices/volume numeric; rows ascending by time; no duplicate timestamps.
- Missing candles are **reported, never silently filled**.

`normalize_external_ohlcv.py` produces this from common raw formats.

---

## Option A — CryptoDataDownload (easiest)

1. Go to CryptoDataDownload and download the **Binance** `BTCUSDT` and `ETHUSDT`
   historical OHLCV CSVs (pick the smallest interval offered that you can
   handle; 1h is ideal because the harness can resample up).
2. Save them in your home folder as:
   - `~/btc_raw.csv`
   - `~/eth_raw.csv`
3. Normalize, validate, and run the diagnostics:

```bash
cd ~/Alas-Trading-bot-project-2

python3 normalize_external_ohlcv.py --input ~/btc_raw.csv --output ~/btc.csv \
    --source cryptodatadownload --symbol BTCUSDT
python3 normalize_external_ohlcv.py --input ~/eth_raw.csv --output ~/eth.csv \
    --source cryptodatadownload --symbol ETHUSDT

python3 ohlcv_csv_validation_harness.py --csv ~/btc.csv --resample 12h
python3 btc_independent_data_validation.py --csv ~/btc.csv --label binance_btc_spot

python3 ohlcv_csv_validation_harness.py --csv ~/eth.csv --resample 6h
python3 eth_independent_data_validation.py --csv ~/eth.csv --label binance_eth_spot
```

CryptoDataDownload files often have a **title line above the header** — the
normalizer auto-skips it. Volume columns may be named `Volume BTC` /
`Volume USDT`; the normalizer auto-detects them.

---

## Option B — Binance Data Vision (official)

Use `data.binance.vision`. Spot klines come as **monthly (or daily) ZIP files**
that must be combined. Two ways:

### B1 — automated (this repo)

```bash
cd ~/Alas-Trading-bot-project-2
python3 download_binance_vision_klines.py --symbol BTCUSDT --interval 1h \
    --start 2020-01 --end 2026-05 --market spot --output ~/btc.csv
python3 download_binance_vision_klines.py --symbol ETHUSDT --interval 1h \
    --start 2020-01 --end 2026-05 --market spot --output ~/eth.csv

python3 ohlcv_csv_validation_harness.py --csv ~/btc.csv --resample 12h
python3 btc_independent_data_validation.py --csv ~/btc.csv --label binance_btc_spot
python3 ohlcv_csv_validation_harness.py --csv ~/eth.csv --resample 6h
python3 eth_independent_data_validation.py --csv ~/eth.csv --label binance_eth_spot
```

- It downloads each month's ZIP to `data_external/raw/binance_vision/...`,
  combines, and writes a normalized CSV (already in the target schema — no
  separate normalize step needed).
- **Missing months fail clearly** (Binance Vision often lacks the current
  partial month and the earliest months of a symbol). Adjust `--start`/`--end`.
- **`--market spot` only** for now. `--market futures` is a documented **Layer-2
  TODO** and will exit rather than guess the schema.

### B2 — manual

1. Download the monthly ZIPs for `BTCUSDT` / `ETHUSDT`, interval `1h`, market
   `spot` from `data.binance.vision`.
2. Unzip and concatenate the CSVs (they are headerless: `open_time, open, high,
   low, close, volume, close_time, ...`).
3. Run the normalizer with `--source binance_vision`, then the harness.

> Combining manually is error-prone — prefer **B1** if you can.

---

## Common mistakes (read this if something fails)

- **Wrong working directory.** `python3: can't open file ...` means you're not in
  the repo. Run `cd ~/Alas-Trading-bot-project-2` first (or pass full paths).
- **Paste artifact `^[[200~`** at the start of a command (`command not found`).
  That's bracketed-paste noise — retype the command or paste line-by-line.
- **CSV still zipped.** Unzip first (`.zip`/`.gz`). The tools read plain `.csv`.
- **Timestamp not parsed.** The normalizer handles epoch s/ms and ISO; if it
  errors, your timestamp column may be in an odd unit — check the summary.
- **Columns not normalized.** Always run `normalize_external_ohlcv.py` (or the
  Binance downloader) *before* the validation scripts.
- **Using Kraken REST OHLC.** Avoid for now — it returns only a shallow recent
  window (no deep multi-year history). Use Binance Vision / CryptoDataDownload.

---

## What each step tells you

- `ohlcv_csv_validation_harness.py` — confirms the file is clean and resamples.
- `btc_independent_data_validation.py` — BTC 12H + 1D **full-compression**
  (comp_count==3); compare *behavior/shape* across data, not PF.
- `eth_independent_data_validation.py` — ETH 6H strict breakout. **Note:** ETH's
  strict regime needs BTC + relative-strength context, so an ETH-only CSV runs
  ETH from your file but BTC/SOL from the built-in provider, on the time
  intersection. Fully independent ETH validation needs matching BTC/SOL CSVs
  (future work).

**Reminder:** this is independent-data validation. Nothing is deployable; do not
advance any model on PF alone. No paper trading, no live trading, no tuning.
