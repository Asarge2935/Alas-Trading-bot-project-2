"""
Offline runtime verification harness for breakout_backtest.py.

Generates synthetic 1D OHLCV for BTC/ETH/SOL with deliberate
compression-then-breakout patterns, runs run_breakout_backtest, exports
CSVs, then asserts the runtime invariants:

  - breakout_output/trades.csv and equity_curve.csv get written
  - Portfolio P&L reconciles to sum(net_pnl_usd) read back from the CSV
  - Equity column is mark-to-market (differs from $500 + cumulative_pnl
    on at least one bar with open_positions > 0)
  - drawdown_pct is non-negative throughout
  - Trade limits respected (max trades/asset/day, max trades/week, max
    open positions)
  - Every trade has setup_type = "breakout"
  - Cooldown invariant: no two same-symbol entries within COOLDOWN bars
    of a clean stop-out (entered without partial)

Run:  python3 verify_offline_breakout.py
Exits non-zero on any assertion failure.
"""

import csv
import os
import sys

import numpy as np
import pandas as pd

import breakout_backtest as bk


def synth_series_with_breakouts(n_bars, seed, base_price):
    """
    Build a 1D OHLCV series with a repeating cycle:
      - 35 bars of tight range  (drives ATR(14) low → compression)
      - 10 bars of strong directional move with elevated volume
      - 5 bars of mean-revert chop
    Cycle length 50 → over 1500 bars we get 30 cycles, mixed long/short.
    """
    rng = np.random.default_rng(seed)
    closes = np.empty(n_bars, dtype=float)
    highs = np.empty(n_bars, dtype=float)
    lows = np.empty(n_bars, dtype=float)
    opens = np.empty(n_bars, dtype=float)
    volumes = np.empty(n_bars, dtype=float)

    price = base_price
    cycle_len = 50
    direction = 1
    for i in range(n_bars):
        phase = i % cycle_len
        if phase < 35:
            # Tight range — small noise around current price.
            opens[i] = price
            change = rng.normal(0, base_price * 0.003)
            closes[i] = price + change
            highs[i] = max(opens[i], closes[i]) + abs(rng.normal(0, base_price * 0.003))
            lows[i] = min(opens[i], closes[i]) - abs(rng.normal(0, base_price * 0.003))
            volumes[i] = rng.uniform(800, 1200)
            price = closes[i]
        elif phase < 45:
            # Strong directional move.
            move = direction * base_price * 0.012
            opens[i] = price
            closes[i] = price + move + rng.normal(0, base_price * 0.002)
            highs[i] = max(opens[i], closes[i]) + abs(rng.normal(0, base_price * 0.003))
            lows[i] = min(opens[i], closes[i]) - abs(rng.normal(0, base_price * 0.002))
            volumes[i] = rng.uniform(2400, 4000)  # spike (>1.2× the ~1000 avg)
            price = closes[i]
        else:
            # Chop / fade.
            opens[i] = price
            closes[i] = price + rng.normal(0, base_price * 0.005)
            highs[i] = max(opens[i], closes[i]) + abs(rng.normal(0, base_price * 0.004))
            lows[i] = min(opens[i], closes[i]) - abs(rng.normal(0, base_price * 0.004))
            volumes[i] = rng.uniform(1000, 1800)
            price = closes[i]
        if phase == cycle_len - 1:
            # Flip direction every cycle so we get longs and shorts.
            direction *= -1

    end = pd.Timestamp.now(tz="UTC").normalize()
    times = pd.date_range(end=end, periods=n_bars, freq="D")
    return pd.DataFrame({
        "time": times,
        "low": lows,
        "high": highs,
        "open": opens,
        "close": closes,
        "volume": volumes,
    })


