# Signal-Only Logger (Step 1 of the MVP)

**SIGNAL-ONLY MODE: no orders, no exchange connection, no paper trading, no
live trading. No API credentials. No keys required.**

This is the first piece of the "Minimum viable trading system" build order
(roadmap §8.5). It runs the existing ETH 6H strict-regime breakout + the frozen
K=2 / +0.5R failure-to-separate exit through the existing backtest engine and
emits one structured JSON event per entry and exit to a JSONL file. **Nothing
is traded; no money moves.**

## What this is

- A deterministic, reproducible **logger** for the candidate strategy's
  entry and exit signals.
- A piece of plumbing that forces research code into the shape needed for a
  later replay → paper → execution flow, without changing any strategy rule.
- A foundation for the future **backtest ↔ replay parity gate** (roadmap §8.5
  step 2).

## What this is NOT

- It does **not** place orders.
- It does **not** connect to any exchange.
- It does **not** know about API keys, balances, or accounts.
- It does **not** paper-trade.
- It does **not** change any strategy rule, parameter, or threshold.

If a code path could place an order, it is not in this script.

## How to run

```bash
# Replay against built-in Coinbase data (needs network at runtime; no API key).
python3 signal_logger.py --mode replay --asset ETH-USD --days 1460 \
    --output logs/signals_eth_replay.jsonl

# Replay against fully-independent CSVs (no network needed).
python3 signal_logger.py --mode csv-replay \
    --eth-csv ~/eth.csv --btc-csv ~/btc.csv --sol-csv ~/sol.csv \
    --label binance_fully_indep \
    --output logs/signals_eth_binance_replay.jsonl
```

Only `--asset ETH-USD` is wired in v1 (the current research candidate). Any
other asset is rejected with a clear error.

## JSONL event schema (one event per line)

Common fields on every event:

| field | meaning |
|---|---|
| `event_id` | sha256 hex prefix; deterministic per (run_id, signal_type, bar, asset) |
| `timestamp_utc` | ISO-8601 UTC of when the event would fire (entry_time / exit_time) |
| `run_id` | sha256 prefix of strategy + version + mode + dataset label + config_hash |
| `mode` | `replay` or `csv-replay` |
| `strategy_name` | `eth_strict_breakout_fts_exit` |
| `strategy_version` | `1.0.0` |
| `asset` | `ETH-USD` |
| `timeframe` | `6H` |
| `signal_type` | `entry` or `exit` |
| `side` | `long` |
| `regime_state` | `risk_on` (the strict-regime gate the entry passed) |
| `risk_state` | `{account_size_usd, risk_pct, max_open_positions}` (config snapshot) |
| `source_data_label` | e.g. `coinbase_builtin`, `binance_fully_indep` |
| `position_id` | sha256 prefix of (asset, entry_iso) |
| `config_hash` | sha256 prefix of the frozen strategy config (`_config_dict`) |

Entry-event extras: `bar_timestamp` (signal bar), `entry_price`, `stop_price`,
`target_1_price`, `initial_stop_distance`, `notional_usd`, `atr_at_signal`,
`r_multiple_expected` (= PARTIAL_R, the +1.5R first target), `reason="breakout_entry"`.

Exit-event extras: `bar_timestamp` (exit bar), `entry_price`, `exit_price`,
`stop_price`, `reason` (`stop_hit` / `breakeven_stop` / `trail_stop` /
`time_stop` / `failure_to_separate` / `backtest_end`), `r_multiple`,
`gross_pnl_usd`, `net_pnl_usd`, `fees_usd`, `slippage_usd`, `funding_usd`,
`bars_held`.

## Summary JSON (written next to the JSONL)

`<output>.summary.json` (e.g. `logs/signals_eth_replay.summary.json`):

```
{
  "run_id", "started_at", "completed_at", "mode",
  "strategy_name", "strategy_version",
  "data_source": { ... mode-specific provenance ... },
  "input_coverage": { "first_bar", "last_bar", "bar_count" },
  "n_entry_signals", "n_exit_signals", "n_completed_trades",
  "net_pnl_usd", "avg_r_multiple", "win_count", "loss_count",
  "warnings": [ ... ],
  "config_hash", "output_file"
}
```

## Real Binance replay command (fully independent)

