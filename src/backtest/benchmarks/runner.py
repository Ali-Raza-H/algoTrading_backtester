from __future__ import annotations

import hashlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from typing import Any

from backtest.analytics import calculate_metrics
from backtest.benchmarks.statistics import empirical_comparison
from backtest.config import ExperimentConfig, StrategyConfig
from backtest.engine import RunResult, execute_run


def _seed(master: int, policy: str, trial: int) -> int:
    material = f"{master}|{policy}|{trial}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _random_trial(arguments: tuple[ExperimentConfig, int]) -> dict[str, Any]:
    config, trial = arguments
    benchmark = config.benchmark
    strategy = StrategyConfig(
        entrypoint="strategies.random_policy:build_strategy",
        strategy_id="random-policy",
        version="1.0.0",
        parameters={
            "entry_probability": benchmark.entry_probability,
            "holding_bars": benchmark.holding_bars,
            "quantity": str(benchmark.quantity),
            "timeframe": benchmark.timeframe,
        },
        timeframes=("1m", benchmark.timeframe),
        lookbacks={"1m": 1, benchmark.timeframe: 1},
    )
    trial_config = replace(
        config,
        strategy=strategy,
        seed=_seed(config.seed, "random_policy", trial),
        benchmark=replace(benchmark, enabled=False),
    )
    result = execute_run(trial_config)
    metrics = calculate_metrics(result)
    return {
        "policy": "random_policy",
        "trial": trial,
        "seed": trial_config.seed,
        "net_return_percent": metrics["net_return_percent"],
        "max_drawdown_percent": metrics["max_drawdown_percent"],
        "closed_trades": metrics["closed_trades"],
    }


def run_benchmarks(result: RunResult) -> None:
    config = result.config
    benchmark = config.benchmark
    if not benchmark.enabled:
        return
    rows: list[dict[str, Any]] = []
    if "cash" in benchmark.policies:
        rows.append(
            {
                "policy": "cash",
                "trial": 0,
                "seed": None,
                "net_return_percent": 0.0,
                "max_drawdown_percent": 0.0,
                "closed_trades": 0,
            }
        )
    if "random_policy" in benchmark.policies:
        arguments = [(config, trial) for trial in range(benchmark.trials)]
        if config.workers == 1:
            rows.extend(_random_trial(item) for item in arguments)
        else:
            with ProcessPoolExecutor(max_workers=config.workers) as executor:
                rows.extend(executor.map(_random_trial, arguments))
    result.benchmarks = rows
    random_returns = [
        float(row["net_return_percent"]) for row in rows if row["policy"] == "random_policy"
    ]
    if random_returns:
        observed = float(calculate_metrics(result)["net_return_percent"])
        result.benchmark_comparison = empirical_comparison(observed, random_returns)
