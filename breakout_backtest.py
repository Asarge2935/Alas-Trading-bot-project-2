"""
Alas Trading Bot — Phase 1: 1D BTC-regime volatility breakout backtester.

Universe: BTC-USD, ETH-USD, SOL-USD  (Phase 1 majors only)
Timeframe: 1D bars (Coinbase Exchange API granularity 86400)
Setup type: "breakout"

Entry:
    Long:
      - BTC close > BTC EMA50 (regime)
      - prior bar's ATR(14) < 0.7 × prior bar's ATR(60) (compression first)
      - close > prior 20-bar high + 0.1 × ATR(14) (breakout with ATR buffer)
    Short: mirror.
    Entry fills at next daily open after the signal candle closes.

    Volume gate (originally `volume >= 1.2 × volume_avg_20`) was DISABLED
    in path B1 (2026-05-09) after the original config produced only 5
    trades over 3 years. See REVIEW_NOTES.md "Phase 1 — B1 relaxation".

Stop & sizing:
    Initial stop = min(low) / max(high) of previous 20 bars (Donchian range edge).
    Skip if stop_distance_pct > 20% (late breakout out of a crazy range).
    Skip if notional_usd < $25 (sub-minimum trade).
    Skip if size_position's own guards fail (stop < 0.5%, margin < $5).

Exits:
    1. Stop hit (initial stop pre-partial; BE-or-chandelier max post-partial).
    2. Target 1 (+2R): close 50%, move stop to entry_price (BE).
       After partial: stop = max(BE, chandelier(3×ATR)) for longs;
                      stop = min(BE, chandelier) for shorts. Ratchets each bar.
    3. Time stop: >= 20 daily bars from entry. Close at bar close.
    No regime-flip exit (chandelier handles reversals).

Risk:
    $5 fixed dollar risk per trade. Max 2 open positions across the universe.
    Max 5 trades / week portfolio. Drawdown 15% pause / 25% stop on MTM equity.
    Cooldown 5 daily bars on a symbol after a clean stop-out (no partial).

Run:
    pip install requests pandas numpy
    python3 breakout_backtest.py

Reuses these helpers from backtest.py (the v2.2 archive):
    safe_get, fetch_candles, drop_incomplete_candles, calculate_fees,
    calculate_slippage, calculate_funding_drag, is_high_vol_bar,
    unrealized_pnl, size_position, export_trades_csv, export_equity_csv.
"""

import csv
import os
from dataclasses import dataclass, asdict
from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd

import backtest as bt


# ---------------------------------------------------------------------------
# Config — Phase 1 strategy constants
# ---------------------------------------------------------------------------

ASSETS = ["BTC-USD", "ETH-USD", "SOL-USD"]
TIMEFRAME_SECONDS = 86400              # 1 day
DAYS_BACK = 1095                        # ~3 years (user's stated minimum)
BARS_PER_DAY = 1

# Trend / regime
EMA_REGIME = 50                         # BTC trend filter

# Compression
ATR_FAST = 14
ATR_SLOW = 60
COMPRESSION_RATIO = 0.7                 # prev bar's atr_14 < 0.7 × atr_60

# Breakout
BREAKOUT_LOOKBACK = 20                  # prior 20-bar high/low
BREAKOUT_BUFFER_ATR = 0.1               # close must clear level by >= 0.1 × ATR(14)
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLIER = 1.2                 # participation confirmation (opposite role from v2.0)

# Exits
PARTIAL_R_MULTIPLE = 2.0                # close 50% at +2R
CHANDELIER_ATR_MULTIPLE = 3.0
TIME_STOP_BARS = 20                     # 20 daily bars

# Skip guards
STOP_DISTANCE_PCT_MAX = 0.20            # skip if stop > 20% from entry
MIN_NOTIONAL_USD = 25.0                 # skip if sized below Coinbase minimum

# Cooldown after a clean stop-out
COOLDOWN_BARS_AFTER_STOP = 5

