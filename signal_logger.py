"""
SIGNAL-ONLY LOGGER — infrastructure only.

  ** SIGNAL-ONLY MODE: no orders, no exchange connection, no paper trading,
  no live trading. No API credentials. No keys required. **

Runs the EXISTING ETH 6H strict-regime breakout + the FROZEN failure-to-separate
exit (K=2 / +0.5R) through the existing backtest engine, then writes each
entry/exit as a structured JSONL event for future replay/paper-parity work.
Does NOT change any strategy rule; does NOT tune; does NOT advance any candidate.

Modes:
  replay     : built-in Coinbase data (network at runtime; no API key)
  csv-replay : --eth-csv/--btc-csv/--sol-csv supplied; fully independent

Run:
  python3 signal_logger.py --mode replay --asset ETH-USD --days 1460 \\
      --output logs/signals_eth_replay.jsonl

  python3 signal_logger.py --mode csv-replay --eth-csv ~/eth.csv \\
      --btc-csv ~/btc.csv --sol-csv ~/sol.csv --label binance_fully_indep \\
      --output logs/signals_eth_binance_replay.jsonl
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
from datetime import timezone

import backtest as bt
from eth_failure_to_separate_exit_diagnostic import make_fts_exit, K_BARS, R_THRESH
from ohlcv_csv_validation_harness import load_ohlcv_csv, resample_ohlcv

STRATEGY_NAME = "eth_strict_breakout_fts_exit"
STRATEGY_VERSION = "1.0.0"
ASSET = "ETH-USD"
TIMEFRAME = "6H"
SUPPORTED_MODES = ("replay", "csv-replay")
SAFETY_BANNER = ("** SIGNAL-ONLY MODE: no orders, no exchange connection, "
                 "no paper trading, no live trading. **")


def _config_dict():
    """Frozen strategy config — anything that, if changed, must change behavior."""
    return {
        "strategy_name": STRATEGY_NAME, "strategy_version": STRATEGY_VERSION,
        "timeframe_seconds": bt.TIMEFRAME_SECONDS,
        "regime_ema_slow": bt.REGIME_EMA_SLOW, "regime_ema_fast": bt.REGIME_EMA_FAST,
        "regime_slope_days": bt.REGIME_SLOPE_DAYS, "neutral_band_pct": bt.NEUTRAL_BAND_PCT,
        "ema20_below_limit": bt.EMA20_BELOW_LIMIT, "flat_slope_eps": bt.FLAT_SLOPE_EPS,
        "rs_lookback_days": bt.RS_LOOKBACK_DAYS,
        "breakout_lookback": bt.BREAKOUT_LOOKBACK, "strong_close_frac": bt.STRONG_CLOSE_FRAC,
        "volume_multiplier": bt.VOLUME_MULTIPLIER, "ema_trend": bt.EMA_TREND,
        "atr_period": bt.ATR_PERIOD, "atr_regime_period": bt.ATR_REGIME_PERIOD,
        "atr_regime_multiple": bt.ATR_REGIME_MULTIPLE,
        "stop_atr_mult": bt.STOP_ATR_MULT, "partial_r": bt.PARTIAL_R,
        "trail_atr_mult": bt.TRAIL_ATR_MULT, "time_stop_bars": bt.TIME_STOP_BARS,
        "risk_pct": bt.RISK_PCT, "max_open_positions": bt.MAX_OPEN_POSITIONS,
        "fts_k_bars": K_BARS, "fts_r_threshold": R_THRESH,
        "strict_regime": True, "long_only": True, "setup": "breakout",
    }


def _hash(s):
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _now_iso():
    return datetime.datetime.now(timezone.utc).isoformat()


def _build_data_csv(eth_csv, btc_csv, sol_csv):
    def load(p):
        raw, _ = load_ohlcv_csv(p)
        return bt.add_indicators(bt.drop_incomplete_candles(resample_ohlcv(raw, "6h"), bt.TIMEFRAME_SECONDS))
    return {"BTC-USD": load(btc_csv), "ETH-USD": load(eth_csv), "SOL-USD": load(sol_csv)}


def _build_data_builtin(days):
    out = {}
    for sym in bt.ASSETS:
        df = bt.load_candles(sym, bt.TIMEFRAME_SECONDS, days)
        out[sym] = bt.add_indicators(bt.drop_incomplete_candles(df, bt.TIMEFRAME_SECONDS))
    return out


def _trade_to_events(trade, run_id, mode, label, config_hash, risk_state):
    entry_iso = trade.entry_time.isoformat()
    exit_iso = trade.exit_time.isoformat() if trade.exit_time is not None else None
    position_id = _hash(f"position|{trade.symbol}|{entry_iso}")
    base = {
        "run_id": run_id, "mode": mode,
        "strategy_name": STRATEGY_NAME, "strategy_version": STRATEGY_VERSION,
        "asset": trade.symbol, "timeframe": TIMEFRAME,
        "side": trade.side, "regime_state": trade.regime_state,
        "risk_state": risk_state, "source_data_label": label,
        "position_id": position_id, "config_hash": config_hash,
    }
    entry = dict(base,
        event_id=_hash(f"{run_id}|entry|{entry_iso}|{trade.symbol}"),
        timestamp_utc=entry_iso, signal_type="entry",
        bar_timestamp=trade.signal_candle_time.isoformat(),
        entry_price=float(trade.entry_price), stop_price=float(trade.stop_price),
        target_1_price=float(trade.target_1_price),
        initial_stop_distance=float(trade.initial_stop_distance),
        notional_usd=float(trade.notional_usd), atr_at_signal=float(trade.atr_at_signal),
        r_multiple_expected=float(bt.PARTIAL_R),    # +1.5R partial as planned first target
        reason="breakout_entry",
    )
    exit_ev = dict(base,
        event_id=_hash(f"{run_id}|exit|{exit_iso or ''}|{trade.symbol}"),
        timestamp_utc=exit_iso, signal_type="exit",
        bar_timestamp=exit_iso,
        entry_price=float(trade.entry_price),
        exit_price=float(trade.exit_price_final) if trade.exit_price_final is not None else None,
        stop_price=float(trade.stop_price),
        reason=trade.exit_reason,
        r_multiple=float(trade.r_multiple),
        net_pnl_usd=float(trade.net_pnl_usd), gross_pnl_usd=float(trade.gross_pnl_usd),
        fees_usd=float(trade.fees_usd), slippage_usd=float(trade.slippage_usd),
        funding_usd=float(trade.funding_usd), bars_held=int(trade.bars_held),
    )
    return entry, exit_ev


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--mode", required=True)
    p.add_argument("--asset", default=ASSET)
    p.add_argument("--days", type=int, default=bt.DAYS_BACK)
    p.add_argument("--eth-csv", default=None)
    p.add_argument("--btc-csv", default=None)
    p.add_argument("--sol-csv", default=None)
    p.add_argument("--label", default=None)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    print(SAFETY_BANNER)

    if args.mode not in SUPPORTED_MODES:
        print(f"ERROR: --mode {args.mode!r} not supported. Use one of: {SUPPORTED_MODES}")
        sys.exit(2)
    if args.asset != ASSET:
        print(f"ERROR: v1 supports only --asset {ASSET} (the current research candidate). "
              f"Got {args.asset!r}.")
        sys.exit(2)

    started_at = _now_iso()
    config_hash = _hash(json.dumps(_config_dict(), sort_keys=True))

    if args.mode == "csv-replay":
        if not (args.eth_csv and args.btc_csv and args.sol_csv):
            print("ERROR: --mode csv-replay requires --eth-csv, --btc-csv and --sol-csv "
                  "(strict regime needs full BTC/ETH/SOL context).")
            sys.exit(2)
        label = args.label or "csv_replay"
        data = _build_data_csv(args.eth_csv, args.btc_csv, args.sol_csv)
        data_source = {"mode": "csv-replay", "eth_csv": args.eth_csv,
                       "btc_csv": args.btc_csv, "sol_csv": args.sol_csv, "label": label}
    else:  # replay
        label = args.label or "coinbase_builtin"
        try:
            data = _build_data_builtin(args.days)
        except Exception as e:
            print(f"\nERROR: built-in data fetch failed ({e.__class__.__name__}: {e}). "
                  f"Use --mode csv-replay with local CSVs, or run on a machine with network.")
            sys.exit(2)
        data_source = {"mode": "replay", "symbols": list(bt.ASSETS), "days": args.days, "label": label}

    run_id = _hash(f"{STRATEGY_NAME}|{STRATEGY_VERSION}|{args.mode}|{label}|{config_hash}")
    eth_indexed = data["ETH-USD"].set_index("time")
    risk_state = {"account_size_usd": float(bt.ACCOUNT_SIZE_USD),
                  "risk_pct": float(bt.RISK_PCT),
                  "max_open_positions": int(bt.MAX_OPEN_POSITIONS)}

    trades, _equity = bt.run_backtest(data, trade_assets=["ETH-USD"], long_only=True,
                                       strict_regime=True, setup="breakout",
                                       early_exit_fn=make_fts_exit(eth_indexed))

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    n_entry = n_exit = 0
    warnings = []
    with open(args.output, "w") as f:
        for t in trades:
            entry, exit_ev = _trade_to_events(t, run_id, args.mode, label, config_hash, risk_state)
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            n_entry += 1
            f.write(json.dumps(exit_ev, sort_keys=True) + "\n")
            n_exit += 1

    if trades:
        wins = sum(1 for t in trades if t.net_pnl_usd > 0)
        net = float(sum(t.net_pnl_usd for t in trades))
        avg_r = float(sum(t.r_multiple for t in trades) / len(trades))
    else:
        wins, net, avg_r = 0, 0.0, 0.0
        warnings.append("no trades produced under current rule + data window")

    coverage = {}
    if "ETH-USD" in data and not data["ETH-USD"].empty:
        coverage = {"first_bar": data["ETH-USD"]["time"].min().isoformat(),
                    "last_bar": data["ETH-USD"]["time"].max().isoformat(),
                    "bar_count": int(len(data["ETH-USD"]))}

    summary = {
        "run_id": run_id, "started_at": started_at, "completed_at": _now_iso(),
        "mode": args.mode, "strategy_name": STRATEGY_NAME, "strategy_version": STRATEGY_VERSION,
        "data_source": data_source, "input_coverage": coverage,
        "n_entry_signals": n_entry, "n_exit_signals": n_exit,
        "n_completed_trades": len(trades),
        "net_pnl_usd": net, "avg_r_multiple": avg_r,
        "win_count": wins, "loss_count": len(trades) - wins,
        "warnings": warnings, "config_hash": config_hash,
        "output_file": os.path.abspath(args.output),
    }
    sum_path = args.output[:-len(".jsonl")] + ".summary.json" if args.output.endswith(".jsonl") \
        else args.output + ".summary.json"
    with open(sum_path, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)

    print(f"\nwrote {n_entry} entry + {n_exit} exit events to {args.output}")
    print(f"wrote summary to {sum_path}")
    print(f"trades={len(trades)}  wins={wins}  net=${net:+.2f}  avg_r={avg_r:+.3f}")
    print(f"run_id={run_id}  config_hash={config_hash}")
    print("\nINFRASTRUCTURE ONLY. No orders, no paper, no live, no tuning.")


if __name__ == "__main__":
    main()
