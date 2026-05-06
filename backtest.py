"""
Alas Trading Bot — Multi-asset 6H scanner backtester for Coinbase perps.

Universe: BTC, ETH, SOL, XRP, ADA, DOT
Timeframe: 6H bars (Coinbase Exchange API granularity 21600)
Mode: Scanner — every closed 6H bar, evaluate all 6 assets, take the strongest
      qualifying signal subject to portfolio limits.

This is the source of truth for the BACKTEST. Live execution is a separate
adapter and is intentionally out of scope here.

Note on tickers: rules.json lists the live perp universe as BTC-PERP-INTX etc.
The Coinbase Exchange public candles endpoint serves spot only, so this
backtester uses BTC-USD, ETH-USD, ... as a price proxy for the perp series.
The spot/perp basis is small at 6H resolution and is partly absorbed by the
slippage assumption. Document this explicitly when interpreting results.

Run:
    pip install requests pandas numpy
    python backtest.py

Outputs:
    backtest_output/trades.csv
    backtest_output/equity_curve.csv
    Console summary report
"""

import csv
import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Config — locked to rules.json. Do not tweak without updating both.
# ---------------------------------------------------------------------------

ASSETS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD", "DOT-USD"]
TIMEFRAME_SECONDS = 21600
DAYS_BACK = 365
BARS_PER_DAY = 86400 // TIMEFRAME_SECONDS

EMA_SLOW = 50
EMA_FAST = 20
RSI_PERIOD = 14
ATR_PERIOD = 14
VOL_AVG_PERIOD = 20
ATR_REGIME_PERIOD = 120          # 30 days at 6H

RSI_LONG_MAX = 30
RSI_SHORT_MIN = 70
ATR_REGIME_MULTIPLE = 2.0
VOLUME_MULTIPLIER = 1.2

BTC_REGIME_RSI_LOW = 35
BTC_REGIME_RSI_HIGH = 65

ACCOUNT_SIZE_USD = 500.0
RISK_PER_TRADE_USD = 5.0
MAX_MARGIN_PER_TRADE = 50.0
MAX_LEVERAGE = 2
MAX_OPEN_POSITIONS = 2
TIME_STOP_BARS = 56              # 14 days at 6H

MAX_TRADES_PER_ASSET_PER_DAY = 2
MAX_TRADES_PORTFOLIO_PER_WEEK = 5

TAKER_FEE_PCT = 0.0003
MIN_FEE_USD = 0.15
SLIPPAGE_NORMAL_PCT = 0.0010
SLIPPAGE_HIGH_VOL_PCT = 0.0020
FUNDING_DRAG_MONTHLY_PCT = 0.005

DRAWDOWN_PAUSE_PCT = 15.0
DRAWDOWN_STOP_PCT = 25.0
DRAWDOWN_PAUSE_DAYS = 7

OUTPUT_DIR = "./backtest_output"
TRADES_CSV = os.path.join(OUTPUT_DIR, "trades.csv")
EQUITY_CSV = os.path.join(OUTPUT_DIR, "equity_curve.csv")


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

USER_AGENT = "AlasTradingBotBacktester/1.0"


def safe_get(url, params, max_retries=5):
    """HTTP GET with rate-limit backoff and bounded retries."""
    headers = {"User-Agent": USER_AGENT}
    last_response = None

    for attempt in range(max_retries):
        r = requests.get(url, params=params, headers=headers, timeout=15)
        last_response = r

        if r.status_code == 429:
            sleep_time = 0.5 * (2 ** attempt)
            time.sleep(sleep_time)
            continue

        r.raise_for_status()
        return r

    last_response.raise_for_status()
    return last_response