# Risk / portfolio (matched to backtest.py's constants for consistency)
ACCOUNT_SIZE_USD = bt.ACCOUNT_SIZE_USD
RISK_PER_TRADE_USD = bt.RISK_PER_TRADE_USD
MAX_MARGIN_PER_TRADE = bt.MAX_MARGIN_PER_TRADE
MAX_LEVERAGE = bt.MAX_LEVERAGE
MAX_OPEN_POSITIONS = bt.MAX_OPEN_POSITIONS
MAX_TRADES_PER_ASSET_PER_DAY = bt.MAX_TRADES_PER_ASSET_PER_DAY
MAX_TRADES_PORTFOLIO_PER_WEEK = bt.MAX_TRADES_PORTFOLIO_PER_WEEK

DRAWDOWN_PAUSE_PCT = bt.DRAWDOWN_PAUSE_PCT
DRAWDOWN_STOP_PCT = bt.DRAWDOWN_STOP_PCT
DRAWDOWN_PAUSE_DAYS = bt.DRAWDOWN_PAUSE_DAYS

FUNDING_DRAG_MONTHLY_PCT = bt.FUNDING_DRAG_MONTHLY_PCT

# Output
OUTPUT_DIR = "./breakout_output"
TRADES_CSV = os.path.join(OUTPUT_DIR, "trades.csv")
EQUITY_CSV = os.path.join(OUTPUT_DIR, "equity_curve.csv")


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def _atr(df, period):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def add_indicators_breakout(df):
    """Indicators for the volatility breakout strategy."""
    df = df.copy()

    # BTC regime EMA (only used when df is BTC's frame; harmless on the others)
    df["ema_50"] = df["close"].ewm(span=EMA_REGIME, adjust=False).mean()

    # Compression
    df["atr_14"] = _atr(df, ATR_FAST)
    df["atr_60"] = _atr(df, ATR_SLOW)
    df["compression_ratio"] = df["atr_14"] / df["atr_60"]

    # Donchian breakout levels — explicitly the PRIOR 20 bars (shifted by 1
    # so the current bar can break them).
    df["prior_high_20"] = df["high"].rolling(BREAKOUT_LOOKBACK).max().shift(1)
    df["prior_low_20"] = df["low"].rolling(BREAKOUT_LOOKBACK).min().shift(1)

    # Volume confirmation
    df["volume_avg_20"] = df["volume"].rolling(VOLUME_LOOKBACK).mean()

    return df


# ---------------------------------------------------------------------------
# Trade dataclass — parallel to backtest.Trade, breakout-specific fields
# ---------------------------------------------------------------------------

@dataclass
class BreakoutTrade:
    symbol: str
    side: str                                # "long" / "short"
    setup_type: str                          # "breakout" in Phase 1
    signal_candle_time: pd.Timestamp         # candle that produced the signal
    entry_time: pd.Timestamp                 # next bar open
    entry_price: float

    # Setup snapshot (for audit + future ML / regime analysis)
    breakout_level: float                    # prior 20-bar high (long) / low (short)
    compression_ratio_at_signal: float       # prev bar's atr_14 / atr_60
    atr_at_signal: float                     # ATR(14) at signal bar
    btc_close_at_signal: float
    btc_ema50_at_signal: float

    # Stop / target / sizing
    initial_stop_price: float                # structure stop
    initial_stop_distance: float             # |entry - stop|, used for R-multiple
    target_1_price: float                    # entry +/- 2R
    notional_usd: float
    margin_usd: float
    leverage: int

    # Mutable trail state (updated each bar)
    current_stop_price: float = 0.0
    highest_high_since_entry: float = 0.0    # for long chandelier
    lowest_low_since_entry: float = 0.0      # for short chandelier

    # Exits
    exit_time: Optional[pd.Timestamp] = None
    exit_time_partial: Optional[pd.Timestamp] = None
    exit_price_partial: Optional[float] = None
    exit_price_final: Optional[float] = None
    exit_reason: str = ""
    bars_held: int = 0

    # P&L
    gross_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    slippage_usd: float = 0.0
    funding_usd: float = 0.0
    net_pnl_usd: float = 0.0
    r_multiple: float = 0.0


# ---------------------------------------------------------------------------
# Position sizing — wraps backtest.size_position with the new skip guards
# ---------------------------------------------------------------------------

