"""BTC directional backtester for Strategy 2.

See docs/STRATEGY_2_BTC_DIRECTIONAL_SPEC.md.

Runs three variants of a single-instrument BTC trend strategy plus a
buy-and-hold benchmark, and scores each against the spec's gates:

    long_flat    long in uptrend, flat otherwise   (spot-executable)
    short_flat   short in downtrend, flat otherwise (research-only)
    long_short   long up / short down / flat        (research-only)

Conventions (no look-ahead):
- Signal is computed on the daily close of day t.
- The resulting target position is held during day t+1 (the signal
  series is shifted forward one bar). This approximates "act at next
  open"; the only thing it misses is the overnight gap, a known and
  minor simplification documented here.
- Returns are close-to-close. Costs are charged when the position
  changes. Shorts also pay a daily funding/borrow drag (placeholder).
- The 2xATR stop is checked on the close: if adverse excursion from
  entry exceeds the stop, the position is flattened the next bar.

Usage:
    python -m strategy1.btc_backtest --in data_cache/
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from strategy1.regime import directional_signal
from strategy1.signals import build_signal, SIGNALS
from strategy1.universe import load_all


# --- cost / risk parameters -----------------------------------------------
# The bot trades Coinbase DERIVATIVES (perps), so the relevant fees are
# the derivatives rates — NOT the much higher Coinbase spot fees
# (spot Intro 1 is 0.60% maker / 1.20% taker). Per the user's actual
# Coinbase Advanced fee schedule:
#
#   Derivatives, Intro 1 (current tier, <$10k 30-day volume/balance):
#       0.095% maker / 0.100% taker  per contract
#       + $0.15 minimum per contract (NFA/exchange/clearing)
#       + 0.10% liquidation fee if Coinbase force-closes a position
#
# Notes:
# - For nano BTC (~1/100 BTC ≈ $800 notional), 0.10% = ~$0.80 > the
#   $0.15 floor, so the per-contract minimum does not bind at sane
#   sizes. It is NOT modeled here; it only matters for sub-$150
#   positions. Documented, not coded — see spec.
# - The maker/taker gap on perps is tiny (0.095 vs 0.100), unlike spot.
#   So limit-order optimization barely helps here. We model taker.
# - The liquidation fee is avoided by the stop + no-high-leverage rule;
#   not modeled.
# - Derivatives fees may also ladder down with volume; only the Intro 1
#   rate is known, so that is the default.
FEE_PRESETS = {
    "perp_taker": 0.0010,    # 0.100% — Intro 1 derivatives taker (default)
    "perp_maker": 0.00095,   # 0.095% — if filling as maker (limit orders)
    "spot_taker": 0.0120,    # 1.20% — Coinbase SPOT taker, for contrast only; do NOT use for perps
}
DEFAULT_SLIPPAGE = 0.0005       # 0.05% per side
DEFAULT_FUNDING_DAILY = 0.0003  # 0.03%/day — PLACEHOLDER (perp), needs real series
DEFAULT_ROLL_COST = 0.0025      # per roll (dated future): ~2 fee legs + small basis, PLACEHOLDER
DEFAULT_ROLL_DAYS = 30          # monthly expiry → roll cadence while in a position
ATR_WINDOW = 14
ATR_STOP_MULT = 2.0
MAX_DRAWDOWN = 0.25             # MTM equity halt threshold
OOS_SPLIT = "2025-01-01"        # in-sample before, out-of-sample on/after


@dataclass
class CostModel:
    taker_fee: float = FEE_PRESETS["perp_taker"]
    slippage: float = DEFAULT_SLIPPAGE
    # instrument cost structure:
    #   "perp"          → continuous funding drag (funding_daily on shorts)
    #   "dated_future"  → no funding; a roll_cost every roll_days while in a position
    instrument: str = "perp"
    funding_daily: float = DEFAULT_FUNDING_DAILY
    roll_cost: float = DEFAULT_ROLL_COST
    roll_days: int = DEFAULT_ROLL_DAYS

    @property
    def per_side(self) -> float:
        return self.taker_fee + self.slippage


MODE_MAP = {
    "long_flat":  {"long": 1, "short": 0, "flat": 0},
    "short_flat": {"long": 0, "short": -1, "flat": 0},
    "long_short": {"long": 1, "short": -1, "flat": 0},
}


@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    side: int               # +1 long, -1 short
    entry_equity: float
    exit_equity: float
    bars_held: int
    stopped: bool

    @property
    def contribution(self) -> float:
        return self.exit_equity - self.entry_equity


@dataclass
class VariantResult:
    mode: str
    equity: pd.Series                  # full MTM equity curve (index = date)
    trades: list[Trade] = field(default_factory=list)
    halted_date: pd.Timestamp | None = None


def _atr(df: pd.DataFrame, window: int = ATR_WINDOW) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=window, min_periods=window).mean()


def run_variant(df: pd.DataFrame, signal: pd.Series, mode: str,
                costs: CostModel | None = None) -> VariantResult:
    """Walk the bars and produce a MTM equity curve plus trade list."""
    costs = costs or CostModel()
    cost_per_side = costs.per_side
    funding_daily = costs.funding_daily
    is_dated = costs.instrument == "dated_future"
    roll_cost = costs.roll_cost
    roll_days = costs.roll_days
    bars_since_roll = 0
    pos_map = MODE_MAP[mode]
    target = signal.map(pos_map).shift(1).fillna(0).astype(int)  # act next bar
    atr = _atr(df)
    closes = df["close"].to_numpy()
    atr_arr = atr.to_numpy()
    target_arr = target.to_numpy()
    dates = df.index

    equity = 1.0
    peak = 1.0
    pos = 0
    entry_px = None
    entry_equity = None
    entry_date = None
    entry_idx = None
    stop_frac = None
    halted_date = None

    curve = np.empty(len(df))
    trades: list[Trade] = []

    for t in range(len(df)):
        close = closes[t]

        # 1. Mark current position to market over yesterday->today close.
        if t > 0 and pos != 0:
            r = close / closes[t - 1] - 1.0
            pnl = pos * r
            if not is_dated and pos == -1:
                pnl -= funding_daily            # perp: funding drag on shorts
            equity *= (1.0 + pnl)
            if is_dated:                        # dated future: periodic roll cost
                bars_since_roll += 1
                if bars_since_roll >= roll_days:
                    equity *= (1.0 - roll_cost)
                    bars_since_roll = 0

        desired = target_arr[t]

        # 2. Stop check on the close (adverse excursion from entry).
        if pos != 0 and entry_px is not None and stop_frac is not None:
            if pos == 1:
                adverse = (entry_px - close) / entry_px
            else:
                adverse = (close - entry_px) / entry_px
            stopped_now = adverse >= stop_frac
        else:
            stopped_now = False
        if stopped_now:
            desired = 0

        # 3. Rebalance to desired position, charging cost per leg traded.
        if desired != pos:
            legs = abs(desired - pos)
            equity *= (1.0 - cost_per_side * legs)
            if pos != 0:
                trades.append(Trade(
                    entry_date=entry_date, exit_date=dates[t], side=pos,
                    entry_equity=entry_equity, exit_equity=equity,
                    bars_held=t - entry_idx, stopped=stopped_now,
                ))
            if desired != 0:
                entry_px = close
                entry_equity = equity
                entry_date = dates[t]
                entry_idx = t
                a = atr_arr[t]
                stop_frac = (ATR_STOP_MULT * a / close) if (a == a and close > 0) else None
                bars_since_roll = 0          # fresh position → reset roll clock
            else:
                entry_px = entry_equity = entry_date = entry_idx = stop_frac = None
            pos = desired

        # 4. Drawdown halt (record first breach; keep curve going for reporting).
        peak = max(peak, equity)
        if halted_date is None and equity / peak - 1.0 <= -MAX_DRAWDOWN:
            halted_date = dates[t]

        curve[t] = equity

    # Close any open position at the final bar for accounting.
    if pos != 0:
        trades.append(Trade(
            entry_date=entry_date, exit_date=dates[-1], side=pos,
            entry_equity=entry_equity, exit_equity=equity,
            bars_held=len(df) - 1 - entry_idx, stopped=False,
        ))

    return VariantResult(mode=mode, equity=pd.Series(curve, index=dates), trades=trades,
                         halted_date=halted_date)


# --- metrics --------------------------------------------------------------

def _sharpe(equity: pd.Series) -> float:
    rets = equity.pct_change().dropna()
    if rets.std() == 0 or len(rets) < 2:
        return 0.0
    return float(rets.mean() / rets.std() * np.sqrt(365))


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def _profit_factor(trades: list[Trade]) -> float:
    gains = sum(t.contribution for t in trades if t.contribution > 0)
    losses = -sum(t.contribution for t in trades if t.contribution < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def _max_trade_share(trades: list[Trade]) -> float:
    net = sum(t.contribution for t in trades)
    if net <= 0 or not trades:
        return float("nan")
    return max(t.contribution for t in trades) / net


def buy_and_hold(df: pd.DataFrame) -> pd.Series:
    c = df["close"]
    return c / c.iloc[0]


@dataclass
class VariantMetrics:
    mode: str
    n_trades: int
    total_return: float
    profit_factor: float
    sharpe: float
    max_dd: float
    max_trade_share: float
    is_pf: float
    oos_pf: float
    yearly: dict[int, float]
    halted_date: pd.Timestamp | None


def _trades_in(trades: list[Trade], lo, hi) -> list[Trade]:
    return [t for t in trades if lo <= t.exit_date < hi]


def evaluate(result: VariantResult, df: pd.DataFrame) -> VariantMetrics:
    eq = result.equity
    split = pd.Timestamp(OOS_SPLIT, tz="UTC")
    is_trades = [t for t in result.trades if t.exit_date < split]
    oos_trades = [t for t in result.trades if t.exit_date >= split]

    yearly: dict[int, float] = {}
    for year in sorted(set(eq.index.year)):
        ysub = eq[eq.index.year == year]
        if len(ysub) >= 2:
            yearly[year] = float(ysub.iloc[-1] / ysub.iloc[0] - 1.0)

    return VariantMetrics(
        mode=result.mode,
        n_trades=len(result.trades),
        total_return=float(eq.iloc[-1] - 1.0),
        profit_factor=_profit_factor(result.trades),
        sharpe=_sharpe(eq),
        max_dd=_max_drawdown(eq),
        max_trade_share=_max_trade_share(result.trades),
        is_pf=_profit_factor(is_trades),
        oos_pf=_profit_factor(oos_trades),
        yearly=yearly,
        halted_date=result.halted_date,
    )


def gate_report(m: VariantMetrics, hodl_sharpe: float) -> list[tuple[str, bool, str]]:
    """Return [(gate_name, passed, detail)] per spec §7."""
    gates: list[tuple[str, bool, str]] = []

    g1 = m.n_trades >= 30
    gates.append(("1 sample >=30", g1, f"{m.n_trades} trades"))

    g2 = m.profit_factor >= 1.3
    pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
    gates.append(("2 profit_factor >=1.3", g2, f"PF={pf}"))

    g3 = m.sharpe >= 1.1 * hodl_sharpe
    gates.append(("3 beats HODL Sharpe", g3, f"{m.sharpe:.2f} vs 1.1x{hodl_sharpe:.2f}={1.1*hodl_sharpe:.2f}"))

    g4 = m.max_dd > -MAX_DRAWDOWN
    gates.append(("4 maxDD <25%", g4, f"{m.max_dd*100:.1f}%"))

    g5 = (m.max_trade_share != m.max_trade_share) or (m.max_trade_share <= 0.25)
    share = "n/a" if m.max_trade_share != m.max_trade_share else f"{m.max_trade_share*100:.0f}%"
    gates.append(("5 no trade >25% P&L", g5, f"max trade={share}"))

    down_years = [y for y, r in m.yearly.items() if y in (2022,)]
    g6_detail = ", ".join(f"{y}:{r*100:+.0f}%" for y, r in sorted(m.yearly.items()))
    g6 = all(r > -0.5 for r in m.yearly.values()) if m.yearly else False
    gates.append(("6 no catastrophic year", g6, g6_detail))

    oos = "inf" if m.oos_pf == float("inf") else f"{m.oos_pf:.2f}"
    is_pf = "inf" if m.is_pf == float("inf") else f"{m.is_pf:.2f}"
    g7 = m.oos_pf >= 0.75 * m.is_pf if m.is_pf not in (0.0, float("inf")) else False
    gates.append(("7 OOS PF >=0.75xIS", g7, f"IS={is_pf} OOS={oos}"))

    return gates


def format_report(results: dict[str, VariantResult], df: pd.DataFrame,
                  costs: CostModel | None = None) -> str:
    costs = costs or CostModel()
    hodl = buy_and_hold(df)
    hodl_sharpe = _sharpe(hodl)
    hodl_ret = float(hodl.iloc[-1] - 1.0)
    hodl_dd = _max_drawdown(hodl)

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("BTC Directional Backtest — Strategy 2")
    lines.append(f"Period: {df.index.min().date()} → {df.index.max().date()}  ({len(df)} bars)")
    if costs.instrument == "dated_future":
        carry = f"dated_future: roll {costs.roll_cost*100:.2f}% every {costs.roll_days}d"
    else:
        carry = f"perp: short funding {costs.funding_daily*100:.2f}%/day"
    lines.append(f"Costs: taker={costs.taker_fee*100:.2f}% + slippage={costs.slippage*100:.2f}% "
                 f"= {costs.per_side*100:.2f}%/side  |  {carry}")
    lines.append("=" * 72)
    lines.append(f"Benchmark buy-and-hold BTC: total_return={hodl_ret*100:+.1f}%  "
                 f"Sharpe={hodl_sharpe:.2f}  maxDD={hodl_dd*100:.1f}%")
    lines.append("")

    for mode in ("long_flat", "short_flat", "long_short"):
        res = results[mode]
        m = evaluate(res, df)
        tag = "SPOT-EXECUTABLE" if mode == "long_flat" else "research-only (needs perp venue)"
        lines.append("-" * 72)
        lines.append(f"[{mode}]  {tag}")
        lines.append(f"  trades={m.n_trades}  total_return={m.total_return*100:+.1f}%  "
                     f"Sharpe={m.sharpe:.2f}  maxDD={m.max_dd*100:.1f}%")
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        lines.append(f"  profit_factor={pf}  "
                     f"max_trade_share={'n/a' if m.max_trade_share!=m.max_trade_share else f'{m.max_trade_share*100:.0f}%'}")
        if m.halted_date is not None:
            lines.append(f"  ** MAX-DRAWDOWN HALT first breached {m.halted_date.date()} **")
        lines.append("  gates:")
        all_pass = True
        for name, passed, detail in gate_report(m, hodl_sharpe):
            mark = "PASS" if passed else "FAIL"
            all_pass = all_pass and passed
            lines.append(f"    [{mark}] {name:<24} {detail}")
        lines.append(f"  VERDICT: {'PROMOTE to paper' if all_pass else 'REJECTED'}")
        lines.append("")

    lines.append("-" * 72)
    lines.append("Reminder: short_flat and long_short are NOT executable on the")
    lines.append("current spot-only Crypto.com App account. They are measured here")
    lines.append("only to decide whether opening a perp venue is justified.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--in", dest="in_dir", type=Path, default=Path("data_cache"))
    p.add_argument("--product", default="BTC-USD")
    p.add_argument("--signal", choices=sorted(SIGNALS), default="trend",
                   help="Entry/exit signal: trend (default), ema_cross, macd, rsi, bollinger")
    p.add_argument("--vol-aware", action="store_true",
                   help="trend signal only: Definition B-dir (vol stand-down on longs)")
    p.add_argument("--fee-preset", choices=sorted(FEE_PRESETS), default="perp_taker",
                   help="Coinbase fee: perp_taker (0.100%, default), "
                        "perp_maker (0.095%), spot_taker (1.20%, contrast only)")
    p.add_argument("--taker-fee", type=float, default=None,
                   help="Override fee per side as a fraction (e.g. 0.001 for 0.10%)")
    p.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE)
    p.add_argument("--instrument", choices=["perp", "dated_future"], default="perp",
                   help="perp (funding) or dated_future (roll cost at each expiry)")
    p.add_argument("--funding-daily", type=float, default=DEFAULT_FUNDING_DAILY,
                   help="Perp daily short funding drag (PLACEHOLDER until real series)")
    p.add_argument("--roll-cost", type=float, default=DEFAULT_ROLL_COST,
                   help="Dated-future cost per roll (PLACEHOLDER; ~2 fee legs + basis)")
    args = p.parse_args(argv)

    all_data = load_all(args.in_dir)
    if args.product not in all_data:
        print(f"ERROR: {args.product} not in {args.in_dir}", file=sys.stderr)
        return 2

    taker = args.taker_fee if args.taker_fee is not None else FEE_PRESETS[args.fee_preset]
    costs = CostModel(taker_fee=taker, slippage=args.slippage, instrument=args.instrument,
                      funding_daily=args.funding_daily, roll_cost=args.roll_cost)

    df = all_data[args.product].sort_index()
    signal = build_signal(args.signal, df["close"], vol_aware=args.vol_aware)

    results = {mode: run_variant(df, signal, mode, costs) for mode in MODE_MAP}
    print(format_report(results, df, costs))
    sig_note = f"{args.signal}" + (" (vol-aware)" if args.vol_aware and args.signal == "trend" else "")
    fee_note = (f"--taker-fee {taker}" if args.taker_fee is not None
                else f"--fee-preset {args.fee_preset}")
    print(f"\n(signal: {sig_note}  |  instrument: {args.instrument}  |  fees: {fee_note})")
    print("Tip: try --signal {trend,ema_cross,macd,rsi,bollinger}, one at a time.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
