"""BTC regime classifier for Strategy 1.

Defines two parallel regime definitions per the spec. Both are pure
functions of BTC's daily close history; they produce a date-indexed
Series of labels in {"risk_on", "risk_off"}.

Definition A (simple):
    risk_on iff close > SMA(50) AND SMA(50) is higher than 10 days ago.

Definition B (vol-aware):
    risk_on iff Definition A AND realized vol(20) is not in the top
    20% of its trailing 1-year distribution. The intuition is that a
    high-vol BTC tape is stress-like and a bad time to add long alts.

The spec mandates that both definitions are tested independently
in-sample, the winner picked, and then re-tested on out-of-sample.
This module produces the inputs; the selection happens in the
backtest report, not here.
"""

from __future__ import annotations

import pandas as pd
import numpy as np


SMA_WINDOW = 50
SMA_SLOPE_LOOKBACK = 10
VOL_WINDOW = 20
VOL_PERCENTILE_WINDOW = 365
VOL_PERCENTILE_THRESHOLD = 0.80  # top 20%


def _sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window=window, min_periods=window).mean()


def _realized_vol(close: pd.Series, window: int) -> pd.Series:
    """Annualized rolling stdev of daily log returns."""
    rets = np.log(close).diff()
    return rets.rolling(window=window, min_periods=window).std() * np.sqrt(365)


def regime_a(btc_close: pd.Series) -> pd.Series:
    """Simple trend regime. Returns a Series of 'risk_on' / 'risk_off'.

    `risk_on` requires:
      - BTC close > SMA(50)
      - SMA(50) today > SMA(50) 10 days ago (slope positive)

    Bars where SMA(50) is not yet defined (first 50 days) get labeled
    'risk_off' as a conservative default. Bars where the slope cannot
    yet be evaluated (first 60 days) likewise.
    """
    close = btc_close.astype(float)
    sma = _sma(close, SMA_WINDOW)
    slope_up = sma > sma.shift(SMA_SLOPE_LOOKBACK)
    above = close > sma
    risk_on = (above & slope_up).fillna(False)
    return pd.Series(
        np.where(risk_on, "risk_on", "risk_off"),
        index=close.index,
        name="regime_a",
    )


def regime_b(btc_close: pd.Series) -> pd.Series:
    """Vol-aware regime. Same as A, plus a stress-vol filter.

    Adds: realized 20-day vol must not be in the top 20% of its
    trailing 1Y distribution. When BTC vol spikes (e.g. macro shock,
    deleveraging), the strategy stands down even if the trend filter
    still says risk_on.
    """
    close = btc_close.astype(float)
    base = regime_a(close)

    vol = _realized_vol(close, VOL_WINDOW)
    vol_pctile = vol.rolling(window=VOL_PERCENTILE_WINDOW, min_periods=VOL_PERCENTILE_WINDOW).rank(pct=True)
    not_stressed = vol_pctile < VOL_PERCENTILE_THRESHOLD

    risk_on = (base == "risk_on") & not_stressed.fillna(False)
    return pd.Series(
        np.where(risk_on, "risk_on", "risk_off"),
        index=close.index,
        name="regime_b",
    )


def summarize(regime: pd.Series) -> dict:
    """Quick stats for sanity-checking a regime series."""
    total = len(regime)
    if total == 0:
        return {"total": 0, "risk_on": 0, "risk_off": 0, "risk_on_pct": 0.0}
    n_on = int((regime == "risk_on").sum())
    return {
        "total": total,
        "risk_on": n_on,
        "risk_off": total - n_on,
        "risk_on_pct": n_on / total,
    }


def _yearly_breakdown(regime: pd.Series) -> pd.DataFrame:
    """Per calendar-year risk-on percentage. Useful eyeball check."""
    df = regime.to_frame("regime")
    df["year"] = df.index.year
    df["is_on"] = (df["regime"] == "risk_on").astype(int)
    grouped = df.groupby("year")
    out = grouped.agg(total=("is_on", "size"), risk_on=("is_on", "sum"))
    out["risk_on_pct"] = out["risk_on"] / out["total"]
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    from pathlib import Path

    from strategy1.universe import load_all

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--in", dest="in_dir", type=Path, default=Path("data_cache"))
    p.add_argument("--product", default="BTC-USD",
                   help="BTC product id (default: BTC-USD)")
    args = p.parse_args(argv)

    all_data = load_all(args.in_dir)
    if args.product not in all_data:
        print(f"ERROR: {args.product} not in {args.in_dir}", file=sys.stderr)
        return 2
    btc = all_data[args.product]
    a = regime_a(btc["close"])
    b = regime_b(btc["close"])

    print(f"Regime A on {args.product}:")
    s = summarize(a)
    print(f"  {s['risk_on']}/{s['total']} risk-on bars ({s['risk_on_pct']*100:.1f}%)")
    print()
    print(f"Regime B on {args.product}:")
    s = summarize(b)
    print(f"  {s['risk_on']}/{s['total']} risk-on bars ({s['risk_on_pct']*100:.1f}%)")
    print()
    print("Yearly breakdown (Regime A | Regime B risk-on %):")
    ya = _yearly_breakdown(a)["risk_on_pct"]
    yb = _yearly_breakdown(b)["risk_on_pct"]
    for year in sorted(set(ya.index) | set(yb.index)):
        a_pct = ya.get(year, float("nan"))
        b_pct = yb.get(year, float("nan"))
        print(f"  {year}:  A={a_pct*100:5.1f}%   B={b_pct*100:5.1f}%")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