def size_position_with_guards(entry_price, stop_price):
    """
    Apply Phase 1 skip guards on top of the standard risk/stop sizing:
      1. Skip if stop_distance_pct > 20% (late breakout from absurd range).
      2. Run backtest.size_position (which also enforces stop_pct >= 0.5%
         and margin >= $5).
      3. Skip if resulting notional < MIN_NOTIONAL_USD ($25).
    """
    stop_distance = abs(entry_price - stop_price)
    if entry_price <= 0:
        return None
    stop_distance_pct = stop_distance / entry_price
    if stop_distance_pct > STOP_DISTANCE_PCT_MAX:
        return None

    sizing = bt.size_position(entry_price, stop_price)
    if sizing is None:
        return None
    notional, margin, leverage = sizing
    if notional < MIN_NOTIONAL_USD:
        return None
    return notional, margin, leverage


# ---------------------------------------------------------------------------
# Signal evaluation
# ---------------------------------------------------------------------------

def evaluate_breakout_signal(row, prev_row, btc_row):
    """
    Return "long" / "short" / None for a closed bar.

    Pre-conditions (NaN guard): all needed indicator columns must be populated
    on both the current and prior row.

    NOTE — volume gate disabled (path B1, 2026-05-09). The first 3-year
    breakout backtest produced only 5 trades; the conjunction of compression
    + breakout + 1.2x volume + BTC regime fired ~0.2% of bar-asset-pairs,
    too rare to evaluate edge. The volume gate was the highest-impact
    single relaxation. The constant VOLUME_MULTIPLIER and the
    volume_avg_20 indicator column are retained for traceability and in
    case the gate is reinstated.
    """
    required_current = [
        "close", "atr_14", "prior_high_20", "prior_low_20",
    ]
    required_prev = ["compression_ratio"]
    required_btc = ["close", "ema_50"]

    for col in required_current:
        if pd.isna(row[col]):
            return None
    for col in required_prev:
        if pd.isna(prev_row[col]):
            return None
    if btc_row is None:
        return None
    for col in required_btc:
        if pd.isna(btc_row[col]):
            return None

    # 1. Compression on the bar BEFORE the breakout.
    if prev_row["compression_ratio"] >= COMPRESSION_RATIO:
        return None

    # 2. Volume confirmation — DISABLED in B1. See docstring.

    btc_above = btc_row["close"] > btc_row["ema_50"]
    btc_below = btc_row["close"] < btc_row["ema_50"]

    # 3. Breakout with ATR buffer.
    long_break = row["close"] > row["prior_high_20"] + BREAKOUT_BUFFER_ATR * row["atr_14"]
    short_break = row["close"] < row["prior_low_20"] - BREAKOUT_BUFFER_ATR * row["atr_14"]

    long_ok = btc_above and long_break
    short_ok = btc_below and short_break

    if long_ok and short_ok:
        return None  # ambiguous (shouldn't really happen but be safe)
    if long_ok:
        return "long"
    if short_ok:
        return "short"
    return None


def breakout_strength(row, side):
    """
    How far past the level did we close, in ATR units? Used to rank multiple
    breakouts on the same bar across the universe.
    """
    atr = row["atr_14"]
    if pd.isna(atr) or atr <= 0:
        return 0.0
    if side == "long":
        return (row["close"] - row["prior_high_20"]) / atr
    return (row["prior_low_20"] - row["close"]) / atr


# ---------------------------------------------------------------------------
# Exit logic
# ---------------------------------------------------------------------------

