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

import argparse
import csv
import os
import random
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
DAYS_BACK = 1460                   # ~4 years — the spec gates need >= 30 trades
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

# --- Validation gates (STRATEGY_SPEC.md §9) ---
GATE_MIN_TRADES = 30
GATE_MIN_PF = 1.3
GATE_MAX_DD_PCT = 25.0
GATE_MAX_TRADE_SHARE = 0.25        # no single trade > 25% of net P&L
GATE_OOS_PF_RATIO = 0.75           # OOS PF must be >= 0.75x in-sample PF
OOS_SPLIT_FRAC = 0.70              # first 70% of the window = in-sample

OUTPUT_DIR = "./backtest_output"
TRADES_CSV = os.path.join(OUTPUT_DIR, "trades.csv")
EQUITY_CSV = os.path.join(OUTPUT_DIR, "equity_curve.csv")
LOSS_AUTOPSY_CSV = os.path.join(OUTPUT_DIR, "loss_autopsy.csv")


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

USER_AGENT = "AlasTradingBotBacktester/3.0"

# --- Network hardening (transport only; no strategy logic) ---
HTTP_TIMEOUT_SECONDS = 30          # was 15; Coinbase pagination is bursty
HTTP_MAX_RETRIES = 6               # backoff sequence 1,2,4,8,16 (then final try)
HTTP_MAX_BACKOFF_SECONDS = 16
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

# --- Candle cache (avoid re-downloading the same history every run) ---
CACHE_DIR = os.path.join("data_cache", "candles")
CACHE_FRESHNESS_SECONDS = 86400    # reuse cache within a day; otherwise refresh


def safe_get(url, params, max_retries=HTTP_MAX_RETRIES):
    """HTTP GET with exponential backoff + jitter.

    Retries on transient transport errors (ReadTimeout / ConnectionError /
    Timeout) and retryable HTTP statuses (429 + 5xx). Non-retryable statuses
    (e.g. 403/404) raise immediately. Raises the last error if all attempts
    fail. Transport hardening only — does not touch candle data."""
    headers = {"User-Agent": USER_AGENT}
    transient = (requests.exceptions.ReadTimeout,
                 requests.exceptions.ConnectionError,
                 requests.exceptions.Timeout)
    last_exc = None
    last_response = None
    for attempt in range(max_retries):
        reason = None
        try:
            r = requests.get(url, params=params, headers=headers,
                             timeout=HTTP_TIMEOUT_SECONDS)
            last_response = r
            if r.status_code in RETRY_STATUS_CODES:
                reason = f"HTTP {r.status_code}"
            else:
                r.raise_for_status()      # non-retryable 4xx raise here
                return r
        except transient as e:
            reason = e.__class__.__name__
            last_exc = e

        if attempt < max_retries - 1:
            base = min(2 ** attempt, HTTP_MAX_BACKOFF_SECONDS)
            sleep_s = base + random.uniform(0, base * 0.25)   # jitter
            print(f"  Retrying Coinbase request after {reason} "
                  f"(attempt {attempt + 1}/{max_retries}, waiting {sleep_s:.1f}s) ...",
                  flush=True)
            time.sleep(sleep_s)

    if last_response is not None and last_response.status_code in RETRY_STATUS_CODES:
        last_response.raise_for_status()
    if last_exc is not None:
        raise last_exc
    raise requests.exceptions.HTTPError(
        f"Coinbase request failed after {max_retries} attempts: {url}")


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


def _cache_path(product_id, granularity_seconds, days_back):
    return os.path.join(CACHE_DIR, f"{product_id}_{granularity_seconds}s_{days_back}d.csv")


def _read_cache(path):
    """Return cached raw candles as a DataFrame, or None if absent/unreadable."""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        if df.empty or "time" not in df.columns:
            return None
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df.sort_values("time").reset_index(drop=True)
    except Exception:
        return None


def _write_cache(path, df):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = df.copy()
    out["time"] = out["time"].map(lambda t: t.isoformat())
    out.to_csv(path, index=False)


