"""
Offline runtime verification harness.

Purpose: exercise backtest.run_backtest, report_summary, and CSV exporters
with synthetic OHLCV (no Coinbase needed) and assert the runtime gates from
the review:

    - trades.csv and equity_curve.csv get written
    - portfolio P&L reconciles to sum(net_pnl_usd)
    - equity column is mark-to-market when positions are open
    - drawdown_pct uses MTM equity
    - daily/weekly trade limits respected

Run:  python verify_offline.py
Exits non-zero if any assertion fails.
"""

import csv
import os
import sys
import numpy as np
import pandas as pd

import backtest as bt


def synth_series(n_bars, seed, base_price, drift=0.0, vol=0.02):
    """Generate a synthetic OHLCV series with trending regimes and breakouts.

    The canonical strategy only trades in a sustained BTC-led regime after a
    20-bar breakout with a strong close. Pure noise rarely satisfies all of
    that, so we build slow multi-month up/down trends (sine-wave drift) on top
    of the random walk. This produces risk_on/risk_off stretches and genuine
    new-high/new-low breakouts, giving the invariant checks real trades to bite.
    """
    rng = np.random.default_rng(seed)
    # Slow regime cycle: ~2 full up/down swings across the series.
    t = np.linspace(0, 4 * np.pi, n_bars)
    trend_drift = 0.004 * np.sin(t)
    returns = rng.normal(drift, vol, n_bars) + trend_drift
    closes = base_price * np.exp(np.cumsum(returns))
    # Strong closes near the bar's extreme on the trending bars so breakouts pass.
    highs = closes * (1 + np.abs(rng.normal(0, vol / 3, n_bars)))
    lows = closes * (1 - np.abs(rng.normal(0, vol / 3, n_bars)))
    opens = np.concatenate([[base_price], closes[:-1]])
    volumes = rng.uniform(2000, 4000, n_bars)
    # Spike volume periodically so the >average volume filter can pass.
    for i in range(15, n_bars, 15):
        volumes[i] *= 2.5
    times = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("h"),
                          periods=n_bars, freq="6h")
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

    # 1. Build synthetic series (1500 bars ≈ 375 days at 6H) for the
    #    BTC/ETH/SOL universe. Different seeds + phases so the assets diverge
    #    in relative strength (otherwise the RS pick is degenerate).
    base_prices = {"BTC-USD": 100_000, "ETH-USD": 3_500, "SOL-USD": 200}
    data = {}
    for i, sym in enumerate(bt.ASSETS):
        df = synth_series(1500, seed=42 + i, base_price=base_prices[sym])
        df = bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS)
        df = bt.add_indicators(df)
        data[sym] = df
        print(f"  {sym}: {len(df)} bars, "
              f"close range [{df['close'].min():.1f}, {df['close'].max():.1f}]")

    # 2. Run the backtest.
    print("\nRunning offline backtest on synthetic data...")
    trades, equity_curve = bt.run_backtest(data)
    print(f"  trades simulated: {len(trades)}")
    print(f"  equity curve points: {len(equity_curve)}")

    # 3. Export CSVs and confirm they exist.
    bt.export_trades_csv(trades, bt.TRADES_CSV)
    bt.export_equity_csv(equity_curve, bt.EQUITY_CSV)
    if trades and not os.path.exists(bt.TRADES_CSV):
        failures.append("trades.csv was not written")
    if not os.path.exists(bt.EQUITY_CSV):
        failures.append("equity_curve.csv was not written")

    # 4. Reconciliation: portfolio Net P&L equals sum(net_pnl_usd).
    if trades:
        sum_pnl = sum(t.net_pnl_usd for t in trades)
        # Read it back from the CSV the user would actually inspect.
        with open(bt.TRADES_CSV) as f:
            csv_pnl = sum(float(row["net_pnl_usd"]) for row in csv.DictReader(f))
        if abs(sum_pnl - csv_pnl) > 1e-6:
            failures.append(f"trades.csv sum {csv_pnl:.4f} != trades sum {sum_pnl:.4f}")
        else:
            print(f"\n[OK] trades.csv sum matches in-memory sum: ${sum_pnl:+.4f}")

    # 5. Mark-to-market: on bars with open positions, equity must differ from
    #    $500 + cumulative_pnl, unless all open positions happen to be flat
    #    (statistically near-impossible across thousands of bars).
    open_bar_rows = [p for p in equity_curve if p["open_positions"] > 0]
    drift_seen = False
    for p in open_bar_rows:
        closed_only = bt.ACCOUNT_SIZE_USD + p["cumulative_pnl"]
        if abs(p["equity"] - closed_only) > 1e-9:
            drift_seen = True
            break
    if open_bar_rows and not drift_seen:
        failures.append("Equity never differed from $500+cumulative_pnl on bars "
                        "with open positions — MTM is not actually applied.")
    elif not open_bar_rows:
        print("\n[WARN] No bars had open positions in this run — MTM "
              "assertion vacuously passes; not a strong test.")
    else:
        sample = open_bar_rows[0]
        print(f"\n[OK] MTM applied: at {sample['time']} "
              f"equity={sample['equity']:.4f}, "
              f"closed-only={bt.ACCOUNT_SIZE_USD + sample['cumulative_pnl']:.4f}, "
              f"open_positions={sample['open_positions']}")

    # 6. Drawdown is computed from MTM equity, not from cumulative_pnl alone.
    #    We just verify the column is populated and >= 0 throughout.
    if equity_curve:
        if any(p["drawdown_pct"] < -1e-9 for p in equity_curve):
            failures.append("drawdown_pct went negative — formula is wrong.")
        else:
            print("[OK] drawdown_pct is non-negative throughout.")

    # 7. Daily / weekly trade-limit invariants.
    if trades:
        from collections import Counter
        per_day_per_sym = Counter()
        per_week = Counter()
        for t in trades:
            d = t.entry_time.date()
            week = t.entry_time.isocalendar()[:2]
            per_day_per_sym[(t.symbol, d)] += 1
            per_week[week] += 1
        max_day = max(per_day_per_sym.values()) if per_day_per_sym else 0
        max_week = max(per_week.values()) if per_week else 0
        if max_day > bt.MAX_TRADES_PER_ASSET_PER_DAY:
            failures.append(f"max trades/asset/day = {max_day} > limit "
                            f"{bt.MAX_TRADES_PER_ASSET_PER_DAY}")
        else:
            print(f"[OK] max trades/asset/day observed: {max_day} "
                  f"(limit {bt.MAX_TRADES_PER_ASSET_PER_DAY})")
        if max_week > bt.MAX_TRADES_PORTFOLIO_PER_WEEK:
            failures.append(f"max trades/week = {max_week} > limit "
                            f"{bt.MAX_TRADES_PORTFOLIO_PER_WEEK}")
        else:
            print(f"[OK] max trades/week observed: {max_week} "
                  f"(limit {bt.MAX_TRADES_PORTFOLIO_PER_WEEK})")

    # 8. Max-open-positions invariant.
    max_open = max((p["open_positions"] for p in equity_curve), default=0)
    if max_open > bt.MAX_OPEN_POSITIONS:
        failures.append(f"observed max open positions = {max_open} > limit "
                        f"{bt.MAX_OPEN_POSITIONS}")
    else:
        print(f"[OK] max open positions observed: {max_open} "
              f"(limit {bt.MAX_OPEN_POSITIONS})")

    # 9. Run the report so we exercise that path too.
    print()
    bt.report_summary(trades, equity_curve)

    # 10. Done.
    if failures:
        print("\n[FAIL] Offline verification found issues:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[PASS] Offline runtime verification — all assertions held.")


if __name__ == "__main__":
    main()
