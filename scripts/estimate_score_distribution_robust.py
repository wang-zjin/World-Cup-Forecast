#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[1]
SPORTTERY_ODDS = ROOT / "2026世界杯体彩赔率.md"
DEFAULT_SAMPLES = 200
DEFAULT_HISTORY_LIMIT = 240
DEFAULT_QUANTILE = 0.05
DEFAULT_MIN_POSITIVE_PROBABILITY = 0.80


def load_limit_estimator():
    path = ROOT / "scripts" / "estimate_score_distribution_limit_prob_space.py"
    spec = importlib.util.spec_from_file_location("estimate_score_distribution_limit_prob_space", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["estimate_score_distribution_limit_prob_space"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)


def markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def percent(value: float, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.2f}%"


def load_sporttery_rows(target_matches: tuple[str, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in SPORTTERY_ODDS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| 时间 ") or line.startswith("|---"):
            continue
        cells = markdown_cells(line)
        if len(cells) != 5:
            continue
        _, matchup, market_type, event_name, odds_text = cells
        if matchup not in target_matches or odds_text == "-":
            continue
        rows.append(
            {
                "matchup": matchup,
                "market_type": market_type,
                "event_name": event_name,
                "odds": float(odds_text),
            }
        )
    return rows


def event_key(event: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(event["market_type"]),
        str(event["event_name"]),
        str(event.get("raw_market", "")),
    )


def all_history_files(estimator: object, history_limit: int) -> list[Path]:
    files = sorted((estimator.BOT_ROOT / "data" / "match-quotes").glob("*.csv"))
    source = estimator.SOURCE_CSV.resolve()
    files = [path for path in files if path.name <= source.name]
    if history_limit > 0:
        files = files[-history_limit:]
    return files


def snapshot_prices(
    estimator: object,
    path: Path,
    wanted: dict[str, set[tuple[str, str, str]]],
) -> dict[str, dict[tuple[str, str, str], float]]:
    prices_by_match = {matchup: {} for matchup in wanted}
    rows = estimator.gpo.read_csv(path)
    for row in rows:
        mapped = estimator.gpo.matchup_zh_for_event(row["event"])
        if not mapped:
            continue
        home_en, away_en, matchup = mapped
        if matchup not in wanted:
            continue

        outcomes = row["outcomes"].split("|") if row["outcomes"] else []
        prices = row["prices"].split("|") if row["prices"] else []
        variant = estimator.gpo.variant_of(row["event"])
        for outcome_index in estimator.gpo.selected_outcomes(variant, outcomes):
            outcome = outcomes[outcome_index] if outcome_index < len(outcomes) else ""
            raw_price = prices[outcome_index].strip() if outcome_index < len(prices) else ""
            if not raw_price:
                continue
            market_type, event_name = estimator.gpo.type_event_for(row, outcome, home_en, away_en)
            if not estimator.included_type(market_type):
                continue
            key = (market_type, event_name, row["market"].strip())
            if key in wanted[matchup]:
                prices_by_match[matchup][key] = float(raw_price)
    return prices_by_match


def collect_price_history(
    estimator: object,
    base_events: dict[str, list[dict[str, object]]],
    history_limit: int,
) -> tuple[list[Path], dict[str, dict[tuple[str, str, str], list[float]]], list[dict[str, dict[tuple[str, str, str], float]]]]:
    wanted = {
        matchup: {event_key(event) for event in events}
        for matchup, events in base_events.items()
    }
    history_files = all_history_files(estimator, history_limit)
    series = {
        matchup: {key: [] for key in keys}
        for matchup, keys in wanted.items()
    }
    snapshots: list[dict[str, dict[tuple[str, str, str], float]]] = []

    for path in history_files:
        prices_by_match = snapshot_prices(estimator, path, wanted)
        snapshots.append(prices_by_match)
        for matchup, prices in prices_by_match.items():
            for key, price in prices.items():
                series[matchup][key].append(price)

    return history_files, series, snapshots


def event_matrix_for_match(estimator: object, matchup: str, events: list[dict[str, object]], result: dict[str, object]) -> np.ndarray:
    specs = estimator.event_specs(events)
    return estimator.event_matrix(
        matchup,
        specs,
        float(result["home_lambda"]),
        float(result["away_lambda"]),
    )


def solve_fixed_prior(
    masks: np.ndarray,
    targets: np.ndarray,
    weights: np.ndarray,
    prior: np.ndarray,
    start: np.ndarray,
    ridge: float,
) -> tuple[np.ndarray, float]:
    def objective(x: np.ndarray) -> float:
        residual = masks @ x - targets
        regularizer = np.sum((x - prior) ** 2 / (prior + 1e-9))
        return float(np.sum(weights * residual * residual) + ridge * regularizer)

    result = minimize(
        objective,
        x0=start,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * len(prior),
        constraints={"type": "eq", "fun": lambda x: np.sum(x) - 1.0},
        options={"ftol": 1e-10, "maxiter": 500},
    )
    if result.success:
        return result.x, float(result.fun)
    return start, float("nan")


def sampled_targets(
    events: list[dict[str, object]],
    latest_targets: np.ndarray,
    snapshot: dict[tuple[str, str, str], float],
    history_series: dict[tuple[str, str, str], list[float]],
    rng: np.random.Generator,
) -> np.ndarray:
    values: list[float] = []
    for index, event in enumerate(events):
        key = event_key(event)
        if key in snapshot:
            values.append(snapshot[key])
            continue
        series = history_series.get(key) or []
        if series:
            values.append(float(rng.choice(series)))
        else:
            values.append(float(latest_targets[index]))
    return np.array(values, dtype=float)


def monte_carlo_match(
    estimator: object,
    matchup: str,
    events: list[dict[str, object]],
    base_result: dict[str, object],
    snapshots: list[dict[str, dict[tuple[str, str, str], float]]],
    history_series: dict[tuple[str, str, str], list[float]],
    sample_count: int,
    rng: np.random.Generator,
) -> dict[str, object]:
    latest_targets = np.array([event["q"] for event in events], dtype=float)
    weights = estimator.normalize_weights(np.array([event["weight"] for event in events], dtype=float))
    masks = event_matrix_for_match(estimator, matchup, events, base_result)
    prior = estimator.state_prior(float(base_result["home_lambda"]), float(base_result["away_lambda"]))
    latest = np.asarray(base_result["probabilities"], dtype=float)

    if not snapshots:
        snapshots = [{matchup: {}}]

    probabilities = []
    losses = []
    start = latest
    for _ in range(sample_count):
        snapshot = rng.choice(snapshots)
        targets = sampled_targets(
            events,
            latest_targets,
            snapshot.get(matchup, {}),
            history_series,
            rng,
        )
        solved, loss = solve_fixed_prior(masks, targets, weights, prior, start, estimator.RIDGE)
        probabilities.append(solved)
        losses.append(loss)
        start = solved

    return {
        "probability_samples": np.vstack(probabilities),
        "losses": np.array(losses, dtype=float),
        "base_result": base_result,
        "event_count": len(events),
        "history_points": {
            key: len(values)
            for key, values in history_series.items()
        },
    }


def probability_samples_for_row(estimator: object, robust_result: dict[str, object], row: dict[str, object]) -> np.ndarray:
    base = robust_result["base_result"]
    values = estimator.conditional_values(
        row["matchup"],
        estimator.EventSpec(row["market_type"], row["event_name"]),
        float(base["home_lambda"]),
        float(base["away_lambda"]),
    )
    return np.asarray(robust_result["probability_samples"]) @ values


def joint_samples_for_rows(
    estimator: object,
    robust_result: dict[str, object],
    left: dict[str, object],
    right: dict[str, object],
) -> np.ndarray:
    base = robust_result["base_result"]
    values = estimator.conditional_joint_values(
        left["matchup"],
        estimator.EventSpec(left["market_type"], left["event_name"]),
        estimator.EventSpec(right["market_type"], right["event_name"]),
        float(base["home_lambda"]),
        float(base["away_lambda"]),
    )
    return np.asarray(robust_result["probability_samples"]) @ values


def summarize_rows(
    estimator: object,
    robust_results: dict[str, dict[str, object]],
    rows: list[dict[str, object]],
    quantile: float,
) -> list[dict[str, object]]:
    summarized: list[dict[str, object]] = []
    for row in rows:
        probability_samples = probability_samples_for_row(estimator, robust_results[row["matchup"]], row)
        expected_return_samples = probability_samples * float(row["odds"]) - 1
        summarized.append(
            {
                **row,
                "probability_samples": probability_samples,
                "expected_return_samples": expected_return_samples,
                "probability_mean": float(np.mean(probability_samples)),
                "probability_p05": float(np.quantile(probability_samples, quantile)),
                "probability_p50": float(np.quantile(probability_samples, 0.50)),
                "probability_p95": float(np.quantile(probability_samples, 1.0 - quantile)),
                "expected_return_mean": float(np.mean(expected_return_samples)),
                "expected_return_p05": float(np.quantile(expected_return_samples, quantile)),
                "expected_return_p50": float(np.quantile(expected_return_samples, 0.50)),
                "expected_return_p95": float(np.quantile(expected_return_samples, 1.0 - quantile)),
                "positive_probability": float(np.mean(expected_return_samples > 0)),
            }
        )
    summarized.sort(key=lambda item: (item["expected_return_p05"], item["expected_return_mean"]), reverse=True)
    return summarized


def robust_candidates(
    rows: list[dict[str, object]],
    min_positive_probability: float,
) -> list[dict[str, object]]:
    return [
        row
        for row in rows
        if row["expected_return_p05"] > 0
        and row["positive_probability"] >= min_positive_probability
    ]


def covariance_matrix(
    estimator: object,
    robust_results: dict[str, dict[str, object]],
    rows: list[dict[str, object]],
) -> np.ndarray:
    n = len(rows)
    sigma = np.zeros((n, n), dtype=float)
    mu_samples = [
        np.asarray(row["expected_return_samples"], dtype=float)
        for row in rows
    ]
    for i, left in enumerate(rows):
        for j, right in enumerate(rows):
            parameter_cov = float(np.cov(mu_samples[i], mu_samples[j], ddof=0)[0, 1])
            outcome_cov = 0.0
            if left["matchup"] == right["matchup"]:
                result = robust_results[left["matchup"]]
                joint = joint_samples_for_rows(estimator, result, left, right)
                left_p = np.asarray(left["probability_samples"], dtype=float)
                right_p = np.asarray(right["probability_samples"], dtype=float)
                outcome_cov = float(
                    np.mean(
                        float(left["odds"])
                        * float(right["odds"])
                        * (joint - left_p * right_p)
                    )
                )
            sigma[i, j] = outcome_cov + parameter_cov
    return sigma


def optimize_weights(rows: list[dict[str, object]], sigma: np.ndarray) -> np.ndarray:
    mu = np.array([row["expected_return_mean"] for row in rows], dtype=float)
    n = len(rows)

    def objective(weights: np.ndarray) -> float:
        expected_return = float(weights @ mu)
        variance = float(weights @ sigma @ weights)
        if variance <= 1e-14:
            return 1e9
        return -expected_return / np.sqrt(variance)

    constraints = [{"type": "eq", "fun": lambda weights: np.sum(weights) - 1.0}]
    bounds = [(0.0, 1.0)] * n
    initial_points = [np.ones(n) / n]
    for index in range(n):
        point = np.zeros(n)
        point[index] = 1.0
        initial_points.append(point)
    rng = np.random.default_rng(42)
    for _ in range(100):
        initial_points.append(rng.dirichlet(np.ones(n)))

    best = None
    for initial in initial_points:
        result = minimize(
            objective,
            initial,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-12, "maxiter": 1000},
        )
        if result.success and (best is None or result.fun < best.fun):
            best = result
    if best is None:
        raise RuntimeError("组合优化失败")
    weights = best.x
    weights[weights < 1e-8] = 0.0
    return weights / weights.sum()


def discrete_units(weights: np.ndarray) -> np.ndarray:
    positive = weights[weights > 0]
    min_weight = float(positive.min())
    units = np.rint(weights / min_weight).astype(int)
    units[weights > 0] = np.maximum(units[weights > 0], 1)
    return units


def print_history_summary(
    robust_results: dict[str, dict[str, object]],
    history_files: list[Path],
    sample_count: int,
    quantile: float,
) -> None:
    print("### 鲁棒估计输入")
    print()
    print(f"历史快照数：{len(history_files)}")
    if history_files:
        print(f"历史窗口：`{history_files[0].name}` 至 `{history_files[-1].name}`")
    print(f"蒙特卡洛样本数：{sample_count}")
    print(f"稳健分位数：{quantile:.0%}")
    print()
    print("| 比赛 | 纳入事件数 | 状态数 | Poisson 先验 lambda | MC目标函数均值 | MC目标函数95分位 |")
    print("|---|---:|---:|---|---:|---:|")
    for matchup, result in robust_results.items():
        base = result["base_result"]
        losses = result["losses"]
        print(
            f"| {matchup} | {result['event_count']} | {len(base['state_labels'])} | "
            f"主队 {float(base['home_lambda']):.3f}，客队 {float(base['away_lambda']):.3f} | "
            f"{float(np.nanmean(losses)):.6f} | {float(np.nanquantile(losses, 0.95)):.6f} |"
        )
    print()


def quantile_label(quantile: float) -> tuple[str, str]:
    return f"{quantile:.0%}", f"{1.0 - quantile:.0%}"


def print_expectation_table(rows: list[dict[str, object]], quantile: float, limit: int = 30) -> None:
    low_label, high_label = quantile_label(quantile)
    print("### 策略四鲁棒期望收益对比")
    print()
    print(f"体彩数字盘口共 `{len(rows)}` 个。下表按期望收益 {low_label} 分位排序，展示前 {limit} 个盘口：")
    print()
    print(f"| 比赛 | 玩法 | 事件 | 体彩赔率 | 概率均值 | 概率{low_label}-{high_label}区间 | 期望收益均值 | 期望收益{low_label}分位 | 正期望概率 |")
    print("|---|---|---|---:|---:|---:|---:|---:|---:|")
    for row in rows[:limit]:
        print(
            f"| {row['matchup']} | {row['market_type']} | {row['event_name']} | "
            f"{float(row['odds']):.2f} | {percent(row['probability_mean'])} | "
            f"{percent(row['probability_p05'])} - {percent(row['probability_p95'])} | "
            f"{percent(row['expected_return_mean'], signed=True)} | "
            f"{percent(row['expected_return_p05'], signed=True)} | "
            f"{percent(row['positive_probability'])} |"
        )
    print()


def print_plan(
    estimator: object,
    robust_results: dict[str, dict[str, object]],
    candidates: list[dict[str, object]],
    min_positive_probability: float,
    quantile: float,
) -> None:
    low_label, _ = quantile_label(quantile)
    print("### 策略四投注方案")
    print()
    print(
        f"稳健正期望门槛：期望收益 {low_label} 分位大于 0，且正期望概率不低于 "
        f"`{min_positive_probability:.0%}`。"
    )
    print()
    print(f"稳健正期望盘口数：`{len(candidates)}`。")
    print()

    if not candidates:
        print("策略四正式结论：不下注，现金仓位 100%。")
        print()
        print("完整投注方案如下：")
        print()
        print("不下注，现金仓位 100%。")
        print()
        print("每注 2 元，合计 0 注，总投入 0 元。")
        return

    sigma = covariance_matrix(estimator, robust_results, candidates)
    weights = optimize_weights(candidates, sigma)
    units = discrete_units(weights)
    total_stake = int(units.sum() * 2)
    expected_pnl = float(sum(2 * unit * row["expected_return_mean"] for unit, row in zip(units, candidates)))
    mu = np.array([row["expected_return_mean"] for row in candidates], dtype=float)
    portfolio_expected_return = float(weights @ mu)
    portfolio_sd = float(np.sqrt(max(0.0, weights @ sigma @ weights)))
    reward_risk = portfolio_expected_return / portfolio_sd if portfolio_sd > 0 else 0.0

    print("策略四最优投注方案：存在稳健正期望盘口，按收益风险比构建组合。")
    print()
    print(f"连续组合期望收益率：{percent(portfolio_expected_return, signed=True)}")
    print(f"连续组合收益标准差：{portfolio_sd:.4f}")
    print(f"连续组合收益风险比：{reward_risk:.4f}")
    print()
    print(f"| 比赛 | 玩法 | 事件 | 连续权重 | 注数 | 投注金额 | 体彩赔率 | 概率均值 | 期望收益均值 | 期望收益{low_label}分位 | 正期望概率 | 期望盈亏 |")
    print("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row, weight, unit in zip(candidates, weights, units):
        if unit <= 0:
            continue
        stake = int(unit * 2)
        print(
            f"| {row['matchup']} | {row['market_type']} | {row['event_name']} | "
            f"{percent(float(weight))} | {int(unit)}注 | {stake}元 | {float(row['odds']):.2f} | "
            f"{percent(row['probability_mean'])} | {percent(row['expected_return_mean'], signed=True)} | "
            f"{percent(row['expected_return_p05'], signed=True)} | "
            f"{percent(row['positive_probability'])} | "
            f"{stake * float(row['expected_return_mean']):.2f}元 |"
        )
    print()
    print(f"总投入：{total_stake}元")
    print(f"组合期望盈亏：{expected_pnl:.2f}元")
    print()
    print("完整投注方案如下：")
    print()
    for row, unit in zip(candidates, units):
        if unit <= 0:
            continue
        print(f"{row['matchup']}，{row['market_type']} {row['event_name']}，买 {int(unit)} 注。")
        print()
    print(f"每注 2 元，合计 {int(units.sum())} 注，总投入 {total_stake} 元。")


def main() -> None:
    estimator = load_limit_estimator()
    sample_count = env_int("WORLDCUP_ROBUST_SAMPLES", DEFAULT_SAMPLES)
    history_limit = env_int("WORLDCUP_ROBUST_HISTORY_LIMIT", DEFAULT_HISTORY_LIMIT)
    quantile = env_float("WORLDCUP_ROBUST_QUANTILE", DEFAULT_QUANTILE)
    min_positive_probability = env_float(
        "WORLDCUP_ROBUST_MIN_POSITIVE_PROBABILITY",
        DEFAULT_MIN_POSITIVE_PROBABILITY,
    )
    seed = env_int("WORLDCUP_ROBUST_SEED", 42)
    rng = np.random.default_rng(seed)

    if not estimator.TARGET_MATCHES:
        raise RuntimeError(f"未找到目标日期 {estimator.TARGET_DATE} 的体彩比赛")

    base_events = estimator.collect_events()
    base_results = {
        matchup: estimator.solve_match(matchup, base_events[matchup])
        for matchup in estimator.TARGET_MATCHES
    }
    history_files, series, snapshots = collect_price_history(estimator, base_events, history_limit)
    robust_results = {
        matchup: monte_carlo_match(
            estimator,
            matchup,
            base_events[matchup],
            base_results[matchup],
            snapshots,
            series[matchup],
            sample_count,
            rng,
        )
        for matchup in estimator.TARGET_MATCHES
    }

    rows = summarize_rows(
        estimator,
        robust_results,
        load_sporttery_rows(estimator.TARGET_MATCHES),
        quantile,
    )
    candidates = robust_candidates(rows, min_positive_probability)

    print("## 策略四：历史赔率随机变量鲁棒分布")
    print()
    print(f"目标日期：{estimator.TARGET_DATE}")
    print(f"Polymarket 源文件：`{estimator.SOURCE_CSV}`")
    print()
    print_history_summary(robust_results, history_files, sample_count, quantile)
    print_expectation_table(rows, quantile)
    print_plan(estimator, robust_results, candidates, min_positive_probability, quantile)


if __name__ == "__main__":
    main()