def load_candles(product_id, granularity_seconds, days_back):
    """Return RAW candles for a symbol, preferring a fresh local cache.

    Caching only — candle values are identical to a direct fetch; the caller
    still drops incomplete candles. Resolution order:
      1. fresh cache (newer than CACHE_FRESHNESS_SECONDS) -> use it
      2. otherwise fetch; on success refresh the cache
      3. if the fetch fails but any cache exists -> warn and use the stale cache
      4. if the fetch fails and no cache exists -> raise clearly
    """
    path = _cache_path(product_id, granularity_seconds, days_back)
    cached = _read_cache(path)
    if cached is not None and not cached.empty:
        age = (pd.Timestamp.now(tz="UTC") - cached["time"].max()).total_seconds()
        if age < CACHE_FRESHNESS_SECONDS:
            print(f"Using cached candles for {product_id} "
                  f"({len(cached)} rows, age {age / 3600:.1f}h) ...", flush=True)
            return cached

    print(f"Fetching {product_id} ...", end=" ", flush=True)
    try:
        df = fetch_candles(product_id, granularity_seconds, days_back)
        if df.empty:
            raise RuntimeError("empty candle response")
        _write_cache(path, df)
        print(f"fetched {len(df)} candles (cached to {path}).", flush=True)
        return df
    except Exception as e:
        if cached is not None and not cached.empty:
            print(f"\nWARNING: fetch failed for {product_id} "
                  f"({e.__class__.__name__}: {e}); using cached candles "
                  f"({len(cached)} rows, possibly stale).", flush=True)
            return cached
        print(f"\nERROR: fetch failed for {product_id} and no cache exists at "
              f"{path}: {e.__class__.__name__}: {e}", flush=True)
        raise


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


def get_btc_regime(btc_daily_close, strict=False):
    """Single source of truth for the BTC daily 3-state regime.

    Returns a Series indexed by daily (midnight-UTC) Timestamp with values in
    {"risk_on", "risk_off", "neutral"}, shifted so a day uses the PRIOR day's
    state (no look-ahead). Look up by current_time.normalize().

    strict=False -> the canonical/default regime (unchanged behavior).
    strict=True  -> the experimental --strict-regime definition: BTC daily
                    close vs EMA50 with a ±2% band and EMA50 slope sign only.
    """
    close = btc_daily_close.astype(float)
    ema_slow = close.ewm(span=REGIME_EMA_SLOW, adjust=False).mean()
    prev_slow = ema_slow.shift(REGIME_SLOPE_DAYS)
    slope = ema_slow / prev_slow - 1.0

    if strict:
        risk_on = (close > ema_slow * 1.02) & (slope > 0)
        risk_off = (close < ema_slow * 0.98) & (slope < 0)
    else:
        ema_fast = close.ewm(span=REGIME_EMA_FAST, adjust=False).mean()
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


def compute_regime(btc_daily_close):
    """Backward-compatible alias for the canonical (non-strict) regime."""
    return get_btc_regime(btc_daily_close, strict=False)


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

def run_backtest(data_by_symbol, trade_assets=None, long_only=False, strict_regime=False):
    """Simulate the strategy.

    Diagnostic isolation (does NOT change the deployed rules): regime and
    relative strength are always computed over the full BTC/ETH/SOL set, so the
    RS pick is unchanged. `trade_assets` restricts which symbols may be ENTERED;
    `long_only` suppresses short entries. With no arguments this is the full
    canonical strategy.
    """
    tradable = set(trade_assets) if trade_assets else set(data_by_symbol.keys())

    common_times = None
    for sym, df in data_by_symbol.items():
        times = set(df["time"])
        common_times = times if common_times is None else common_times & times
    common_times = sorted(common_times)

    indexed = {sym: df.set_index("time") for sym, df in data_by_symbol.items()}
    btc_df = indexed["BTC-USD"]

    # Daily regime + relative strength, derived from the 6H series.
    daily_close = {sym: _daily_close(indexed[sym]) for sym in indexed}
    regime_by_day = get_btc_regime(daily_close["BTC-USD"], strict=strict_regime)
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
        if long_only and side == "short":   # diagnostic isolation only
            continue
        if sym not in tradable:              # diagnostic isolation only
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


# ---------------------------------------------------------------------------
# Loss autopsy — ANALYSIS ONLY. Reads the produced trades + the same price
# series; it changes no entries, exits, sizing, or costs.
# ---------------------------------------------------------------------------