def main():
    failures = []

    base_prices = {"BTC-USD": 60_000, "ETH-USD": 3_000, "SOL-USD": 150}
    data = {}
    for i, sym in enumerate(bk.ASSETS):
        df = synth_series_with_breakouts(1500, seed=11 + i, base_price=base_prices[sym])
        df = bk.bt.drop_incomplete_candles(df, bk.TIMEFRAME_SECONDS)
        df = bk.add_indicators_breakout(df)
        data[sym] = df
        print(f"  {sym}: {len(df)} bars (synthetic), "
              f"compression_ratio range "
              f"[{df['compression_ratio'].min():.2f}, {df['compression_ratio'].max():.2f}]")

    print("\nRunning offline breakout backtest...")
    trades, equity_curve = bk.run_breakout_backtest(data)
    print(f"  trades simulated: {len(trades)}")
    print(f"  equity curve points: {len(equity_curve)}")

    # 1. Export and confirm CSVs exist.
    bk.export_breakout_trades_csv(trades, bk.TRADES_CSV)
    bk.bt.export_equity_csv(equity_curve, bk.EQUITY_CSV)
    if trades and not os.path.exists(bk.TRADES_CSV):
        failures.append("trades.csv was not written")
    if not os.path.exists(bk.EQUITY_CSV):
        failures.append("equity_curve.csv was not written")

    # 2. Reconciliation.
    if trades:
        sum_pnl = sum(t.net_pnl_usd for t in trades)
        with open(bk.TRADES_CSV) as f:
            csv_pnl = sum(float(row["net_pnl_usd"]) for row in csv.DictReader(f))
        if abs(sum_pnl - csv_pnl) > 1e-6:
            failures.append(f"trades.csv sum {csv_pnl:.4f} != trades sum {sum_pnl:.4f}")
        else:
            print(f"\n[OK] trades.csv sum matches in-memory sum: ${sum_pnl:+.4f}")

    # 3. Mark-to-market: equity should differ from $500 + cumulative_pnl
    #    on at least one bar where positions were open.
    open_bar_rows = [p for p in equity_curve if p["open_positions"] > 0]
    drift_seen = False
    for p in open_bar_rows:
        closed_only = bk.ACCOUNT_SIZE_USD + p["cumulative_pnl"]
        if abs(p["equity"] - closed_only) > 1e-9:
            drift_seen = True
            break
    if open_bar_rows and not drift_seen:
        failures.append("Equity never differed from $500+cumulative_pnl on bars "
                        "with open positions — MTM not actually applied.")
    elif not open_bar_rows:
        print("[WARN] No bars had open positions — MTM assertion vacuously passes.")
    else:
        sample = open_bar_rows[0]
        print(f"[OK] MTM applied: at {sample['time']} "
              f"equity={sample['equity']:.4f}, "
              f"closed-only={bk.ACCOUNT_SIZE_USD + sample['cumulative_pnl']:.4f}")

    # 4. Drawdown column never negative.
    if equity_curve:
        if any(p["drawdown_pct"] < -1e-9 for p in equity_curve):
            failures.append("drawdown_pct went negative — formula is wrong.")
        else:
            print("[OK] drawdown_pct is non-negative throughout.")

    # 5. Trade limits.
    if trades:
        from collections import Counter
        per_day = Counter()
        per_week = Counter()
        for t in trades:
            d = t.entry_time.date()
            wk = t.entry_time.isocalendar()[:2]
            per_day[(t.symbol, d)] += 1
            per_week[wk] += 1
        max_day = max(per_day.values()) if per_day else 0
        max_week = max(per_week.values()) if per_week else 0
        if max_day > bk.MAX_TRADES_PER_ASSET_PER_DAY:
            failures.append(f"trades/asset/day = {max_day} > limit {bk.MAX_TRADES_PER_ASSET_PER_DAY}")
        else:
            print(f"[OK] max trades/asset/day: {max_day} (limit {bk.MAX_TRADES_PER_ASSET_PER_DAY})")
        if max_week > bk.MAX_TRADES_PORTFOLIO_PER_WEEK:
            failures.append(f"trades/week = {max_week} > limit {bk.MAX_TRADES_PORTFOLIO_PER_WEEK}")
        else:
            print(f"[OK] max trades/week: {max_week} (limit {bk.MAX_TRADES_PORTFOLIO_PER_WEEK})")

    max_open = max((p["open_positions"] for p in equity_curve), default=0)
    if max_open > bk.MAX_OPEN_POSITIONS:
        failures.append(f"max open positions = {max_open} > limit {bk.MAX_OPEN_POSITIONS}")
    else:
        print(f"[OK] max open positions: {max_open} (limit {bk.MAX_OPEN_POSITIONS})")

    # 6. Every trade tagged setup_type = "breakout".
    if trades:
        bad = [t for t in trades if t.setup_type != "breakout"]
        if bad:
            failures.append(f"{len(bad)} trades have setup_type != 'breakout'")
        else:
            print(f"[OK] all {len(trades)} trades tagged setup_type='breakout'")

    # 7. Cooldown invariant: after a clean stop-out (no partial), there should
    #    be no NEW entry on the same symbol within COOLDOWN_BARS_AFTER_STOP days.
    if trades:
        clean_stops_by_sym = {}
        for t in trades:
            if t.exit_reason == "stop_hit" and t.exit_price_partial is None:
                clean_stops_by_sym.setdefault(t.symbol, []).append(t.exit_time)
        cooldown_violations = []
        for sym, stop_times in clean_stops_by_sym.items():
            sym_entries = sorted(t.entry_time for t in trades if t.symbol == sym)
            for stop_time in stop_times:
                cutoff = stop_time + pd.Timedelta(days=bk.COOLDOWN_BARS_AFTER_STOP)
                # Entries strictly AFTER stop_time but before the cutoff are violations.
                for entry_time in sym_entries:
                    if stop_time < entry_time < cutoff:
                        cooldown_violations.append((sym, stop_time, entry_time))
        if cooldown_violations:
            for sym, st, et in cooldown_violations[:3]:
                failures.append(f"cooldown violated on {sym}: stopped {st}, re-entered {et}")
        elif clean_stops_by_sym:
            print(f"[OK] cooldown respected after "
                  f"{sum(len(v) for v in clean_stops_by_sym.values())} clean stop-outs")

    # 8. Run the report so we exercise that path too.
    print()
    bk.report_summary_breakout(trades, equity_curve)

    if failures:
        print("\n[FAIL] Offline verification found issues:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[PASS] Offline runtime verification — all assertions held.")


if __name__ == "__main__":
    main()