def check_exit_breakout(trade, bar, current_time):
    """
    Exit priority:
      1. Current stop hit (initial structure stop pre-partial; BE-or-chandelier
         max post-partial). Conservative: stop fires first when both stop and
         target prices fall inside one bar's range.
      2. Target 1 (+2R) hit pre-partial: record partial, stop -> BE, runner
         continues. The runner is NOT also evaluated on the same bar.
      3. Time stop: bars_held >= 20.

    The chandelier trail ratchet is applied AFTER this function returns None
    (i.e., we don't update it on the same bar we exited on). See
    `update_chandelier_after_bar` in run_breakout_backtest.
    """
    bars_held_so_far = (current_time - trade.entry_time).total_seconds() / TIMEFRAME_SECONDS
    if bars_held_so_far < 0:
        return None
    is_high_vol = bt.is_high_vol_bar(bar)
    has_partial = trade.exit_price_partial is not None

    if trade.side == "long":
        # 1. stop
        if bar["low"] <= trade.current_stop_price:
            return {"exit_time": current_time, "exit_price": trade.current_stop_price,
                    "reason": "stop_hit", "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}
        # 2. target_1
        if not has_partial and bar["high"] >= trade.target_1_price:
            trade.exit_price_partial = trade.target_1_price
            trade.exit_time_partial = current_time
            trade.current_stop_price = trade.entry_price  # BE
            return None
    else:  # short
        if bar["high"] >= trade.current_stop_price:
            return {"exit_time": current_time, "exit_price": trade.current_stop_price,
                    "reason": "stop_hit", "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}
        if not has_partial and bar["low"] <= trade.target_1_price:
            trade.exit_price_partial = trade.target_1_price
            trade.exit_time_partial = current_time
            trade.current_stop_price = trade.entry_price
            return None

    # 3. time stop
    if bars_held_so_far >= TIME_STOP_BARS:
        return {"exit_time": current_time, "exit_price": bar["close"],
                "reason": "time_stop", "bars_held": int(bars_held_so_far),
                "is_high_vol": is_high_vol}

    return None


def update_chandelier_after_bar(trade, bar):
    """
    Ratchet trail state on every bar AFTER target-1 has fired. The runner's
    stop is max(BE, chandelier) for longs, min(BE, chandelier) for shorts.
    Pre-partial, this is a no-op (initial structure stop holds).
    """
    if trade.exit_price_partial is None:
        return
    atr = bar["atr_14"]
    if pd.isna(atr) or atr <= 0:
        return

    if trade.side == "long":
        trade.highest_high_since_entry = max(trade.highest_high_since_entry, bar["high"])
        chandelier = trade.highest_high_since_entry - CHANDELIER_ATR_MULTIPLE * atr
        # BE is the floor; chandelier ratchets ABOVE it as profit accumulates.
        new_stop = max(trade.entry_price, chandelier)
        # Stops never move backward (against the position).
        trade.current_stop_price = max(trade.current_stop_price, new_stop)
    else:
        trade.lowest_low_since_entry = min(trade.lowest_low_since_entry, bar["low"])
        chandelier = trade.lowest_low_since_entry + CHANDELIER_ATR_MULTIPLE * atr
        new_stop = min(trade.entry_price, chandelier)
        trade.current_stop_price = min(trade.current_stop_price, new_stop)


# ---------------------------------------------------------------------------
# Trade finalization (P&L, fees, slippage, funding, R-multiple)
# ---------------------------------------------------------------------------

def finalize_breakout_trade(trade, exit_event):
    trade.exit_time = exit_event["exit_time"]
    trade.exit_price_final = exit_event["exit_price"]
    trade.exit_reason = exit_event["reason"]
    trade.bars_held = exit_event["bars_held"]

    if trade.exit_price_partial is not None:
        if trade.side == "long":
            move_partial = trade.exit_price_partial - trade.entry_price
            move_final = trade.exit_price_final - trade.entry_price
        else:
            move_partial = trade.entry_price - trade.exit_price_partial
            move_final = trade.entry_price - trade.exit_price_final

        gross_partial = (move_partial / trade.entry_price) * (trade.notional_usd / 2)
        gross_final = (move_final / trade.entry_price) * (trade.notional_usd / 2)
        trade.gross_pnl_usd = gross_partial + gross_final

        trade.fees_usd = (
            bt.calculate_fees(trade.notional_usd)
            + bt.calculate_fees(trade.notional_usd / 2)
            + bt.calculate_fees(trade.notional_usd / 2)
        )
    else:
        if trade.side == "long":
            move = trade.exit_price_final - trade.entry_price
        else:
            move = trade.entry_price - trade.exit_price_final
        trade.gross_pnl_usd = (move / trade.entry_price) * trade.notional_usd
        trade.fees_usd = bt.calculate_fees(trade.notional_usd) * 2

    is_high_vol = exit_event.get("is_high_vol", False)
    if trade.exit_price_partial is not None:
        trade.slippage_usd = (
            bt.calculate_slippage(trade.notional_usd, is_high_vol)
            + bt.calculate_slippage(trade.notional_usd / 2, is_high_vol)
            + bt.calculate_slippage(trade.notional_usd / 2, is_high_vol)
        )
    else:
        trade.slippage_usd = bt.calculate_slippage(trade.notional_usd, is_high_vol) * 2

    # Partial-aware funding drag (full notional pre-partial, half post).
    monthly_to_daily = FUNDING_DRAG_MONTHLY_PCT / 30
    if trade.exit_time_partial is None:
        days_held = trade.bars_held / BARS_PER_DAY
        trade.funding_usd = trade.notional_usd * monthly_to_daily * days_held
    else:
        bars_before = (trade.exit_time_partial - trade.entry_time).total_seconds() / TIMEFRAME_SECONDS
        bars_after = (trade.exit_time - trade.exit_time_partial).total_seconds() / TIMEFRAME_SECONDS
        days_before = bars_before / BARS_PER_DAY
        days_after = bars_after / BARS_PER_DAY
        trade.funding_usd = (
            trade.notional_usd * monthly_to_daily * days_before
            + (trade.notional_usd / 2) * monthly_to_daily * days_after
        )

    trade.net_pnl_usd = (
        trade.gross_pnl_usd - trade.fees_usd - trade.slippage_usd - trade.funding_usd
    )

    one_r_dollars = (trade.initial_stop_distance / trade.entry_price) * trade.notional_usd
    trade.r_multiple = trade.net_pnl_usd / one_r_dollars if one_r_dollars > 0 else 0.0


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

def run_breakout_backtest(data_by_symbol):
    common_times = None
    for sym, df in data_by_symbol.items():
        times = set(df["time"])
        common_times = times if common_times is None else common_times & times
    common_times = sorted(common_times)

    indexed = {sym: df.set_index("time") for sym, df in data_by_symbol.items()}
    if "BTC-USD" not in indexed:
        raise RuntimeError("BTC-USD frame is required for the regime filter.")
    btc_df = indexed["BTC-USD"]

    open_trades = {}
    closed_trades = []
    equity_curve = []
    cumulative_pnl = 0.0
    peak_account_value = ACCOUNT_SIZE_USD
    drawdown_pct_from_peak = 0.0

    trades_today = {sym: 0 for sym in ASSETS}
    last_day = None
    weekly_trade_times = []
    drawdown_pause_until = None

    # Cooldown: last clean stop-out time per symbol (None = no cooldown active).
    last_clean_stop_time = {sym: None for sym in ASSETS}

    for current_time in common_times:
        current_day = current_time.date()
        if last_day is None or current_day != last_day:
            trades_today = {sym: 0 for sym in ASSETS}
            last_day = current_day
        weekly_trade_times = [t for t in weekly_trade_times if (current_time - t).days < 7]

        # 1. Manage open positions: check exits, then ratchet chandelier on survivors.
        for sym in list(open_trades.keys()):
            trade = open_trades[sym]
            bar = indexed[sym].loc[current_time]
            exit_event = check_exit_breakout(trade, bar, current_time)
            if exit_event:
                finalize_breakout_trade(trade, exit_event)
                closed_trades.append(trade)
                cumulative_pnl += trade.net_pnl_usd
                # Cooldown only fires on a CLEAN stop-out (no partial).
                if exit_event["reason"] == "stop_hit" and trade.exit_price_partial is None:
                    last_clean_stop_time[sym] = current_time
                del open_trades[sym]
            else:
                update_chandelier_after_bar(trade, bar)

        # 2. Mark-to-market equity for drawdown breakers.
        open_pnl = 0.0
        for sym, trade in open_trades.items():
            bar = indexed[sym].loc[current_time]
            open_pnl += bt.unrealized_pnl(trade, bar["close"])
        mark_to_market_equity = ACCOUNT_SIZE_USD + cumulative_pnl + open_pnl

        if mark_to_market_equity > peak_account_value:
            peak_account_value = mark_to_market_equity
        drawdown_pct_from_peak = (
            (peak_account_value - mark_to_market_equity) / peak_account_value * 100
            if peak_account_value > 0 else 0.0
        )

        if drawdown_pct_from_peak >= DRAWDOWN_STOP_PCT:
            print(f"DRAWDOWN STOP HIT at {current_time}: {drawdown_pct_from_peak:.1f}%")
            equity_curve.append({
                "time": current_time,
                "equity": mark_to_market_equity,
                "cumulative_pnl": cumulative_pnl,
                "open_positions": len(open_trades),
                "drawdown_pct": drawdown_pct_from_peak,
            })
            return closed_trades, equity_curve
        if drawdown_pct_from_peak >= DRAWDOWN_PAUSE_PCT and drawdown_pause_until is None:
            drawdown_pause_until = current_time + timedelta(days=DRAWDOWN_PAUSE_DAYS)
            print(f"DRAWDOWN PAUSE at {current_time}: {drawdown_pct_from_peak:.1f}% "
                  f"— paused until {drawdown_pause_until.date()}")

        equity_curve.append({
            "time": current_time,
            "equity": mark_to_market_equity,
            "cumulative_pnl": cumulative_pnl,
            "open_positions": len(open_trades),
            "drawdown_pct": drawdown_pct_from_peak,
        })

        # 3. Entry eligibility gates.
        if drawdown_pause_until and current_time < drawdown_pause_until:
            continue
        if drawdown_pause_until and current_time >= drawdown_pause_until:
            drawdown_pause_until = None

        if len(open_trades) >= MAX_OPEN_POSITIONS:
            continue
        if len(weekly_trade_times) >= MAX_TRADES_PORTFOLIO_PER_WEEK:
            continue

        # 4. Scan all assets for qualifying breakouts on this bar.
        candidates = []
        sym_dfs_indexed = {sym: indexed[sym] for sym in ASSETS}
        for sym in ASSETS:
            if sym in open_trades:
                continue
            if trades_today[sym] >= MAX_TRADES_PER_ASSET_PER_DAY:
                continue
            # Cooldown after a clean stop on this symbol.
            cooldown_start = last_clean_stop_time.get(sym)
            if cooldown_start is not None:
                bars_since_stop = (current_time - cooldown_start).total_seconds() / TIMEFRAME_SECONDS
                if bars_since_stop < COOLDOWN_BARS_AFTER_STOP:
                    continue

            sym_df = sym_dfs_indexed[sym].reset_index()
            row_idxs = sym_df.index[sym_df["time"] == current_time].tolist()
            if not row_idxs or row_idxs[0] == 0:
                continue
            cur_idx = row_idxs[0]
            row = sym_df.iloc[cur_idx]
            prev_row = sym_df.iloc[cur_idx - 1]
            btc_row = btc_df.loc[current_time] if current_time in btc_df.index else None
            side = evaluate_breakout_signal(row, prev_row, btc_row)
            if side:
                strength = breakout_strength(row, side)
                candidates.append((sym, side, strength, row, prev_row, btc_row, sym_df, cur_idx))

        if not candidates:
            continue

        # 5. Pick the strongest breakout (most ATR-units past the level).
        candidates.sort(key=lambda c: c[2], reverse=True)
        sym, side, strength, row, prev_row, btc_row, sym_df, cur_idx = candidates[0]

        # 6. Resolve next-bar entry — must exist in this asset's frame.
        if cur_idx + 1 >= len(sym_df):
            continue
        next_bar = sym_df.iloc[cur_idx + 1]
        entry_price = float(next_bar["open"])
        entry_time = next_bar["time"]

        # 7. Structure stop = bottom (long) / top (short) of the prior 20-bar range.
        if side == "long":
            stop_price = float(row["prior_low_20"])
            target_1 = entry_price + PARTIAL_R_MULTIPLE * abs(entry_price - stop_price)
            breakout_level = float(row["prior_high_20"])
        else:
            stop_price = float(row["prior_high_20"])
            target_1 = entry_price - PARTIAL_R_MULTIPLE * abs(entry_price - stop_price)
            breakout_level = float(row["prior_low_20"])

        # 8. Sizing with all skip guards.
        sizing = size_position_with_guards(entry_price, stop_price)
        if sizing is None:
            continue
        notional, margin, leverage = sizing

        # 9. Build the trade.
        atr_at_signal = float(row["atr_14"])
        compression_at_signal = float(prev_row["compression_ratio"])
        btc_close = float(btc_row["close"])
        btc_ema50 = float(btc_row["ema_50"])

        trade = BreakoutTrade(
            symbol=sym, side=side, setup_type="breakout",
            signal_candle_time=current_time, entry_time=entry_time,
            entry_price=entry_price,
            breakout_level=breakout_level,
            compression_ratio_at_signal=compression_at_signal,
            atr_at_signal=atr_at_signal,
            btc_close_at_signal=btc_close, btc_ema50_at_signal=btc_ema50,
            initial_stop_price=stop_price,
            initial_stop_distance=abs(entry_price - stop_price),
            target_1_price=target_1,
            notional_usd=notional, margin_usd=margin, leverage=leverage,
            current_stop_price=stop_price,
            highest_high_since_entry=entry_price,
            lowest_low_since_entry=entry_price,
        )
        open_trades[sym] = trade
        trades_today[sym] += 1
        weekly_trade_times.append(current_time)

    # Mark-to-market any still-open trades at the last bar.
    for sym, trade in open_trades.items():
        last_bar = indexed[sym].iloc[-1]
        bars_held = (last_bar.name - trade.entry_time).total_seconds() / TIMEFRAME_SECONDS
        finalize_breakout_trade(trade, {
            "exit_time": last_bar.name, "exit_price": float(last_bar["close"]),
            "reason": "backtest_end", "bars_held": int(bars_held),
            "is_high_vol": bt.is_high_vol_bar(last_bar),
        })
        closed_trades.append(trade)
        cumulative_pnl += trade.net_pnl_usd

    return closed_trades, equity_curve


# ---------------------------------------------------------------------------
# CSV exporters and reporting
# ---------------------------------------------------------------------------

def export_breakout_trades_csv(trades, path):
    """Like backtest.export_trades_csv but for the BreakoutTrade dataclass."""
    if not trades:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()))
        writer.writeheader()
        for t in trades:
            row = asdict(t)
            for k, v in row.items():
                if isinstance(v, pd.Timestamp):
                    row[k] = v.isoformat()
            writer.writerow(row)
    print(f"Exported {len(trades)} trades to {path}")