# A "loss" matches report_summary's convention: net P&L <= 0.
# Classification thresholds (descriptive heuristics, not strategy parameters):
AUTOPSY_INSTANT_BARS = 2          # stopped within ~2 bars -> breakout failed instantly
AUTOPSY_SLOW_BLEED_BARS = 6       # held >= 6 bars, never got going -> slow bleed
AUTOPSY_ALMOST_TARGET_R = 1.0     # MFE reached >= 1.0R (target_1 is +1.5R)
AUTOPSY_STOP_TIGHT_R = 0.5        # had >= 0.5R in favor before the stop hit
AUTOPSY_CHOP_MFE_R = 0.5          # never made 0.5R of progress
AUTOPSY_CHOP_MAE_R = 1.0          # ...and never took a clean 1R hit -> oscillated


def _one_r_dollars(trade):
    """Initial risk in $ — identical to finalize_trade's r_multiple denominator."""
    return (trade.initial_stop_distance / trade.entry_price) * trade.notional_usd


def _trade_excursions(trade, sym_indexed):
    """MFE_R / MAE_R and the bar offsets at which they occurred, derived by
    re-walking the bars the trade was actually open for. Excursions are in
    price-R (gross), the standard MFE/MAE convention."""
    idx = sym_indexed.index
    bars = sym_indexed.loc[(idx >= trade.entry_time) & (idx <= trade.exit_time)]
    R = trade.initial_stop_distance
    if bars.empty or R <= 0:
        return 0.0, 0.0, 0, 0
    best_fav, best_adv, b_mfe, b_mae = None, None, 0, 0
    for i, (_, row) in enumerate(bars.iterrows()):
        if trade.side == "long":
            fav = row["high"] - trade.entry_price
            adv = trade.entry_price - row["low"]
        else:
            fav = trade.entry_price - row["low"]
            adv = row["high"] - trade.entry_price
        if best_fav is None or fav > best_fav:
            best_fav, b_mfe = fav, i
        if best_adv is None or adv > best_adv:
            best_adv, b_mae = adv, i
    return best_fav / R, max(best_adv, 0.0) / R, b_mfe, b_mae


def _classify_loss(trade, mfe_r, mae_r):
    """Multi-label classification of a single losing trade."""
    labels = []
    reason = trade.exit_reason
    bars = trade.bars_held
    net = trade.net_pnl_usd
    gross = trade.gross_pnl_usd

    if reason == "time_stop" or (mfe_r < AUTOPSY_CHOP_MFE_R and mae_r < AUTOPSY_CHOP_MAE_R):
        labels.append("regime_chop_loss")
    if mfe_r < AUTOPSY_CHOP_MFE_R and mae_r >= AUTOPSY_CHOP_MAE_R and bars <= 3:
        labels.append("regime_against_trade")
    if bars <= AUTOPSY_INSTANT_BARS and mfe_r < AUTOPSY_CHOP_MFE_R:
        labels.append("instant_failure")
    if bars >= AUTOPSY_SLOW_BLEED_BARS and mfe_r < AUTOPSY_ALMOST_TARGET_R and net < 0:
        labels.append("slow_bleed")
    if mfe_r >= AUTOPSY_ALMOST_TARGET_R:
        labels.append("almost_hit_target")
    if (trade.exit_price_partial is not None or mfe_r >= PARTIAL_R) and net < 0:
        labels.append("gave_back_profit")
    if reason == "stop_hit" and mfe_r >= AUTOPSY_STOP_TIGHT_R:
        labels.append("stop_too_tight")
    if gross >= 0 and net < 0:
        labels.append("cost_drag_loss")
    if trade.symbol == "SOL-USD":
        labels.append("asset_specific_SOL")
    if trade.side == "short":
        labels.append("short_side_loss")
    return labels