```bash
python3 signal_logger.py --mode csv-replay \
    --eth-csv ~/eth.csv --btc-csv ~/btc.csv --sol-csv ~/sol.csv \
    --label binance_fully_indep \
    --output logs/signals_eth_binance_replay.jsonl
```

This produces 24 entry + 24 exit events under the current research-frozen rule.
No network, no API, no orders — just `bt.run_backtest` against the CSV-loaded
data and an event written per signal.

## Parity check — report-only and strict modes

`signal_replay_parity_check.py` reconstructs trades from the JSONL (matching
entry/exit by `position_id`) and compares them to a backtest `trades.csv`
joined on `entry_time`. Without `--strict` it is a report-only tool (always
exit 0 unless inputs are invalid). With `--strict` it **enforces** parity:
every required check must pass or the script exits non-zero.

### Strict parity command (real Binance run)

```bash
python3 signal_replay_parity_check.py \
    --signals logs/signals_eth_binance_replay.jsonl \
    --trades ./backtest_output_eth_fts_exit/binance_fully_indep/trades_exit.csv \
    --strict
```

Flags and defaults:

| flag | default | meaning |
|---|---|---|
| `--strict` | off | enforce required checks; exit non-zero on any failure |
| `--price-tol` | `1e-8` | max abs Δ for entry_price / exit_price |
| `--r-tol` | `1e-8` | max abs Δ for r_multiple |
| `--time-tol-seconds` | `0` | max abs Δ for exit_time (in seconds) |
| `--max-mismatches` | `20` | first N mismatches to print on FAIL |

### Required checks (must all pass under `--strict`)

- trade count matches
- no signals-only entries; no csv-only entries (after entry_time join)
- `entry_price` within `--price-tol`
- `exit_price` within `--price-tol` (logger `exit_price` vs CSV `exit_price_final`)
- `exit_time` within `--time-tol-seconds`
- `exit_reason` exact match (when both sides have it)
- `r_multiple` within `--r-tol` (when both sides have it)

### Output

The human-readable summary always prints. On `--strict`, it ends with either:

```
STRICT PARITY: PASS
```

or

```
STRICT PARITY: FAIL (<N> mismatch(es); showing up to <M>)
  <field> @ <row>  sig=<value>  csv=<value>  Δ=<delta>
```

### Why the gate exists

A strict parity pass **must** hold before any dry-run / paper / live work
proceeds. It guarantees that the live-shaped signal path produces the same
events as the offline backtest — so any later divergence is caused by data
feed / execution / time-handling, not by the strategy code shifting.

**Strict parity is infrastructure validation only.** Passing it does NOT mean
the strategy is deployable. The research gates (§5 of the roadmap), the paper
gates, the operational gates, the risk gates, and the execution gates all
still apply. No live trading. No paper trading. No orders.

### Offline self-test of the gate

```bash
python3 verify_signal_parity_offline.py
```

Builds a tiny synthetic fixture, runs strict parity (expects PASS), mutates
one `exit_price`, re-runs (expects FAIL), and reports `[PASS]` only if both
verdicts behaved as expected. The same self-test is also called from
`verify_offline.py`.

## Limitations / known-not-yet

- `--asset` is fixed to `ETH-USD` in v1 (the current research candidate).
- `replay` mode needs network at runtime (it uses the existing Coinbase fetch
  with retry/cache); no API key is involved.
- `csv-replay` mode requires **all three** of `--eth-csv`, `--btc-csv`,
  `--sol-csv` because the strict regime gates on BTC and the RS rank uses the
  full BTC/ETH/SOL universe. An ETH-only CSV cannot replicate the rule.
- The logger emits events for **completed** entries/exits (the same trades the
  backtest produced). A live-feed version would also emit "scan" / "skip" events
  per bar — that is a Step 5 concern.
- No orders. No exchange. No account.

## Safety guardrails

- Unknown `--mode` values are rejected with exit code 2.
- Unsupported `--asset` is rejected with exit code 2.
- No code path in this script reads API keys, connects to an exchange, or
  places orders. There is no execution adapter here.

## Roadmap context

This is Step 1 of §8.5 in `docs/STRATEGY_RESEARCH_ROADMAP.md`. Next steps,
strictly in order: backtest ↔ replay parity gate (step 2); paper / dry-run
(step 3); ops scaffolding (step 4); read-only exchange adapter (step 5).
Nothing in this folder is deployable.