def report_summary_breakout(trades, equity_curve):
    if not trades:
        print("\nNo trades executed in this period.")
        return

    by_bucket = {}
    for t in trades:
        for bucket_name in [
            "ALL", t.symbol, f"{t.symbol}_{t.side}", f"ALL_{t.side}",
            f"setup_{t.setup_type}",
        ]:
            by_bucket.setdefault(bucket_name, []).append(t)

    print("\n" + "=" * 70)
    print(f"BREAKOUT BACKTEST SUMMARY ({DAYS_BACK} days, 1D bars)")
    print("=" * 70)

    for bucket, bucket_trades in sorted(by_bucket.items()):
        if not bucket_trades:
            continue
        wins = [t for t in bucket_trades if t.net_pnl_usd > 0]
        losses = [t for t in bucket_trades if t.net_pnl_usd <= 0]
        win_rate = len(wins) / len(bucket_trades) * 100
        gross_win = sum(t.net_pnl_usd for t in wins)
        gross_loss = abs(sum(t.net_pnl_usd for t in losses))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        avg_r = float(np.mean([t.r_multiple for t in bucket_trades]))
        total_pnl = sum(t.net_pnl_usd for t in bucket_trades)
        total_gross = sum(t.gross_pnl_usd for t in bucket_trades)
        total_fees = sum(t.fees_usd for t in bucket_trades)
        total_slip = sum(t.slippage_usd for t in bucket_trades)
        total_fund = sum(t.funding_usd for t in bucket_trades)

        print(f"\n--- {bucket} ---")
        print(f"  Trades:              {len(bucket_trades)}")
        print(f"  Win rate:            {win_rate:.1f}%   ({len(wins)}W / {len(losses)}L)")
        print(f"  Profit factor:       {pf:.2f}")
        print(f"  Avg R-multiple:      {avg_r:+.2f}R")
        print(f"  Gross P&L:           ${total_gross:+.2f}")
        print(f"  Fees:               -${total_fees:.2f}")
        print(f"  Slippage:           -${total_slip:.2f}")
        print(f"  Funding drag:       -${total_fund:.2f}")
        print(f"  Net P&L:             ${total_pnl:+.2f}")

    final_pnl = sum(t.net_pnl_usd for t in trades)
    final_equity = ACCOUNT_SIZE_USD + final_pnl
    max_dd = max((p["drawdown_pct"] for p in equity_curve), default=0.0)

    # Single-asset concentration check (Phase 1 validation gate).
    by_asset = {}
    for t in trades:
        by_asset.setdefault(t.symbol, 0.0)
        by_asset[t.symbol] += t.net_pnl_usd
    abs_total = sum(abs(v) for v in by_asset.values())
    asset_share = {sym: (val / abs_total * 100 if abs_total > 0 else 0.0) for sym, val in by_asset.items()}
    max_share_sym = max(asset_share, key=asset_share.get) if asset_share else None
    max_share_pct = max(asset_share.values()) if asset_share else 0.0

    print(f"\n--- PORTFOLIO ---")
    print(f"  Starting capital:    ${ACCOUNT_SIZE_USD:.2f}")
    print(f"  Final equity:        ${final_equity:.2f}")
    print(f"  Net P&L:             ${final_pnl:+.2f}")
    print(f"  Return on capital:   {final_pnl / ACCOUNT_SIZE_USD * 100:+.2f}%")
    print(f"  Max drawdown:        {max_dd:.2f}%   (mark-to-market)")
    if max_share_sym is not None:
        print(f"  Top-asset concentration: {max_share_sym} = {max_share_pct:.1f}% of |net P&L|")

    trades_sorted = sorted(trades, key=lambda t: t.exit_time or t.entry_time)
    streak = 0
    max_loss_streak = 0
    for t in trades_sorted:
        if t.net_pnl_usd <= 0:
            streak += 1
            max_loss_streak = max(max_loss_streak, streak)
        else:
            streak = 0
    print(f"  Max consecutive losses: {max_loss_streak}")

    print("\n" + "=" * 70)
    print("PHASE 1 VALIDATION GATES (user-stated)")
    print("=" * 70)
    print(f"  PF >= 1.3:                {'PASS' if (pf := _portfolio_pf(trades)) >= 1.3 else 'FAIL'}  (actual {pf:.2f})")
    print(f"  Avg R > 0:                {'PASS' if (avg_r := float(np.mean([t.r_multiple for t in trades]))) > 0 else 'FAIL'}  (actual {avg_r:+.2f}R)")
    print(f"  No asset > 50% of |P&L|:  {'PASS' if max_share_pct < 50.0 else 'FAIL'}  (top: {max_share_sym} = {max_share_pct:.1f}%)")
    print(f"  Trade count >= 30:        {'PASS' if len(trades) >= 30 else 'FAIL'}  (actual {len(trades)})")
    print(f"  Max consec losses < 8:    {'PASS' if max_loss_streak < 8 else 'FAIL'}  (actual {max_loss_streak})")
    print()