def build_loss_autopsy(trades, data_by_symbol):
    """Return a list of per-losing-trade dict rows for loss_autopsy.csv.
    ANALYSIS ONLY — recomputes RS exactly as run_backtest does for the rank."""
    indexed = {sym: df.set_index("time") for sym, df in data_by_symbol.items()}
    daily_close = {sym: _daily_close(indexed[sym]) for sym in indexed}
    rs_by_day = compute_rs(daily_close)

    rows = []
    for t in trades:
        if t.net_pnl_usd > 0:                      # losers only (net <= 0)
            continue
        sym_indexed = indexed.get(t.symbol)
        if sym_indexed is None:
            continue
        mfe_r, mae_r, b_mfe, b_mae = _trade_excursions(t, sym_indexed)
        one_r = _one_r_dollars(t)
        total_costs = t.fees_usd + t.slippage_usd + t.funding_usd
        cost_r = total_costs / one_r if one_r > 0 else 0.0

        # Signal-bar context (extension, volume ratio).
        sig_day = t.signal_candle_time.normalize()
        entry_ext_atr = ""
        vol_ratio = ""
        if t.signal_candle_time in sym_indexed.index:
            sig = sym_indexed.loc[t.signal_candle_time]
            if not pd.isna(sig["ema_50"]) and t.atr_at_signal > 0:
                ext = (t.entry_price - sig["ema_50"]) if t.side == "long" \
                    else (sig["ema_50"] - t.entry_price)
                entry_ext_atr = round(ext / t.atr_at_signal, 3)
            if not pd.isna(sig["volume_avg_20"]) and sig["volume_avg_20"] > 0:
                vol_ratio = round(sig["volume"] / sig["volume_avg_20"], 3)

        # Relative-strength rank at entry (1 = strongest), same row the sim used.
        rs_rank = ""
        if sig_day in rs_by_day.index:
            rs_row = rs_by_day.loc[sig_day].dropna()
            if t.symbol in rs_row.index:
                rs_rank = int(rs_row.rank(ascending=False)[t.symbol])

        rows.append({
            "symbol": t.symbol,
            "side": t.side,
            "entry_time": t.entry_time.isoformat(),
            "exit_time": t.exit_time.isoformat() if t.exit_time is not None else "",
            "exit_reason": t.exit_reason,
            "entry_price": round(t.entry_price, 6),
            "exit_price_final": round(t.exit_price_final, 6) if t.exit_price_final else "",
            "gross_pnl_usd": round(t.gross_pnl_usd, 4),
            "net_pnl_usd": round(t.net_pnl_usd, 4),
            "fees_usd": round(t.fees_usd, 4),
            "slippage_usd": round(t.slippage_usd, 4),
            "funding_usd": round(t.funding_usd, 4),
            "r_multiple": round(t.r_multiple, 3),
            "cost_R": round(cost_r, 3),
            "bars_held": t.bars_held,
            "MFE_R": round(mfe_r, 3),
            "MAE_R": round(mae_r, 3),
            "bars_to_MFE": b_mfe,
            "bars_to_MAE": b_mae,
            "btc_regime_at_entry": t.regime_state,
            "rs_rank_at_entry": rs_rank,
            "entry_ext_atr": entry_ext_atr,
            "volume_ratio_at_entry": vol_ratio,
            "loss_category": "|".join(_classify_loss(t, mfe_r, mae_r)),
        })
    return rows


def export_loss_autopsy_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = [
        "symbol", "side", "entry_time", "exit_time", "exit_reason",
        "entry_price", "exit_price_final", "gross_pnl_usd", "net_pnl_usd",
        "fees_usd", "slippage_usd", "funding_usd", "r_multiple", "cost_R",
        "bars_held", "MFE_R", "MAE_R", "bars_to_MFE", "bars_to_MAE",
        "btc_regime_at_entry", "rs_rank_at_entry", "entry_ext_atr",
        "volume_ratio_at_entry", "loss_category",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"Exported {len(rows)} losing trades to {path}")


