#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[1]
SPORTTERY_ODDS = ROOT / "2026世界杯体彩赔率.md"


def load_estimator():
    path = ROOT / "scripts" / "estimate_score_distribution_limit_prob_space.py"
    spec = importlib.util.spec_from_file_location("estimate_score_distribution_limit_prob_space", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["estimate_score_distribution_limit_prob_space"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


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


def percent(value: float, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.2f}%"


def solve_rows() -> tuple[object, dict[str, dict[str, object]], list[dict[str, object]]]:
    estimator = load_estimator()
    events_by_match = estimator.collect_events()
    results = {
        matchup: estimator.solve_match(matchup, events_by_match[matchup])
        for matchup in estimator.TARGET_MATCHES
    }

    rows = []
    for row in load_sporttery_rows(estimator.TARGET_MATCHES):
        result = results[row["matchup"]]
        probability = estimator.score_probability(
            result,
            row["market_type"],
            row["event_name"],
        )
        expected_return = probability * row["odds"] - 1
        rows.append({**row, "probability": probability, "expected_return": expected_return})
    rows.sort(key=lambda item: item["expected_return"], reverse=True)
    return estimator, results, rows


def covariance_matrix(
    estimator: object,
    results: dict[str, dict[str, object]],
    rows: list[dict[str, object]],
) -> np.ndarray:
    n = len(rows)
    sigma = np.zeros((n, n))
    for i, left in enumerate(rows):
        for j, right in enumerate(rows):
            if left["matchup"] != right["matchup"]:
                sigma[i, j] = 0.0
                continue
            result = results[left["matchup"]]
            joint = estimator.joint_probability(
                result,
                left["market_type"],
                left["event_name"],
                right["market_type"],
                right["event_name"],
            )
            sigma[i, j] = (
                left["odds"]
                * right["odds"]
                * (joint - left["probability"] * right["probability"])
            )
    return sigma


def optimize_weights(rows: list[dict[str, object]], sigma: np.ndarray) -> np.ndarray:
    mu = np.array([row["expected_return"] for row in rows], dtype=float)
    n = len(rows)

    def objective(weights: np.ndarray) -> float:
        expected_return = float(weights @ mu)
        variance = float(weights @ sigma @ weights)
        if variance <= 0:
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
            x0=initial,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
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


def main() -> None:
    estimator, results, rows = solve_rows()
    positive = [row for row in rows if row["expected_return"] > 0]

    print("## 策略三：有限比分概率空间")
    print()
    print(f"目标日期：{estimator.TARGET_DATE}")
    print(f"有限比分状态数：{len(estimator.STATE_LABELS)}")
    print(f"候选体彩数字盘口数：{len(rows)}")
    print(f"正期望盘口数：{len(positive)}")
    print()
    print("有限空间估计概率下的体彩期望收益（前 30）：")
    print()
    print("| 比赛 | 玩法 | 事件 | 体彩赔率 | 有限空间估计概率 | 期望收益 |")
    print("|---|---|---|---:|---:|---:|")
    for row in rows[:30]:
        print(
            f"| {row['matchup']} | {row['market_type']} | {row['event_name']} | "
            f"{row['odds']:.2f} | {percent(row['probability'])} | "
            f"{percent(row['expected_return'], signed=True)} |"
        )
    print()

    if not positive:
        print("策略三最优投注方案：不下注，现金仓位 100%。")
        return

    sigma = covariance_matrix(estimator, results, positive)
    weights = optimize_weights(positive, sigma)
    units = discrete_units(weights)
    total_stake = int(units.sum() * 2)
    expected_pnl = float(sum(2 * unit * row["expected_return"] for unit, row in zip(units, positive)))
    mu = np.array([row["expected_return"] for row in positive], dtype=float)
    portfolio_expected_return = float(weights @ mu)
    portfolio_sd = float(np.sqrt(weights @ sigma @ weights))
    reward_risk = portfolio_expected_return / portfolio_sd if portfolio_sd > 0 else 0.0

    print("策略三最优投注方案：存在正期望盘口，按收益风险比构建组合。")
    print()
    print(f"连续组合期望收益率：{percent(portfolio_expected_return, signed=True)}")
    print(f"连续组合收益标准差：{portfolio_sd:.4f}")
    print(f"连续组合收益风险比：{reward_risk:.4f}")
    print()
    print("| 比赛 | 玩法 | 事件 | 连续权重 | 注数 | 投注金额 | 体彩赔率 | 有限空间估计概率 | 期望收益 | 期望盈亏 |")
    print("|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for row, weight, unit in zip(positive, weights, units):
        if unit <= 0:
            continue
        stake = int(unit * 2)
        row_expected_pnl = stake * row["expected_return"]
        print(
            f"| {row['matchup']} | {row['market_type']} | {row['event_name']} | "
            f"{percent(float(weight))} | {unit}注 | {stake}元 | {row['odds']:.2f} | "
            f"{percent(row['probability'])} | {percent(row['expected_return'], signed=True)} | "
            f"{row_expected_pnl:.2f}元 |"
        )
    print()
    print(f"总投入：{total_stake}元")
    print(f"组合期望盈亏：{expected_pnl:.2f}元")


if __name__ == "__main__":
    main()
