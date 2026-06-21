"""Technical indicators as pure, no-look-ahead functions.

These are the indicators exposed in the Coinbase trading UI (RSI, MA,
EMA, MACD, Bollinger Bands), implemented here as reusable building
blocks. Each operates on a pandas Series of closes (or an OHLC frame
where noted) and returns aligned output indexed the same way.

IMPORTANT — read before wiring any of these into a strategy:

    Having indicators available is not the same as having edge. The
    project's founding brief explicitly chose to move *away* from
    "random indicator combinations" toward tested, behavior-based
    edges. Bolting several of these onto entry conditions and tuning
    them until the backtest looks good is exactly the curve-fitting
    trap to avoid.

    The disciplined use: pick ONE indicator, write a falsifiable
    hypothesis for why it should have edge on BTC, backtest it alone
    under realistic costs, and run it through the gates in
    docs/STRATEGY_2_BTC_DIRECTIONAL_SPEC.md §7. Keep it only if it
    passes. See that spec and the README.

All functions are backward-looking (rolling / ewm), so they introduce
no look-ahead on their own. Look-ahead is still possible at the
strategy level if you act on the same bar you computed the signal
from — the backtester guards against that by shifting signals one bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    return close.rolling(window=window, min_periods=window).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (recursive, adjust=False)."""
    return close.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI.

    Endpoint handling so one-sided runs don't produce NaN:
      - average loss == 0 (pure up-move)   -> RSI = 100
      - average gain == 0 (pure down-move) -> RSI = 0
      - both == 0 (flat)                   -> RSI = 50
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing == EMA with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(avg_gain != 0, 0.0)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return out.rename("rsi")


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line, signal line, and histogram.

    Returns a DataFrame with columns macd / signal / hist.
    """
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands.

    Returns a DataFrame with columns mid / upper / lower / pctb / bandwidth.
    - pctb: position of price within the bands (0 = lower, 1 = upper).
    - bandwidth: (upper - lower) / mid, a volatility-compression gauge.
    """
    mid = sma(close, window)
    std = close.rolling(window=window, min_periods=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = upper - lower
    pctb = (close - lower) / width.where(width != 0, np.nan)
    bandwidth = width / mid.where(mid != 0, np.nan)
    return pd.DataFrame({
        "mid": mid, "upper": upper, "lower": lower,
        "pctb": pctb, "bandwidth": bandwidth,
    })


def main(argv: list[str] | None = None) -> int:
    """Print the latest indicator values for a cached product (sanity check)."""
    import argparse
    import sys
    from pathlib import Path
    from strategy1.universe import load_all

    p = argparse.ArgumentParser(description="Print latest indicator values for a product.")
    p.add_argument("--in", dest="in_dir", type=Path, default=Path("data_cache"))
    p.add_argument("--product", default="BTC-USD")
    args = p.parse_args(argv)

    data = load_all(args.in_dir)
    if args.product not in data:
        print(f"ERROR: {args.product} not in {args.in_dir}", file=sys.stderr)
        return 2
    c = data[args.product]["close"]

    last = c.index[-1].date()
    m = macd(c).iloc[-1]
    b = bollinger(c).iloc[-1]
    print(f"{args.product} latest indicators (as of {last}, daily bars):")
    print(f"  close       {c.iloc[-1]:.2f}")
    print(f"  SMA(50)     {sma(c, 50).iloc[-1]:.2f}")
    print(f"  EMA(20)     {ema(c, 20).iloc[-1]:.2f}")
    print(f"  RSI(14)     {rsi(c).iloc[-1]:.1f}")
    print(f"  MACD        macd={m['macd']:.2f} signal={m['signal']:.2f} hist={m['hist']:.2f}")
    print(f"  Bollinger   mid={b['mid']:.2f} upper={b['upper']:.2f} lower={b['lower']:.2f} %b={b['pctb']:.2f}")
    print("\nNote: these are diagnostics. An indicator on a chart is not an edge.")
    print("Test one as a hypothesis through the gates before trusting it.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