def print_loss_autopsy_summary(rows):
    print("\n" + "=" * 70)
    print("LOSS AUTOPSY SUMMARY (analysis only — does not change the strategy)")
    print("=" * 70)
    if not rows:
        print("  No losing trades to autopsy.")
        return
    n = len(rows)
    total_net = sum(r["net_pnl_usd"] for r in rows)
    print(f"  Losing trades: {n}   Total net P&L: ${total_net:+.2f}")

    def _grouped(key_fn, title):
        groups = {}
        for r in rows:
            groups.setdefault(key_fn(r), []).append(r)
        print(f"\n  {title}")
        for key in sorted(groups, key=lambda k: str(k)):
            g = groups[key]
            net = sum(x["net_pnl_usd"] for x in g)
            avg_r = sum(x["r_multiple"] for x in g) / len(g)
            print(f"    {str(key):<16} count={len(g):<3} avgR={avg_r:+.2f}  net=${net:+.2f}")

    # By category (multi-label: a trade appears under each of its labels).
    cat_groups = {}
    for r in rows:
        for lab in (r["loss_category"].split("|") if r["loss_category"] else ["(unlabeled)"]):
            cat_groups.setdefault(lab, []).append(r)
    print("\n  By loss_category (multi-label; trades may appear in several)")
    for cat in sorted(cat_groups, key=lambda c: -len(cat_groups[c])):
        g = cat_groups[cat]
        net = sum(x["net_pnl_usd"] for x in g)
        avg_r = sum(x["r_multiple"] for x in g) / len(g)
        print(f"    {cat:<22} count={len(g):<3} avgR={avg_r:+.2f}  net=${net:+.2f}")

    _grouped(lambda r: r["symbol"], "By symbol")
    _grouped(lambda r: r["side"], "By side")
    _grouped(lambda r: r["exit_reason"], "By exit_reason")
    _grouped(lambda r: r["btc_regime_at_entry"], "By BTC regime at entry")

    # MFE reach distribution before failing.
    print("\n  Losers that reached a favorable excursion before failing")
    for thresh in (0.5, 1.0, 1.5):
        hit = sum(1 for r in rows if r["MFE_R"] >= thresh)
        print(f"    reached +{thresh:>3}R MFE: {hit}/{n} ({hit / n * 100:.0f}%)")


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
# Validation gates (STRATEGY_SPEC.md §9)
# ---------------------------------------------------------------------------

