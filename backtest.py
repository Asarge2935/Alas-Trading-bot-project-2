"""
Alas Trading Bot — Canonical strategy backtester (Coinbase BTC/ETH/SOL perps).

Implements docs/STRATEGY_SPEC.md: BTC-led regime + relative strength + breakout.

Pipeline, per closed 6H bar:
  1. REGIME (BTC, 1D, derived by resampling 6H): risk_on / risk_off / neutral.
     - risk_on : BTC>EMA50, EMA50 rising, BTC not >2% below EMA20, not within
                 the ±2% neutral band, EMA50 not flat.
     - risk_off: BTC<EMA50, EMA50 falling, not within the neutral band.
     - neutral : everything else -> NO TRADES (the bot is paid to wait).
  2. RELATIVE STRENGTH (1D, 20-day return): risk_on -> strongest is the long
     candidate; risk_off -> weakest is the short candidate. One candidate.
  3. BREAKOUT ENTRY (6H) on that one candidate: close>EMA50, breaks the prior
     20-bar high (long) / low (short), strong close (top/bottom 25% of range),
     volume above average, and BTC confirms (BTC on the same side of its EMA50).
  4. EXITS: stop = structure (breakout candle extreme) OR 1.5xATR, whichever is
     wider; take 50% at +1.5R and move stop to breakeven; trail the runner at
     2.5xATR; time-stop a dead trade after TIME_STOP_BARS with no progress.
  5. RISK: 1% of equity per trade. Daily-loss 2% / weekly-loss 5% halts. Phase-1
     caps: 1 open position at a time, 1 trade per asset per day.

No look-ahead: regime/RS for a 6H bar use the PREVIOUS completed daily bar;
signals form on a closed 6H bar and fill at the next bar's open.

Sizing note (edge vs. execution): the backtest sizes by 1%-risk notional so it
can MEASURE expectancy. The live integer-contract constraint (a nano perp is
~$188 margin, so a small account can fund 0 contracts) is a Phase-1 EXECUTION
concern handled in the live adapter, not here. See STRATEGY_SPEC.md §7.

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
# Config — locked to rules.json (v3). Do not tweak without updating both.
# ---------------------------------------------------------------------------

ASSETS = ["BTC-USD", "ETH-USD", "SOL-USD"]
TIMEFRAME_SECONDS = 21600          # 6H entry timeframe
DAYS_BACK = 365
BARS_PER_DAY = 86400 // TIMEFRAME_SECONDS

# --- Regime (BTC, daily) ---
REGIME_EMA_SLOW = 50
REGIME_EMA_FAST = 20
REGIME_SLOPE_DAYS = 10             # EMA50 "rising/falling" lookback
NEUTRAL_BAND_PCT = 0.02            # within ±2% of EMA50 -> neutral
EMA20_BELOW_LIMIT = 0.02           # risk_on blocked if >2% below EMA20
FLAT_SLOPE_EPS = 0.005             # |EMA50 change over lookback| < 0.5% -> flat -> neutral

# --- Relative strength (daily) ---
RS_LOOKBACK_DAYS = 20

# --- Breakout entry (6H) ---
BREAKOUT_LOOKBACK = 20             # prior N-bar high/low
STRONG_CLOSE_FRAC = 0.75           # close in top/bottom 25% of the bar range
VOLUME_MULTIPLIER = 1.0            # volume strictly above the 20-bar average
EMA_TREND = 50                     # 6H EMA the entry/BTC-confirm uses
VOL_AVG_PERIOD = 20
ATR_PERIOD = 14
ATR_REGIME_PERIOD = 120            # 30 days at 6H
ATR_REGIME_MULTIPLE = 2.0          # skip entries when ATR > 2x its regime avg (stress)

# --- Exits ---
STOP_ATR_MULT = 1.5
PARTIAL_R = 1.5                    # take 50% at +1.5R
TRAIL_ATR_MULT = 2.5               # trail the runner at 2.5xATR
TIME_STOP_BARS = 12               # ~3 days at 6H — dead-trade exit (spec: 8-12)

# --- Risk / portfolio ---
ACCOUNT_SIZE_USD = 500.0
RISK_PCT = 0.01                    # 1% of equity per trade
MAX_LEVERAGE = 2                   # notional cap = equity * MAX_LEVERAGE
MIN_STOP_DISTANCE_PCT = 0.005      # skip if stop tighter than 0.5%
MAX_OPEN_POSITIONS = 1             # Phase 1: one position at a time
MAX_TRADES_PER_ASSET_PER_DAY = 1   # Phase 1: max 1 per asset per day
MAX_TRADES_PORTFOLIO_PER_WEEK = 5  # backstop; the loss-% halts are the real cap
MAX_DAILY_LOSS_PCT = 2.0           # halt new entries for the day
MAX_WEEKLY_LOSS_PCT = 5.0          # halt new entries for the week

# --- Drawdown circuit breakers (MTM) ---
DRAWDOWN_PAUSE_PCT = 15.0
DRAWDOWN_STOP_PCT = 25.0
DRAWDOWN_PAUSE_DAYS = 7

# --- Costs (Coinbase derivatives, Intro 1) ---
TAKER_FEE_PCT = 0.0010             # 0.10% taker per side (perp); see VENUE_AUDIT_PERP_US.md
MIN_FEE_USD = 0.15
SLIPPAGE_NORMAL_PCT = 0.0010
SLIPPAGE_HIGH_VOL_PCT = 0.0020
FUNDING_DRAG_MONTHLY_PCT = 0.005   # PLACEHOLDER — wire real funding before live

OUTPUT_DIR = "./backtest_output"
TRADES_CSV = os.path.join(OUTPUT_DIR, "trades.csv")
EQUITY_CSV = os.path.join(OUTPUT_DIR, "equity_curve.csv")


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

USER_AGENT = "AlasTradingBotBacktester/3.0"


def safe_get(url, params, max_retries=5):
    """HTTP GET with rate-limit backoff and bounded retries."""
    headers = {"User-Agent": USER_AGENT}
    last_response = None
    for attempt in range(max_retries):
        r = requests.get(url, params=params, headers=headers, timeout=15)
        last_response = r
        if r.status_code == 429:
            time.sleep(0.5 * (2 ** attempt))
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

    df = pd.DataFrame(all_candles, columns=["time", "low", "high", "open", "close", "volume"])
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def drop_incomplete_candles(df, timeframe_seconds):
    """Drop the latest still-forming candle(s) so only closed bars are evaluated."""
    now = pd.Timestamp.now(tz="UTC")
    candle_close_time = df["time"] + pd.Timedelta(seconds=timeframe_seconds)
    return df[candle_close_time <= now].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Indicators (6H entry timeframe)
# ---------------------------------------------------------------------------

def add_indicators(df):
    df = df.copy()
    df["ema_50"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(window=ATR_PERIOD).mean()
    df["atr_regime_avg"] = df["atr_14"].rolling(window=ATR_REGIME_PERIOD).mean()

    df["volume_avg_20"] = df["volume"].rolling(window=VOL_AVG_PERIOD).mean()

    # Prior N-bar extremes (shifted so the current bar is excluded -> no look-ahead).
    df["prior_high"] = df["high"].rolling(window=BREAKOUT_LOOKBACK).max().shift(1)
    df["prior_low"] = df["low"].rolling(window=BREAKOUT_LOOKBACK).min().shift(1)
    return df


# ---------------------------------------------------------------------------
# Regime + relative strength (daily, derived from the 6H series)
# ---------------------------------------------------------------------------

def _daily_close(df_6h_indexed):
    """Daily close = last 6H close of each UTC day."""
    return df_6h_indexed["close"].resample("1D").last().dropna()


def compute_regime(btc_daily_close):
    """Three-state BTC regime per day, shifted so a day uses the PRIOR day's state.

    Returns a Series indexed by daily (midnight-UTC) Timestamp with values in
    {"risk_on", "risk_off", "neutral"}. Look up by current_time.normalize().
    """
    close = btc_daily_close.astype(float)
    ema_slow = close.ewm(span=REGIME_EMA_SLOW, adjust=False).mean()
    ema_fast = close.ewm(span=REGIME_EMA_FAST, adjust=False).mean()
    prev_slow = ema_slow.shift(REGIME_SLOPE_DAYS)

    slope = ema_slow / prev_slow - 1.0
    rising = slope > FLAT_SLOPE_EPS
    falling = slope < -FLAT_SLOPE_EPS
    near_band = (close / ema_slow - 1.0).abs() < NEUTRAL_BAND_PCT
    not_below_ema20 = close >= ema_fast * (1.0 - EMA20_BELOW_LIMIT)

    risk_on = (close > ema_slow) & rising & ~near_band & not_below_ema20
    risk_off = (close < ema_slow) & falling & ~near_band

    state = np.where(risk_on, "risk_on", np.where(risk_off, "risk_off", "neutral"))
    out = pd.Series(state, index=close.index, name="regime")
    # Bars without enough history (NaN EMAs / slope) are neutral by default.
    out[ema_slow.isna() | prev_slow.isna()] = "neutral"
    return out.shift(1).fillna("neutral")


def compute_rs(daily_close_by_sym):
    """Per-day trailing 20-day return for each asset, shifted to use the prior day.

    Returns a DataFrame indexed by daily Timestamp, one column per symbol.
    """
    cols = {}
    for sym, s in daily_close_by_sym.items():
        cols[sym] = s / s.shift(RS_LOOKBACK_DAYS) - 1.0
    rs = pd.DataFrame(cols).shift(1)
    return rs


def select_candidate(regime_state, rs_row):
    """Return (symbol, side) for the one candidate this bar, or (None, None)."""
    if regime_state == "neutral" or rs_row is None:
        return None, None
    valid = rs_row.dropna()
    if valid.empty:
        return None, None
    if regime_state == "risk_on":
        return valid.idxmax(), "long"
    return valid.idxmin(), "short"


# ---------------------------------------------------------------------------
# Trade dataclass
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    side: str
    # Coinbase candle "time" is the candle START. The signal is actionable only
    # AFTER the bar closes; entry fills at the next bar's open.
    signal_candle_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    target_1_price: float
    initial_stop_distance: float
    notional_usd: float
    margin_usd: float
    leverage: int
    atr_at_signal: float
    regime_state: str
    rs_return: float

    trail_anchor: Optional[float] = None
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
    return max(notional_usd * TAKER_FEE_PCT, MIN_FEE_USD)


def calculate_slippage(notional_usd, is_high_vol):
    pct = SLIPPAGE_HIGH_VOL_PCT if is_high_vol else SLIPPAGE_NORMAL_PCT
    return notional_usd * pct


def calculate_trade_funding_drag(trade):
    """Funding drag respecting the partial close: full notional pre-target-1, half after."""
    monthly_to_daily = FUNDING_DRAG_MONTHLY_PCT / 30
    if trade.exit_time_partial is None:
        days_held = trade.bars_held / BARS_PER_DAY
        return trade.notional_usd * monthly_to_daily * days_held
    bars_before = (trade.exit_time_partial - trade.entry_time).total_seconds() / TIMEFRAME_SECONDS
    bars_after = (trade.exit_time - trade.exit_time_partial).total_seconds() / TIMEFRAME_SECONDS
    funding_before = trade.notional_usd * monthly_to_daily * (bars_before / BARS_PER_DAY)
    funding_after = (trade.notional_usd / 2) * monthly_to_daily * (bars_after / BARS_PER_DAY)
    return funding_before + funding_after


def is_high_vol_bar(bar):
    """High-volatility bar: range exceeds 2 x ATR(14)."""
    atr = bar.get("atr_14") if hasattr(bar, "get") else None
    if atr is None or pd.isna(atr):
        return False
    return (bar["high"] - bar["low"]) > 2 * atr


def unrealized_pnl(trade, current_price):
    """MTM P&L on the OPEN portion. After a partial close, only half is open."""
    move = (current_price - trade.entry_price) if trade.side == "long" \
        else (trade.entry_price - current_price)
    open_notional = trade.notional_usd / 2 if trade.exit_price_partial is not None \
        else trade.notional_usd
    return (move / trade.entry_price) * open_notional


# ---------------------------------------------------------------------------
# Position sizing — 1% risk
# ---------------------------------------------------------------------------

def size_position(entry_price, stop_price, equity):
    """notional = (RISK_PCT * equity) / stop_pct, capped at equity * MAX_LEVERAGE."""
    stop_distance_pct = abs(entry_price - stop_price) / entry_price
    if stop_distance_pct < MIN_STOP_DISTANCE_PCT:
        return None
    risk_usd = RISK_PCT * equity
    notional_usd = risk_usd / stop_distance_pct
    notional_usd = min(notional_usd, equity * MAX_LEVERAGE)
    margin_usd = notional_usd / MAX_LEVERAGE
    if notional_usd <= 0:
        return None
    return notional_usd, margin_usd, MAX_LEVERAGE


# ---------------------------------------------------------------------------
# Breakout signal
# ---------------------------------------------------------------------------

def evaluate_breakout(row, side, btc_row):
    """True if `side` breakout conditions hold on this 6H bar (spec §5)."""
    required = ["ema_50", "atr_14", "atr_regime_avg", "volume_avg_20",
                "prior_high", "prior_low"]
    for col in required:
        if pd.isna(row[col]):
            return False

    # Volatility-stress stand-down.
    if row["atr_14"] > ATR_REGIME_MULTIPLE * row["atr_regime_avg"]:
        return False
    # Volume confirmation.
    if row["volume"] < VOLUME_MULTIPLIER * row["volume_avg_20"]:
        return False

    bar_range = row["high"] - row["low"]
    if bar_range <= 0:
        return False

    btc_ok = btc_row is not None and not pd.isna(btc_row["ema_50"])
    if not btc_ok:
        return False

    if side == "long":
        strong_close = (row["close"] - row["low"]) / bar_range >= STRONG_CLOSE_FRAC
        return (
            row["close"] > row["ema_50"]
            and row["close"] > row["prior_high"]
            and strong_close
            and btc_row["close"] > btc_row["ema_50"]
        )
    else:
        strong_close = (row["high"] - row["close"]) / bar_range >= STRONG_CLOSE_FRAC
        return (
            row["close"] < row["ema_50"]
            and row["close"] < row["prior_low"]
            and strong_close
            and btc_row["close"] < btc_row["ema_50"]
        )


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

    # Daily regime + relative strength, derived from the 6H series.
    daily_close = {sym: _daily_close(indexed[sym]) for sym in indexed}
    regime_by_day = compute_regime(daily_close["BTC-USD"])
    regime_map = regime_by_day.to_dict()
    rs_by_day = compute_rs(daily_close)

    open_trades = {}
    closed_trades = []
    equity_curve = []
    cumulative_pnl = 0.0
    peak_account_value = ACCOUNT_SIZE_USD
    drawdown_pct_from_peak = 0.0

    trades_today = {sym: 0 for sym in ASSETS}
    last_day = None
    last_week = None
    day_start_equity = ACCOUNT_SIZE_USD
    week_start_equity = ACCOUNT_SIZE_USD
    weekly_trade_times = []
    drawdown_pause_until = None

    for current_time in common_times:
        current_day = current_time.date()
        current_week = current_time.isocalendar()[:2]

        # 1. Manage open positions: exits first.
        for sym in list(open_trades.keys()):
            trade = open_trades[sym]
            bar = indexed[sym].loc[current_time]
            exit_event = check_exit(trade, bar, current_time)
            if exit_event:
                finalize_trade(trade, exit_event)
                closed_trades.append(trade)
                cumulative_pnl += trade.net_pnl_usd
                del open_trades[sym]

        # 2. Mark-to-market equity (account + closed + unrealized open).
        open_pnl = 0.0
        for sym, trade in open_trades.items():
            bar = indexed[sym].loc[current_time]
            open_pnl += unrealized_pnl(trade, bar["close"])
        mark_to_market_equity = ACCOUNT_SIZE_USD + cumulative_pnl + open_pnl

        # Day / week rollover: snapshot equity for the loss-limit halts.
        if last_day is None or current_day != last_day:
            trades_today = {sym: 0 for sym in ASSETS}
            day_start_equity = mark_to_market_equity
            last_day = current_day
        if last_week is None or current_week != last_week:
            week_start_equity = mark_to_market_equity
            last_week = current_week
        weekly_trade_times = [t for t in weekly_trade_times if (current_time - t).days < 7]

        if mark_to_market_equity > peak_account_value:
            peak_account_value = mark_to_market_equity
        drawdown_pct_from_peak = (
            (peak_account_value - mark_to_market_equity) / peak_account_value * 100
            if peak_account_value > 0 else 0
        )

        if drawdown_pct_from_peak >= DRAWDOWN_STOP_PCT:
            print(f"DRAWDOWN STOP HIT at {current_time}: {drawdown_pct_from_peak:.1f}%")
            equity_curve.append(_equity_point(current_time, mark_to_market_equity,
                                               cumulative_pnl, open_trades, drawdown_pct_from_peak))
            return closed_trades, equity_curve
        if drawdown_pct_from_peak >= DRAWDOWN_PAUSE_PCT and drawdown_pause_until is None:
            drawdown_pause_until = current_time + timedelta(days=DRAWDOWN_PAUSE_DAYS)
            print(f"DRAWDOWN PAUSE at {current_time}: {drawdown_pct_from_peak:.1f}% "
                  f"— paused until {drawdown_pause_until.date()}")

        equity_curve.append(_equity_point(current_time, mark_to_market_equity,
                                           cumulative_pnl, open_trades, drawdown_pct_from_peak))

        # 3. Entry eligibility gates.
        if drawdown_pause_until and current_time < drawdown_pause_until:
            continue
        elif drawdown_pause_until and current_time >= drawdown_pause_until:
            drawdown_pause_until = None
        if len(open_trades) >= MAX_OPEN_POSITIONS:
            continue
        if len(weekly_trade_times) >= MAX_TRADES_PORTFOLIO_PER_WEEK:
            continue
        # Daily / weekly loss-% halts.
        if day_start_equity > 0 and \
                (mark_to_market_equity - day_start_equity) / day_start_equity * 100 <= -MAX_DAILY_LOSS_PCT:
            continue
        if week_start_equity > 0 and \
                (mark_to_market_equity - week_start_equity) / week_start_equity * 100 <= -MAX_WEEKLY_LOSS_PCT:
            continue

        # 4. Regime -> RS -> single candidate.
        regime_state = regime_map.get(current_time.normalize(), "neutral")
        if regime_state == "neutral":
            continue
        rs_row = rs_by_day.loc[current_time.normalize()] \
            if current_time.normalize() in rs_by_day.index else None
        sym, side = select_candidate(regime_state, rs_row)
        if sym is None or sym not in indexed:
            continue
        if sym in open_trades or trades_today.get(sym, 0) >= MAX_TRADES_PER_ASSET_PER_DAY:
            continue

        bar = indexed[sym].loc[current_time]
        btc_bar = btc_df.loc[current_time] if current_time in btc_df.index else None
        if not evaluate_breakout(bar, side, btc_bar):
            continue

        # 5. Entry at the next bar's open (no look-ahead).
        sym_df = indexed[sym].reset_index()
        sig_idx = sym_df.index[sym_df["time"] == current_time].tolist()
        if not sig_idx or sig_idx[0] + 1 >= len(sym_df):
            continue
        next_bar = sym_df.iloc[sig_idx[0] + 1]
        entry_price = next_bar["open"]
        entry_time = next_bar["time"]
        atr = bar["atr_14"]

        # Stop = structure (breakout candle extreme) OR 1.5xATR, whichever is WIDER.
        if side == "long":
            structure_stop = bar["low"]
            atr_stop = entry_price - STOP_ATR_MULT * atr
            stop_price = min(structure_stop, atr_stop)
            r = entry_price - stop_price
            target_1 = entry_price + PARTIAL_R * r
        else:
            structure_stop = bar["high"]
            atr_stop = entry_price + STOP_ATR_MULT * atr
            stop_price = max(structure_stop, atr_stop)
            r = stop_price - entry_price
            target_1 = entry_price - PARTIAL_R * r
        if r <= 0:
            continue

        sizing = size_position(entry_price, stop_price, mark_to_market_equity)
        if not sizing:
            continue
        notional, margin, leverage = sizing

        rs_ret = float(rs_row[sym]) if rs_row is not None and not pd.isna(rs_row[sym]) else float("nan")
        trade = Trade(
            symbol=sym, side=side,
            signal_candle_time=current_time, entry_time=entry_time,
            entry_price=entry_price, stop_price=stop_price, target_1_price=target_1,
            initial_stop_distance=abs(entry_price - stop_price),
            notional_usd=notional, margin_usd=margin, leverage=leverage,
            atr_at_signal=float(atr), regime_state=regime_state, rs_return=rs_ret,
        )
        open_trades[sym] = trade
        trades_today[sym] = trades_today.get(sym, 0) + 1
        weekly_trade_times.append(current_time)

    # Mark any still-open trades to the final bar.
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


def _equity_point(t, equity, cum_pnl, open_trades, dd):
    return {"time": t, "equity": equity, "cumulative_pnl": cum_pnl,
            "open_positions": len(open_trades), "drawdown_pct": dd}


def check_exit(trade, bar, current_time):
    """
    Exit priority (spec §6):
      1. Stop (initial / breakeven / trailing) — checked with the stop carried
         in from prior bars (conservative: same-bar new highs don't tighten the
         stop until the next bar).
      2. Partial at +1.5R — close 50%, move stop to breakeven.
      3. After a partial, ratchet the trailing stop (2.5xATR) for the NEXT bar.
      4. Time stop — exit a dead trade after TIME_STOP_BARS with no partial.
    """
    bars_held_so_far = (current_time - trade.entry_time).total_seconds() / TIMEFRAME_SECONDS
    if bars_held_so_far < 0:
        return None
    is_high_vol = is_high_vol_bar(bar)
    has_partial = trade.exit_price_partial is not None

    if trade.side == "long":
        # 1. stop
        if bar["low"] <= trade.stop_price:
            return {"exit_time": current_time, "exit_price": trade.stop_price,
                    "reason": _stop_reason(trade, has_partial), "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}
        # 2. partial
        if not has_partial and bar["high"] >= trade.target_1_price:
            trade.exit_price_partial = trade.target_1_price
            trade.exit_time_partial = current_time
            trade.stop_price = trade.entry_price          # breakeven
            trade.trail_anchor = max(bar["high"], trade.entry_price)
            return None
        # 3. ratchet trailing stop for next bar
        if has_partial:
            trade.trail_anchor = max(trade.trail_anchor, bar["high"])
            trade.stop_price = max(trade.stop_price,
                                   trade.trail_anchor - TRAIL_ATR_MULT * trade.atr_at_signal)
    else:
        if bar["high"] >= trade.stop_price:
            return {"exit_time": current_time, "exit_price": trade.stop_price,
                    "reason": _stop_reason(trade, has_partial), "bars_held": int(bars_held_so_far),
                    "is_high_vol": is_high_vol}
        if not has_partial and bar["low"] <= trade.target_1_price:
            trade.exit_price_partial = trade.target_1_price
            trade.exit_time_partial = current_time
            trade.stop_price = trade.entry_price
            trade.trail_anchor = min(bar["low"], trade.entry_price)
            return None
        if has_partial:
            trade.trail_anchor = min(trade.trail_anchor, bar["low"])
            trade.stop_price = min(trade.stop_price,
                                   trade.trail_anchor + TRAIL_ATR_MULT * trade.atr_at_signal)

    # 4. time stop — only a dead trade that never reached the partial
    if not has_partial and bars_held_so_far >= TIME_STOP_BARS:
        return {"exit_time": current_time, "exit_price": bar["close"],
                "reason": "time_stop", "bars_held": int(bars_held_so_far),
                "is_high_vol": is_high_vol}
    return None


def _stop_reason(trade, has_partial):
    if not has_partial:
        return "stop_hit"
    return "breakeven_stop" if trade.stop_price == trade.entry_price else "trail_stop"


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
        trade.fees_usd = (calculate_fees(trade.notional_usd)
                          + calculate_fees(trade.notional_usd / 2)
                          + calculate_fees(trade.notional_usd / 2))
    else:
        move = (trade.exit_price_final - trade.entry_price) if trade.side == "long" \
            else (trade.entry_price - trade.exit_price_final)
        trade.gross_pnl_usd = (move / trade.entry_price) * trade.notional_usd
        trade.fees_usd = calculate_fees(trade.notional_usd) * 2

    is_high_vol = exit_event.get("is_high_vol", False)
    if trade.exit_price_partial is not None:
        trade.slippage_usd = (calculate_slippage(trade.notional_usd, is_high_vol)
                              + calculate_slippage(trade.notional_usd / 2, is_high_vol)
                              + calculate_slippage(trade.notional_usd / 2, is_high_vol))
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
            writer.writerow({**row, "time": row["time"].isoformat()})
    print(f"Exported equity curve ({len(curve)} points) to {path}")


def report_summary(trades, equity_curve):
    if not trades:
        print("\nNo trades executed in this period. "
              "(With strict regime+RS+breakout gating, zero trades is a valid result.)")
        return

    by_bucket = {}
    for t in trades:
        for bucket_name in ["ALL", t.symbol, f"{t.symbol}_{t.side}", f"ALL_{t.side}"]:
            by_bucket.setdefault(bucket_name, []).append(t)

    print("\n" + "=" * 70)
    print(f"BACKTEST SUMMARY ({DAYS_BACK} days, 6H bars, BTC/ETH/SOL)")
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

    final_pnl = sum(t.net_pnl_usd for t in trades)
    final_equity = ACCOUNT_SIZE_USD + final_pnl
    max_dd = max((p["drawdown_pct"] for p in equity_curve), default=0.0)

    print(f"\n--- PORTFOLIO ---")
    print(f"  Starting capital:    ${ACCOUNT_SIZE_USD:.2f}")
    print(f"  Final equity:        ${final_equity:.2f}")
    print(f"  Net P&L:             ${final_pnl:+.2f}")
    print(f"  Return on capital:   {final_pnl / ACCOUNT_SIZE_USD * 100:+.2f}%")
    print(f"  Max drawdown:        {max_dd:.2f}%   (mark-to-market)")

    trades_sorted = sorted(trades, key=lambda t: t.exit_time or t.entry_time)
    streak = max_loss_streak = 0
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
    print("Need >= 30 trades before the numbers mean anything (spec gate).")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Backtest config: {DAYS_BACK} days, 6H bars, assets: {', '.join(ASSETS)}")
    print(f"Regime: BTC 1D EMA{REGIME_EMA_SLOW} (3-state)  |  RS: {RS_LOOKBACK_DAYS}d return")
    print(f"Entry: {BREAKOUT_LOOKBACK}-bar breakout + strong close + BTC confirm")
    print(f"Risk: {RISK_PCT*100:.0f}%/trade, daily-loss {MAX_DAILY_LOSS_PCT}%, "
          f"weekly-loss {MAX_WEEKLY_LOSS_PCT}%, max {MAX_OPEN_POSITIONS} open")
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
        print("ERROR: BTC-USD data is required for the regime filter — aborting.")
        return

    print("\nRunning scanner backtest...")
    trades, equity_curve = run_backtest(data)
    print(f"Backtest complete. {len(trades)} trades simulated.")

    export_trades_csv(trades, TRADES_CSV)
    export_equity_csv(equity_curve, EQUITY_CSV)
    report_summary(trades, equity_curve)


if __name__ == "__main__":
    main()