def _portfolio_pf(trades):
    wins = [t.net_pnl_usd for t in trades if t.net_pnl_usd > 0]
    losses = [t.net_pnl_usd for t in trades if t.net_pnl_usd <= 0]
    gw = sum(wins)
    gl = abs(sum(losses))
    return gw / gl if gl > 0 else float("inf")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Phase 1 breakout backtest: {DAYS_BACK} days, 1D bars, {len(ASSETS)} assets")
    print(f"Universe: {', '.join(ASSETS)}")
    print(f"Setup: BTC-regime volatility breakout (compression -> expansion + volume)")
    print(f"Risk: ${RISK_PER_TRADE_USD:.0f}/trade, max {MAX_OPEN_POSITIONS} open, "
          f"max {MAX_TRADES_PORTFOLIO_PER_WEEK}/week")
    print()

    data = {}
    for symbol in ASSETS:
        print(f"Fetching {symbol} ...", end=" ", flush=True)
        df = bt.fetch_candles(symbol, TIMEFRAME_SECONDS, DAYS_BACK)
        raw_count = len(df)
        df = bt.drop_incomplete_candles(df, TIMEFRAME_SECONDS)
        if df.empty:
            print("0 bars — skipping this symbol")
            continue
        dropped = raw_count - len(df)
        df = add_indicators_breakout(df)
        data[symbol] = df
        suffix = f" (dropped {dropped} incomplete)" if dropped else ""
        print(f"{len(df)} bars ({df['time'].min().date()} to {df['time'].max().date()}){suffix}")

    if "BTC-USD" not in data:
        print("ERROR: BTC-USD data is required for the regime filter — aborting.")
        return

    print("\nRunning breakout backtest...")
    trades, equity_curve = run_breakout_backtest(data)
    print(f"Backtest complete. {len(trades)} trades simulated.")

    export_breakout_trades_csv(trades, TRADES_CSV)
    # Equity curve format matches v2.2; safe to reuse the v2.2 exporter.
    bt.export_equity_csv(equity_curve, EQUITY_CSV)
    report_summary_breakout(trades, equity_curve)


if __name__ == "__main__":
    main()