def _profit_factor(trades):
    gains = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
    losses = -sum(t.net_pnl_usd for t in trades if t.net_pnl_usd < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def _daily_sharpe(equity_or_close):
    """Annualized Sharpe from a time-indexed series, resampled to daily."""
    daily = equity_or_close.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0:
        return 0.0
    return float(rets.mean() / rets.std() * np.sqrt(365))


def equal_weight_hodl(data_by_symbol):
    """Equal-weight buy-and-hold of the universe, time-indexed and normalized."""
    curves = []
    for df in data_by_symbol.values():
        s = df.set_index("time")["close"].astype(float)
        curves.append(s / s.iloc[0])
    h = pd.concat(curves, axis=1).dropna().mean(axis=1)
    return h


def gate_report(trades, equity_curve, data_by_symbol):
    """Score a run against the spec's promotion gates. Returns (lines, all_pass)."""
    lines = ["", "=" * 70, "VALIDATION GATES (STRATEGY_SPEC.md §9)", "=" * 70]
    if not trades:
        lines.append("No trades — cannot evaluate. (Valid outcome; not a pass.)")
        return lines, False

    eq = pd.Series([p["equity"] for p in equity_curve],
                   index=pd.to_datetime([p["time"] for p in equity_curve]))
    hodl = equal_weight_hodl(data_by_symbol)

    # In-sample / out-of-sample split by time.
    first_t, last_t = eq.index.min(), eq.index.max()
    split_t = first_t + (last_t - first_t) * OOS_SPLIT_FRAC
    is_trades = [t for t in trades if (t.exit_time or t.entry_time) < split_t]
    oos_trades = [t for t in trades if (t.exit_time or t.entry_time) >= split_t]

    n = len(trades)
    pf = _profit_factor(trades)
    avg_r = float(np.mean([t.r_multiple for t in trades]))
    net = sum(t.net_pnl_usd for t in trades)
    max_dd = max((p["drawdown_pct"] for p in equity_curve), default=0.0)
    strat_sharpe = _daily_sharpe(eq)
    hodl_sharpe = _daily_sharpe(hodl)
    is_pf, oos_pf = _profit_factor(is_trades), _profit_factor(oos_trades)
    top_share = (max(t.net_pnl_usd for t in trades) / net) if net > 0 else float("nan")

    def fmt_pf(x):
        return "inf" if x == float("inf") else f"{x:.2f}"

    checks = []
    checks.append(("1 sample >= 30", n >= GATE_MIN_TRADES, f"{n} trades"))
    checks.append(("2 expectancy > 0", net > 0 and avg_r > 0,
                   f"net=${net:+.2f}, avgR={avg_r:+.2f}"))
    checks.append(("3 profit factor >= 1.3", pf >= GATE_MIN_PF, f"PF={fmt_pf(pf)}"))
    checks.append(("4 beats HODL Sharpe", strat_sharpe >= hodl_sharpe,
                   f"strat={strat_sharpe:.2f} vs HODL={hodl_sharpe:.2f}"))
    checks.append(("5 maxDD < 25%", max_dd < GATE_MAX_DD_PCT, f"{max_dd:.1f}%"))
    if net > 0:
        checks.append(("6 no trade > 25% P&L", top_share <= GATE_MAX_TRADE_SHARE,
                       f"top trade={top_share*100:.0f}% of net"))
    else:
        checks.append(("6 no trade > 25% P&L", False, "n/a (net P&L <= 0)"))
    if is_pf not in (0.0, float("inf")) and oos_trades:
        g7 = oos_pf >= GATE_OOS_PF_RATIO * is_pf
        checks.append(("7 OOS PF >= 0.75x IS", g7,
                       f"IS={fmt_pf(is_pf)} OOS={fmt_pf(oos_pf)} "
                       f"({len(is_trades)}/{len(oos_trades)} trades)"))
    else:
        checks.append(("7 OOS PF >= 0.75x IS", False,
                       f"insufficient split (IS={fmt_pf(is_pf)}, "
                       f"{len(is_trades)} IS / {len(oos_trades)} OOS trades)"))

    all_pass = True
    for name, passed, detail in checks:
        all_pass = all_pass and passed
        lines.append(f"  [{'PASS' if passed else 'FAIL'}] {name:<24} {detail}")
    lines.append("-" * 70)
    lines.append(f"  VERDICT: {'PROMOTE to 30-day paper trade' if all_pass else 'REJECTED'}")
    lines.append("  (Passing only qualifies for paper trading — it does not approve live.)")
    return lines, all_pass


# ---------------------------------------------------------------------------
# Regime comparison helper (default vs --strict-regime). ANALYSIS ONLY.
# ---------------------------------------------------------------------------

def _regime_run_metrics(data_by_symbol, strict):
    """Run one backtest and collapse it to the comparison metrics."""
    trades, eq = run_backtest(data_by_symbol, strict_regime=strict)
    rows = build_loss_autopsy(trades, data_by_symbol)
    if trades:
        wins = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
        loss = abs(sum(t.net_pnl_usd for t in trades if t.net_pnl_usd <= 0))
        pf = wins / loss if loss > 0 else float("inf")
        avg_r = float(np.mean([t.r_multiple for t in trades]))
        net = sum(t.net_pnl_usd for t in trades)
    else:
        pf, avg_r, net = float("nan"), float("nan"), 0.0
    cat = lambda name: sum(1 for r in rows if name in r["loss_category"])
    return {
        "trades": len(trades), "pf": pf, "avg_r": avg_r, "net": net,
        "max_dd": max((p["drawdown_pct"] for p in eq), default=0.0),
        "rat": cat("regime_against_trade"), "chop": cat("regime_chop_loss"),
    }


def compare_regime(data_by_symbol):
    """Print a side-by-side DEFAULT vs --strict-regime table on shared data."""
    print("\nRunning DEFAULT vs --strict-regime comparison (same candles) ...")
    d = _regime_run_metrics(data_by_symbol, strict=False)
    s = _regime_run_metrics(data_by_symbol, strict=True)

    def num(x, pos=False):
        if x != x:                      # NaN
            return "n/a"
        if x == float("inf"):
            return "inf"
        return f"{x:+.2f}" if pos else f"{x:.2f}"

    rows = [
        ("Trades", str(d["trades"]), str(s["trades"])),
        ("Profit factor", num(d["pf"]), num(s["pf"])),
        ("Avg R", num(d["avg_r"], pos=True), num(s["avg_r"], pos=True)),
        ("Net P&L $", num(d["net"], pos=True), num(s["net"], pos=True)),
        ("Max DD %", num(d["max_dd"]), num(s["max_dd"])),
        ("regime_against_trade losses", str(d["rat"]), str(s["rat"])),
        ("regime_chop_loss losses", str(d["chop"]), str(s["chop"])),
    ]
    print("\n" + "=" * 66)
    print("REGIME COMPARISON  (default = canonical; strict = experimental)")
    print("=" * 66)
    print(f"{'Metric':<30}{'Default':>16}{'--strict-regime':>18}")
    print("-" * 66)
    for label, a, b in rows:
        print(f"{label:<30}{a:>16}{b:>18}")
    print("-" * 66)
    print("Strict is opt-in/experimental. If a column shows <30 trades, its "
          "PF / Avg R are not yet reliable (spec §9 gate).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "")
    p.add_argument("--days", type=int, default=DAYS_BACK,
                   help=f"Days of 6H history to fetch (default {DAYS_BACK}; "
                        f"~1460 ≈ 4 years for a real sample)")
    p.add_argument("--trade-assets", nargs="*", default=None, metavar="SYM",
                   help="DIAGNOSTIC: restrict which symbols may be entered "
                        "(e.g. --trade-assets ETH-USD). Regime/RS still use all "
                        "of BTC/ETH/SOL. Default: all.")
    p.add_argument("--long-only", action="store_true",
                   help="DIAGNOSTIC: suppress short entries (risk-off -> no trade).")
    p.add_argument("--strict-regime", action="store_true",
                   help="EXPERIMENTAL: stricter BTC daily master filter "
                        "(risk_on: close>EMA50*1.02 & slope>0; "
                        "risk_off: close<EMA50*0.98 & slope<0; else neutral).")
    p.add_argument("--compare-regime", action="store_true",
                   help="Run DEFAULT vs --strict-regime on the same data and "
                        "print a side-by-side metrics table (no CSV export).")
    args = p.parse_args(argv)
    days_back = args.days

    trade_assets = args.trade_assets
    if trade_assets:
        bad = [s for s in trade_assets if s not in ASSETS]
        if bad:
            print(f"ERROR: --trade-assets {bad} not in universe {ASSETS}")
            return
    is_diagnostic = bool(trade_assets) or args.long_only or args.strict_regime

    print(f"Backtest config: {days_back} days, 6H bars, assets: {', '.join(ASSETS)}")
    print(f"Regime: BTC 1D EMA{REGIME_EMA_SLOW} (3-state)  |  RS: {RS_LOOKBACK_DAYS}d return")
    print(f"Entry: {BREAKOUT_LOOKBACK}-bar breakout + strong close + BTC confirm")
    print(f"Risk: {RISK_PCT*100:.0f}%/trade, daily-loss {MAX_DAILY_LOSS_PCT}%, "
          f"weekly-loss {MAX_WEEKLY_LOSS_PCT}%, max {MAX_OPEN_POSITIONS} open")
    if is_diagnostic:
        print(f"** DIAGNOSTIC ISOLATION (not the deployed strategy): "
              f"trade_assets={trade_assets or 'all'}, long_only={args.long_only}, "
              f"strict_regime={args.strict_regime} **")
    print()

    data = {}
    for symbol in ASSETS:
        df = load_candles(symbol, TIMEFRAME_SECONDS, days_back)
        raw_count = len(df)
        df = drop_incomplete_candles(df, TIMEFRAME_SECONDS)
        if df.empty:
            print(f"  {symbol}: 0 bars after dropping incomplete — skipping")
            continue
        dropped = raw_count - len(df)
        df = add_indicators(df)
        data[symbol] = df
        suffix = f" (dropped {dropped} incomplete)" if dropped else ""
        print(f"  {symbol}: {len(df)} bars "
              f"({df['time'].min().date()} to {df['time'].max().date()}){suffix}")

    if "BTC-USD" not in data:
        print("ERROR: BTC-USD data is required for the regime filter — aborting.")
        return

    if args.compare_regime:
        compare_regime(data)
        return

    print("\nRunning scanner backtest...")
    trades, equity_curve = run_backtest(data, trade_assets=trade_assets,
                                        long_only=args.long_only,
                                        strict_regime=args.strict_regime)
    print(f"Backtest complete. {len(trades)} trades simulated.")

    export_trades_csv(trades, TRADES_CSV)
    export_equity_csv(equity_curve, EQUITY_CSV)
    autopsy_rows = build_loss_autopsy(trades, data)
    export_loss_autopsy_csv(autopsy_rows, LOSS_AUTOPSY_CSV)
    report_summary(trades, equity_curve)
    lines, _ = gate_report(trades, equity_curve, data)
    print("\n".join(lines))
    print_loss_autopsy_summary(autopsy_rows)


if __name__ == "__main__":
    main()
