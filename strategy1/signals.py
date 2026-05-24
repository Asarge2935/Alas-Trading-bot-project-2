"""Selectable entry/exit signal generators.

Each generator maps a price series to a directional signal Series of
{"long", "short", "flat"} — the same format the backtester consumes —
so they are interchangeable. Pick one with the backtester's `--signal`
flag.

Available signals:
    trend        SMA(50) trend + slope (the regime.directional_signal)
    ema_cross    EMA(fast) vs EMA(slow): long above / short below
    macd         MACD line vs signal line: long above / short below
    rsi          RSI mean-reversion: long oversold / short overbought
    bollinger    Bollinger breakout: long above upper / short below lower

DISCIPLINE NOTE (kept deliberately): these are offered as SEPARATE,
single-hypothesis signals — not as a stack to be tuned together. Run
one at a time through the gates. Stacking several and tuning until the
curve looks good is the curve-fitting trap the project was created to
avoid. The point of having them all available is to test each cleanly,
not to combine them blindly.

All generators are backward-looking; the backtester additionally shifts
the chosen signal one bar before acting, so there is no look-ahead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy1 import indicators as ind
from strategy1.regime import directional_signal


def _label(long_cond: pd.Series, short_cond: pd.Series, index) -> pd.Series:
    long_cond = long_cond.reindex(index).fillna(False)
    short_cond = short_cond.reindex(index).fillna(False)
    arr = np.where(long_cond, "long", np.where(short_cond, "short", "flat"))
    return pd.Series(arr, index=index, name="signal")


def ema_cross_signal(close: pd.Series, fast: int = 20, slow: int = 50) -> pd.Series:
    """Long when EMA(fast) > EMA(slow), short when below. A trend signal."""
    ef, es = ind.ema(close, fast), ind.ema(close, slow)
    return _label(ef > es, ef < es, close.index)


def macd_signal(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """Long when MACD line > signal line, short when below. Momentum."""
    m = ind.macd(close, fast, slow, signal)
    return _label(m["macd"] > m["signal"], m["macd"] < m["signal"], close.index)


def rsi_signal(close: pd.Series, period: int = 14, low: float = 30.0,
               high: float = 70.0) -> pd.Series:
    """RSI mean-reversion: long while oversold (<low), short while overbought (>high).

    Note this is a *counter-trend* hypothesis — the opposite stance to
    the trend/ema/macd signals. Worth testing precisely because it is
    different, not because it can be blended with them.
    """
    r = ind.rsi(close, period)
    return _label(r < low, r > high, close.index)


def bollinger_signal(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
    """Bollinger breakout: long above the upper band, short below the lower."""
    b = ind.bollinger(close, window, num_std)
    return _label(close > b["upper"], close < b["lower"], close.index)


def trend_signal(close: pd.Series, vol_aware: bool = False) -> pd.Series:
    """SMA(50) trend + slope — delegates to regime.directional_signal."""
    return directional_signal(close, vol_aware=vol_aware)


# name -> generator. trend takes vol_aware; others take only close (+ defaults).
SIGNALS = {
    "trend": trend_signal,
    "ema_cross": ema_cross_signal,
    "macd": macd_signal,
    "rsi": rsi_signal,
    "bollinger": bollinger_signal,
}


def build_signal(name: str, close: pd.Series, vol_aware: bool = False) -> pd.Series:
    """Dispatch to the named signal generator."""
    if name not in SIGNALS:
        raise ValueError(f"unknown signal '{name}'; choices: {sorted(SIGNALS)}")
    if name == "trend":
        return trend_signal(close, vol_aware=vol_aware)
    return SIGNALS[name](close)
