"""
Filter funnel diagnostic — counts how many bar-asset signals survive each
filter in evaluate_signal, plus counterfactuals where one filter at a time
is dropped.

Goal: identify which filter(s) are doing the cutting BEFORE relaxing any
parameter. A 4-trade backtest doesn't tell you whether the strategy is
broken; it tells you the funnel collapsed somewhere. This shows where.

Run:
    python verify_filter_funnel.py

Paste the entire console output back into chat.
"""

import sys

import pandas as pd

import backtest as bt


def main():
    # ---- Fetch + indicators ----
    print("Fetching candles...")
    data = {}
    for symbol in bt.ASSETS:
        df = bt.fetch_candles(symbol, bt.TIMEFRAME_SECONDS, bt.DAYS_BACK)
        df = bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS)
        if df.empty:
            print(f"  {symbol}: 0 bars — skipping")
            continue
        df = bt.add_indicators(df)
        data[symbol] = df
        print(f"  {symbol}: {len(df)} bars")
    if "BTC-USD" not in data:
        print("ERROR: BTC-USD missing — cannot evaluate BTC RSI filter.")
        sys.exit(1)
    btc_indexed = data["BTC-USD"].set_index("time")
    print()

    # ---- Counters ----
    total_bar_assets = 0
    pass_atr = 0
    pass_atr_vol = 0

    L_close = L_ema = L_rsi = L_btc = 0   # long cumulative
    S_close = S_ema = S_rsi = S_btc = 0   # short cumulative

    cf_baseline_long = cf_baseline_short = 0
    cf_no_vol_long = cf_no_vol_short = 0
    cf_no_btc_long = cf_no_btc_short = 0
    cf_no_atr_long = cf_no_atr_short = 0
    cf_no_vol_no_btc_long = cf_no_vol_no_btc_short = 0
    cf_vol_10x_long = cf_vol_10x_short = 0   # volume threshold relaxed to 1.0x
    cf_rsi_3565_long = cf_rsi_3565_short = 0  # RSI thresholds relaxed to 35/65

    required_cols = ["ema_50", "ema_20", "rsi_14", "atr_14",
                     "atr_regime_avg", "volume_avg_20"]

    for sym, df in data.items():
        for _, row in df.iterrows():
            if any(pd.isna(row[c]) for c in required_cols):
                continue

            total_bar_assets += 1

            # BTC RSI at this timestamp (neutral 50 if missing)
            t = row["time"]
            btc_rsi = 50.0
            if t in btc_indexed.index:
                v = btc_indexed.loc[t, "rsi_14"]
                if not pd.isna(v):
                    btc_rsi = v

            # ---- per-filter booleans ----
            atr_ok = row["atr_14"] <= bt.ATR_REGIME_MULTIPLE * row["atr_regime_avg"]
            vol_ok = row["volume"] >= bt.VOLUME_MULTIPLIER * row["volume_avg_20"]
            vol_ok_10x = row["volume"] >= 1.0 * row["volume_avg_20"]

            close_above = row["close"] > row["ema_50"]
            ema20_above = row["ema_20"] > row["ema_50"]
            rsi_below_30 = row["rsi_14"] < bt.RSI_LONG_MAX
            rsi_below_35 = row["rsi_14"] < 35
            btc_long_ok = btc_rsi >= bt.BTC_REGIME_RSI_LOW

            close_below = row["close"] < row["ema_50"]
            ema20_below = row["ema_20"] < row["ema_50"]
            rsi_above_70 = row["rsi_14"] > bt.RSI_SHORT_MIN
            rsi_above_65 = row["rsi_14"] > 65
            btc_short_ok = btc_rsi <= bt.BTC_REGIME_RSI_HIGH

            # ---- cumulative funnel (in evaluate_signal order) ----
            if atr_ok:
                pass_atr += 1
                if vol_ok:
                    pass_atr_vol += 1
                    # long
                    if close_above:
                        L_close += 1
                        if ema20_above:
                            L_ema += 1
                            if rsi_below_30:
                                L_rsi += 1
                                if btc_long_ok:
                                    L_btc += 1
                    # short
                    if close_below:
                        S_close += 1
                        if ema20_below:
                            S_ema += 1
                            if rsi_above_70:
                                S_rsi += 1
                                if btc_short_ok:
                                    S_btc += 1

            # ---- counterfactuals ----
            long_core = close_above and ema20_above and rsi_below_30
            short_core = close_below and ema20_below and rsi_above_70

            # baseline (all filters as-is)
            if atr_ok and vol_ok and long_core and btc_long_ok:
                cf_baseline_long += 1
            if atr_ok and vol_ok and short_core and btc_short_ok:
                cf_baseline_short += 1
            # drop volume only
            if atr_ok and long_core and btc_long_ok:
                cf_no_vol_long += 1
            if atr_ok and short_core and btc_short_ok:
                cf_no_vol_short += 1
            # drop BTC regime only
            if atr_ok and vol_ok and long_core:
                cf_no_btc_long += 1
            if atr_ok and vol_ok and short_core:
                cf_no_btc_short += 1
            # drop ATR regime only
            if vol_ok and long_core and btc_long_ok:
                cf_no_atr_long += 1
            if vol_ok and short_core and btc_short_ok:
                cf_no_atr_short += 1
            # drop volume + BTC
            if atr_ok and long_core:
                cf_no_vol_no_btc_long += 1
            if atr_ok and short_core:
                cf_no_vol_no_btc_short += 1
            # volume threshold relaxed to 1.0x (not below average)
            if atr_ok and vol_ok_10x and long_core and btc_long_ok:
                cf_vol_10x_long += 1
            if atr_ok and vol_ok_10x and short_core and btc_short_ok:
                cf_vol_10x_short += 1
            # RSI thresholds relaxed to 35/65
            long_core_3565 = close_above and ema20_above and rsi_below_35
            short_core_3565 = close_below and ema20_below and rsi_above_65
            if atr_ok and vol_ok and long_core_3565 and btc_long_ok:
                cf_rsi_3565_long += 1
            if atr_ok and vol_ok and short_core_3565 and btc_short_ok:
                cf_rsi_3565_short += 1

    # ---- print ----
    n = total_bar_assets

    def p(x):
        return f"{x/n*100:5.1f}%" if n else "—"

    print(f"Total bar-asset pairs evaluated (post-warmup): {n}")
    print()
    print("=== CUMULATIVE FUNNEL (filters applied in evaluate_signal order) ===")
    print(f"  Pass ATR regime gate:        {pass_atr:>6} ({p(pass_atr)})")
    print(f"  Pass volume filter (>=1.2x): {pass_atr_vol:>6} ({p(pass_atr_vol)})")
    print()
    print("  LONG path:")
    print(f"    + close > EMA50:           {L_close:>6} ({p(L_close)})")
    print(f"    + EMA20 > EMA50:           {L_ema:>6} ({p(L_ema)})")
    print(f"    + RSI < 30:                {L_rsi:>6} ({p(L_rsi)})")
    print(f"    + BTC RSI >= 35:           {L_btc:>6} ({p(L_btc)})  <-- final long signal count")
    print()
    print("  SHORT path:")
    print(f"    + close < EMA50:           {S_close:>6} ({p(S_close)})")
    print(f"    + EMA20 < EMA50:           {S_ema:>6} ({p(S_ema)})")
    print(f"    + RSI > 70:                {S_rsi:>6} ({p(S_rsi)})")
    print(f"    + BTC RSI <= 65:           {S_btc:>6} ({p(S_btc)})  <-- final short signal count")
    print()
    print("=== COUNTERFACTUALS (signal counts under different filter configs) ===")
    print(f"  Baseline (current rules.json):       long={cf_baseline_long:>5}, short={cf_baseline_short:>5}")
    print(f"  Drop volume filter entirely:         long={cf_no_vol_long:>5}, short={cf_no_vol_short:>5}")
    print(f"  Drop BTC regime filter entirely:     long={cf_no_btc_long:>5}, short={cf_no_btc_short:>5}")
    print(f"  Drop ATR regime filter entirely:     long={cf_no_atr_long:>5}, short={cf_no_atr_short:>5}")
    print(f"  Drop volume + BTC:                   long={cf_no_vol_no_btc_long:>5}, short={cf_no_vol_no_btc_short:>5}")
    print(f"  Volume threshold 1.2x -> 1.0x:       long={cf_vol_10x_long:>5}, short={cf_vol_10x_short:>5}")
    print(f"  RSI thresholds 30/70 -> 35/65:       long={cf_rsi_3565_long:>5}, short={cf_rsi_3565_short:>5}")
    print()
    print("=== HOW TO READ ===")
    print("These are SIGNAL counts (qualifying bar-asset pairs across all 6 assets,")
    print("all closed bars in the window). Realised TRADE counts will be lower because:")
    print("  - Scanner picks at most 1 trade per bar across all 6 assets")
    print("  - max 2 open positions, max 5 trades / week, max 2 / asset / day")
    print("  - Same-direction filter requires new signal to be 5+ RSI points stronger")
    print()
    print("Rough rule of thumb: realised trades ~= 30-60% of signal count, capped at")
    print("the weekly portfolio limit. So 80 signals -> roughly 30-50 actual trades.")


if __name__ == "__main__":
    main()