def fetch_candles(product_id, granularity_seconds, days_back):
    """Pull historical 6H candles from Coinbase Exchange public API, paginated."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    all_candles = []
    cursor = end

    while cursor > start:
        chunk_start = cursor - timedelta(seconds=granularity_seconds * 300)
        if chunk_start < start:
            chunk_start = start

        params = {
            "start": chunk_start.isoformat(),
            "end": cursor.isoformat(),
            "granularity": granularity_seconds,
        }
        url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
        r = safe_get(url, params)
        chunk = r.json()
        all_candles.extend(chunk)
        if not chunk:
            break
        cursor = chunk_start
        time.sleep(0.3)

    df = pd.DataFrame(
        all_candles,
        columns=["time", "low", "high", "open", "close", "volume"],
    )
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def drop_incomplete_candles(df, timeframe_seconds):
    """Drop the latest still-forming candle(s) so the backtester only evaluates closed bars."""
    now = pd.Timestamp.now(tz="UTC")
    candle_close_time = df["time"] + pd.Timedelta(seconds=timeframe_seconds)
    return df[candle_close_time <= now].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def add_indicators(df):
    df = df.copy()
    df["ema_50"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["ema_20"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()

    # RSI with explicit endpoint handling so strong one-sided runs don't produce NaN:
    #   loss == 0 (pure up-move)   -> RSI = 100
    #   gain == 0 (pure down-move) -> RSI = 0
    #   gain == 0 and loss == 0    -> RSI = 50 (flat)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(window=RSI_PERIOD).mean()
    loss = (-delta.clip(upper=0)).rolling(window=RSI_PERIOD).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(loss != 0, 100)
    rsi = rsi.where(gain != 0, 0)
    rsi = rsi.where(~((gain == 0) & (loss == 0)), 50)
    df["rsi_14"] = rsi

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(window=ATR_PERIOD).mean()
    df["atr_regime_avg"] = df["atr_14"].rolling(window=ATR_REGIME_PERIOD).mean()

    df["volume_avg_20"] = df["volume"].rolling(window=VOL_AVG_PERIOD).mean()
    return df


# ---------------------------------------------------------------------------
# Trade dataclass
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    side: str
    # Coinbase candle "time" is the candle START. signal_candle_time is the
    # start time of the bar that produced the signal; the signal is only
    # actionable AFTER the bar closes (i.e., signal_candle_time + TIMEFRAME).
    signal_candle_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    target_1_price: float
    target_2_price: float
    initial_stop_distance: float
    notional_usd: float
    margin_usd: float
    leverage: int
    rsi_at_signal: float
    atr_at_signal: float
    btc_rsi_at_signal: float

    exit_time: Optional[pd.Timestamp] = None
    exit_time_partial: Optional[pd.Timestamp] = None
    exit_price_partial: Optional[float] = None
    exit_price_final: Optional[float] = None
    exit_reason: str = ""
    bars_held: int = 0
    gross_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    slippage_usd: float = 0.0
    funding_usd: float = 0.0
    net_pnl_usd: float = 0.0
    r_multiple: float = 0.0


# ---------------------------------------------------------------------------
# Cost modeling
# ---------------------------------------------------------------------------

def calculate_fees(notional_usd):
    pct_fee = notional_usd * TAKER_FEE_PCT
    return max(pct_fee, MIN_FEE_USD)


def calculate_slippage(notional_usd, is_high_vol):
    pct = SLIPPAGE_HIGH_VOL_PCT if is_high_vol else SLIPPAGE_NORMAL_PCT
    return notional_usd * pct


def calculate_funding_drag(notional_usd, bars_held):
    days_held = bars_held / BARS_PER_DAY
    monthly_to_daily = FUNDING_DRAG_MONTHLY_PCT / 30
    return notional_usd * monthly_to_daily * days_held


def calculate_trade_funding_drag(trade):
    """
    Funding drag that respects the partial close: full notional before target 1,
    half notional after. Falls back to the simple notional × days_held when the
    trade exited without a partial fill.
    """
    monthly_to_daily = FUNDING_DRAG_MONTHLY_PCT / 30

    if trade.exit_time_partial is None:
        days_held = trade.bars_held / BARS_PER_DAY
        return trade.notional_usd * monthly_to_daily * days_held

    bars_before_partial = (
        trade.exit_time_partial - trade.entry_time
    ).total_seconds() / TIMEFRAME_SECONDS
    bars_after_partial = (
        trade.exit_time - trade.exit_time_partial
    ).total_seconds() / TIMEFRAME_SECONDS

    days_before = bars_before_partial / BARS_PER_DAY
    days_after = bars_after_partial / BARS_PER_DAY

    funding_before = trade.notional_usd * monthly_to_daily * days_before
    funding_after = (trade.notional_usd / 2) * monthly_to_daily * days_after

    return funding_before + funding_after


def is_high_vol_bar(bar):
    """Flag a bar as high-volatility when its range exceeds 2 × ATR(14)."""
    atr = bar.get("atr_14") if hasattr(bar, "get") else None
    if atr is None or pd.isna(atr):
        return False
    bar_range = bar["high"] - bar["low"]
    return bar_range > 2 * atr


def unrealized_pnl(trade, current_price):
    """
    Mark-to-market P&L on the OPEN portion of the trade. After a partial close
    at target 1, only half the original notional is still open.
    """
    if trade.side == "long":
        move = current_price - trade.entry_price
    else:
        move = trade.entry_price - current_price

    open_notional = trade.notional_usd
    if trade.exit_price_partial is not None:
        open_notional = trade.notional_usd / 2

    return (move / trade.entry_price) * open_notional


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def size_position(entry_price, stop_price):
    """notional = risk / stop_pct, capped at max_notional, margin = notional / leverage."""
    stop_distance = abs(entry_price - stop_price)
    stop_distance_pct = stop_distance / entry_price

    if stop_distance_pct < 0.005:
        return None

    notional_usd = RISK_PER_TRADE_USD / stop_distance_pct
    max_notional = MAX_MARGIN_PER_TRADE * MAX_LEVERAGE
    notional_usd = min(notional_usd, max_notional)
    margin_usd = notional_usd / MAX_LEVERAGE

    if margin_usd < 5:
        return None

    return notional_usd, margin_usd, MAX_LEVERAGE


# ---------------------------------------------------------------------------
# Signal evaluation
# ---------------------------------------------------------------------------

def evaluate_signal(row, btc_row):
    required = ["ema_50", "ema_20", "rsi_14", "atr_14", "atr_regime_avg",
                "volume_avg_20"]
    for col in required:
        if pd.isna(row[col]):
            return None

    if row["atr_14"] > ATR_REGIME_MULTIPLE * row["atr_regime_avg"]:
        return None
    if row["volume"] < VOLUME_MULTIPLIER * row["volume_avg_20"]:
        return None

    # Treat missing/NaN BTC RSI as neutral (50) — never block trades because BTC bar is unavailable
    if btc_row is None or pd.isna(btc_row["rsi_14"]):
        btc_rsi = 50.0
    else:
        btc_rsi = btc_row["rsi_14"]
    btc_extreme_low = btc_rsi < BTC_REGIME_RSI_LOW
    btc_extreme_high = btc_rsi > BTC_REGIME_RSI_HIGH

    long_ok = (
        row["close"] > row["ema_50"]
        and row["ema_20"] > row["ema_50"]
        and row["rsi_14"] < RSI_LONG_MAX
        and not btc_extreme_low
    )
    short_ok = (
        row["close"] < row["ema_50"]
        and row["ema_20"] < row["ema_50"]
        and row["rsi_14"] > RSI_SHORT_MIN
        and not btc_extreme_high
    )

    if long_ok and short_ok:
        return None
    if long_ok:
        return "long"
    if short_ok:
        return "short"
    return None


def signal_strength_from_rsi(rsi):
    """RSI distance from 50. Used to rank multiple simultaneous signals."""
    return abs(rsi - 50)


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

def run_backtest(data_by_symbol):
    common_times = None
    for sym, df in data_by_symbol.items():
        times = set(df["time"])
        common_times = times if common_times is None else common_times & times
    common_times = sorted(common_times)

    indexed = {sym: df.set_index("time") for sym, df in data_by_symbol.items()}
    btc_df = indexed["BTC-USD"]

    open_trades = {}
    closed_trades = []
    equity_curve = []
    cumulative_pnl = 0.0
    # Drawdown is measured against ACCOUNT_SIZE_USD baseline so early losses
    # before the first winner are correctly tracked. Fixed in this rebuild.
    peak_account_value = ACCOUNT_SIZE_USD
    drawdown_pct_from_peak = 0.0

    trades_today = {sym: 0 for sym in ASSETS}
    last_day = None
    weekly_trade_times = []
    drawdown_pause_until = None

    for current_time in common_times:
        current_day = current_time.date()

        if last_day is None or current_day != last_day:
            trades_today = {sym: 0 for sym in ASSETS}
            last_day = current_day

        weekly_trade_times = [t for t in weekly_trade_times
                              if (current_time - t).days < 7]

        # 1. Manage open positions: check exits first
        for sym in list(open_trades.keys()):
            trade = open_trades[sym]
            bar = indexed[sym].loc[current_time]
            exit_event = check_exit(trade, bar, current_time)
            if exit_event:
                finalize_trade(trade, exit_event)
                closed_trades.append(trade)
                cumulative_pnl += trade.net_pnl_usd
                del open_trades[sym]

        # 2. Mark-to-market equity: ACCOUNT + closed P&L + unrealized open P&L.
        #    Drawdown and circuit breakers operate against this MTM number so
        #    open losers can't hide behind closed winners.
        open_pnl = 0.0
        for sym, trade in open_trades.items():
            bar = indexed[sym].loc[current_time]
            open_pnl += unrealized_pnl(trade, bar["close"])

        mark_to_market_equity = ACCOUNT_SIZE_USD + cumulative_pnl + open_pnl

        if mark_to_market_equity > peak_account_value:
            peak_account_value = mark_to_market_equity
        drawdown_pct_from_peak = (
            (peak_account_value - mark_to_market_equity) / peak_account_value * 100
            if peak_account_value > 0 else 0
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

        # 3. Check entry eligibility
        if drawdown_pause_until and current_time < drawdown_pause_until:
            continue
        elif drawdown_pause_until and current_time >= drawdown_pause_until:
            drawdown_pause_until = None

        if len(open_trades) >= MAX_OPEN_POSITIONS:
            continue
        if len(weekly_trade_times) >= MAX_TRADES_PORTFOLIO_PER_WEEK:
            continue

        # 4. Scan all assets for signals on this bar
        candidates = []
        for sym in ASSETS:
            if sym in open_trades:
                continue
            if trades_today[sym] >= MAX_TRADES_PER_ASSET_PER_DAY:
                continue

            bar = indexed[sym].loc[current_time]
            btc_bar = btc_df.loc[current_time] if current_time in btc_df.index else None
            side = evaluate_signal(bar, btc_bar)
            if side:
                candidates.append((sym, side, signal_strength_from_rsi(bar["rsi_14"]), bar, btc_bar))

        if not candidates:
            continue

        # Same-direction filter: a new signal in the same direction as an existing
        # open trade must be 5+ RSI points more extreme to qualify.
        if open_trades:
            existing_sides = [t.side for t in open_trades.values()]
            filtered = []
            for cand in candidates:
                sym, side, strength, bar, btc_bar = cand
                if side in existing_sides:
                    existing_max = max(
                        signal_strength_from_rsi(t.rsi_at_signal)
                        for t in open_trades.values() if t.side == side
                    )
                    if strength <= existing_max + 5:
                        continue
                filtered.append(cand)
            candidates = filtered

        if not candidates:
            continue

        candidates.sort(key=lambda x: x[2], reverse=True)
        sym, side, strength, bar, btc_bar = candidates[0]

        # 5. Determine entry price (next bar open — no lookahead)
        sym_df = indexed[sym].reset_index()
        sig_idx = sym_df.index[sym_df["time"] == current_time].tolist()
        if not sig_idx or sig_idx[0] + 1 >= len(sym_df):
            continue
        next_bar = sym_df.iloc[sig_idx[0] + 1]
        entry_price = next_bar["open"]
        entry_time = next_bar["time"]

        atr = bar["atr_14"]
        if side == "long":
            stop_price = entry_price - 2 * atr
            target_1 = entry_price + 3 * atr
            target_2 = entry_price + 6 * atr
        else:
            stop_price = entry_price + 2 * atr
            target_1 = entry_price - 3 * atr
            target_2 = entry_price - 6 * atr

        sizing = size_position(entry_price, stop_price)
        if not sizing:
            continue
        notional, margin, leverage = sizing

        btc_rsi_at_signal = 50.0
        if btc_bar is not None and not pd.isna(btc_bar["rsi_14"]):
            btc_rsi_at_signal = float(btc_bar["rsi_14"])

        trade = Trade(
            symbol=sym, side=side,
            signal_candle_time=current_time, entry_time=entry_time,
            entry_price=entry_price, stop_price=stop_price,
            target_1_price=target_1, target_2_price=target_2,
            initial_stop_distance=abs(entry_price - stop_price),
            notional_usd=notional, margin_usd=margin, leverage=leverage,
            rsi_at_signal=float(bar["rsi_14"]), atr_at_signal=float(atr),
            btc_rsi_at_signal=btc_rsi_at_signal,
        )
        open_trades[sym] = trade
        trades_today[sym] += 1
        weekly_trade_times.append(current_time)

    # Mark-to-market any still-open trades at the last bar — and add their
    # net P&L to cumulative_pnl so the portfolio total reflects them.
    for sym, trade in open_trades.items():
        last_bar = indexed[sym].iloc[-1]
        bars_held = (last_bar.name - trade.entry_time).total_seconds() / TIMEFRAME_SECONDS
        finalize_trade(trade, {
            "exit_time": last_bar.name, "exit_price": last_bar["close"],
            "reason": "backtest_end", "bars_held": int(bars_held),
            "is_high_vol": is_high_vol_bar(last_bar),
        })
        closed_trades.append(trade)
        cumulative_pnl += trade.net_pnl_usd

    return closed_trades, equity_curve


def check_exit(trade, bar, current_time):
    """
    Exit priority (matches rules.json exit_logic order):
        1. Stop hit (immediate market close)
        2. Target 1 hit (close 50%, move stop to breakeven, runner stays open)
        3. Target 2 hit (close runner)
        4. Time stop (>= 56 bars from entry)
        5. Regime flip (close on wrong side of EMA(50))

    Same-bar policy:
      - When stop and target prices both fall within a single bar's range, the
        stop is assumed to fire first (conservative).
      - When target 1 fires, the runner is NOT also evaluated on the same bar;
        the BE-stopped runner is re-checked on the next bar. This understates
        the rare case of a single 6H bar covering entry → 6 ATR.
    """
    bars_held_so_far = (current_time - trade.entry_time).total_seconds() / TIMEFRAME_SECONDS
    if bars_held_so_far < 0:
        return None  # bar before entry — defensive
    is_high_vol = is_high_vol_bar(bar)

    has_partial = trade.exit_price_partial is not None

    # Priority 1-3: stop, partial-at-target-1, runner-at-target-2
    if trade.side == "long":
        if bar["low"] <= trade.stop_price:
            return {"exit_time": current_time, "exit_price": trade.stop_price,
                    "reason": "stop_hit", "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}
        if not has_partial and bar["high"] >= trade.target_1_price:
            trade.exit_price_partial = trade.target_1_price
            trade.exit_time_partial = current_time
            trade.stop_price = trade.entry_price
            return None
        if has_partial and bar["high"] >= trade.target_2_price:
            return {"exit_time": current_time, "exit_price": trade.target_2_price,
                    "reason": "target_2_runner_hit", "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}
    else:
        if bar["high"] >= trade.stop_price:
            return {"exit_time": current_time, "exit_price": trade.stop_price,
                    "reason": "stop_hit", "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}
        if not has_partial and bar["low"] <= trade.target_1_price:
            trade.exit_price_partial = trade.target_1_price
            trade.exit_time_partial = current_time
            trade.stop_price = trade.entry_price
            return None
        if has_partial and bar["low"] <= trade.target_2_price:
            return {"exit_time": current_time, "exit_price": trade.target_2_price,
                    "reason": "target_2_runner_hit", "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}

    # Priority 4: time stop (BEFORE regime flip per rules.json)
    if bars_held_so_far >= TIME_STOP_BARS:
        return {"exit_time": current_time, "exit_price": bar["close"],
                "reason": "time_stop", "bars_held": int(bars_held_so_far),
                "is_high_vol": is_high_vol}

    # Priority 5: regime flip
    if not pd.isna(bar["ema_50"]):
        if trade.side == "long" and bar["close"] < bar["ema_50"]:
            return {"exit_time": current_time, "exit_price": bar["close"],
                    "reason": "regime_flip", "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}
        if trade.side == "short" and bar["close"] > bar["ema_50"]:
            return {"exit_time": current_time, "exit_price": bar["close"],
                    "reason": "regime_flip", "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}

    return None


def finalize_trade(trade, exit_event):
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
            calculate_fees(trade.notional_usd)
            + calculate_fees(trade.notional_usd / 2)
            + calculate_fees(trade.notional_usd / 2)
        )
    else:
        if trade.side == "long":
            move = trade.exit_price_final - trade.entry_price
        else:
            move = trade.entry_price - trade.exit_price_final
        trade.gross_pnl_usd = (move / trade.entry_price) * trade.notional_usd
        trade.fees_usd = calculate_fees(trade.notional_usd) * 2

    is_high_vol = exit_event.get("is_high_vol", False)
    if trade.exit_price_partial is not None:
        trade.slippage_usd = (
            calculate_slippage(trade.notional_usd, is_high_vol)
            + calculate_slippage(trade.notional_usd / 2, is_high_vol)
            + calculate_slippage(trade.notional_usd / 2, is_high_vol)
        )
    else:
        trade.slippage_usd = calculate_slippage(trade.notional_usd, is_high_vol) * 2

    trade.funding_usd = calculate_trade_funding_drag(trade)
    trade.net_pnl_usd = trade.gross_pnl_usd - trade.fees_usd - trade.slippage_usd - trade.funding_usd

    one_r_dollars = (trade.initial_stop_distance / trade.entry_price) * trade.notional_usd
    trade.r_multiple = trade.net_pnl_usd / one_r_dollars if one_r_dollars > 0 else 0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def export_trades_csv(trades, path):
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


def export_equity_csv(curve, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = ["time", "equity", "cumulative_pnl", "open_positions", "drawdown_pct"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in curve:
            row = {**row, "time": row["time"].isoformat()}
            writer.writerow(row)
    print(f"Exported equity curve ({len(curve)} points) to {path}")


def report_summary(trades, equity_curve):
    if not trades:
        print("\nNo trades executed in this period.")
        return

    by_bucket = {}
    for t in trades:
        for bucket_name in ["ALL", t.symbol, f"{t.symbol}_{t.side}", f"ALL_{t.side}"]:
            by_bucket.setdefault(bucket_name, []).append(t)

    print("\n" + "=" * 70)
    print(f"BACKTEST SUMMARY ({DAYS_BACK} days, 6H bars)")
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
        avg_r = np.mean([t.r_multiple for t in bucket_trades])
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

    # Reconcile final portfolio P&L from trades directly so it always matches trades.csv.
    final_pnl = sum(t.net_pnl_usd for t in trades)
    final_equity = ACCOUNT_SIZE_USD + final_pnl
    max_dd = max((p["drawdown_pct"] for p in equity_curve), default=0.0)

    print(f"\n--- PORTFOLIO ---")
    print(f"  Starting capital:    ${ACCOUNT_SIZE_USD:.2f}")
    print(f"  Final equity:        ${final_equity:.2f}")
    print(f"  Net P&L:             ${final_pnl:+.2f}")
    print(f"  Return on capital:   {final_pnl / ACCOUNT_SIZE_USD * 100:+.2f}%")
    print(f"  Max drawdown:        {max_dd:.2f}%   (mark-to-market)")

    # Loss streak must be in chronological order — sort by exit_time, fall back to entry_time.
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
    print("INTERPRETATION GUIDE")
    print("=" * 70)
    print("Net Profit Factor > 1.5 = potentially viable, paper trade for 30 days")
    print("Net Profit Factor 1.0-1.5 = marginal, probably not worth deploying")
    print("Net Profit Factor < 1.0 = strategy loses money after costs, do not deploy")
    print("Avg R > +0.2 = each trade has positive expectancy")
    print("Max DD > 25% = risk parameters too aggressive")
    print("Max consecutive losses > 6 = high tail risk, expect rough patches")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Backtest config: {DAYS_BACK} days, 6H bars, {len(ASSETS)} assets")
    print(f"Assets: {', '.join(ASSETS)}")
    print(f"Indicators: EMA{EMA_FAST}/{EMA_SLOW}, RSI{RSI_PERIOD}, ATR{ATR_PERIOD}, Vol{VOL_AVG_PERIOD}")
    print(f"Filters: BTC regime ({BTC_REGIME_RSI_LOW}-{BTC_REGIME_RSI_HIGH}), "
          f"volume >= {VOLUME_MULTIPLIER}x avg")
    print()

    data = {}
    for symbol in ASSETS:
        print(f"Fetching {symbol} ...", end=" ", flush=True)
        df = fetch_candles(symbol, TIMEFRAME_SECONDS, DAYS_BACK)
        raw_count = len(df)
        df = drop_incomplete_candles(df, TIMEFRAME_SECONDS)
        if df.empty:
            print("0 bars — skipping this symbol")
            continue
        dropped = raw_count - len(df)
        df = add_indicators(df)
        data[symbol] = df
        suffix = f" (dropped {dropped} incomplete)" if dropped else ""
        print(f"{len(df)} bars ({df['time'].min().date()} to {df['time'].max().date()}){suffix}")

    if "BTC-USD" not in data:
        print("ERROR: BTC-USD data is required for the BTC regime filter — aborting.")
        return

    print("\nRunning scanner backtest...")
    trades, equity_curve = run_backtest(data)
    print(f"Backtest complete. {len(trades)} trades simulated.")

    export_trades_csv(trades, TRADES_CSV)
    export_equity_csv(equity_curve, EQUITY_CSV)
    report_summary(trades, equity_curve)


if __name__ == "__main__":
    main()
