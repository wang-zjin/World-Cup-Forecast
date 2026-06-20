#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[1]


def load_compare_module():
    path = ROOT / "scripts" / "compare_sporttery_expected_returns.py"
    spec = importlib.util.spec_from_file_location("compare_sporttery_expected_returns", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["compare_sporttery_expected_returns"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def expected_return_rows() -> list[dict[str, object]]:
    compare = load_compare_module()
    estimator = compare.load_estimator()
    events_by_match = estimator.collect_events()
    matrices = {
        matchup: estimator.solve_match(events_by_match[matchup])["matrix"]
        for matchup in estimator.TARGET_MATCHES
    }

    rows = []
    for row in compare.load_sporttery_rows(estimator.TARGET_MATCHES):
        probability = compare.score_probability(
            matrices[row["matchup"]],
            row["market_type"],
            row["event_name"],
        )
        expected_return = probability * row["odds"] - 1
        rows.append({**row, "probability": probability, "expected_return": expected_return})
    rows.sort(key=lambda item: item["expected_return"], reverse=True)
    return rows


def matrix_rows() -> tuple[object, dict[str, np.ndarray], list[dict[str, object]]]:
    compare = load_compare_module()
    estimator = compare.load_estimator()
    events_by_match = estimator.collect_events()
    matrices = {
        matchup: estimator.solve_match(events_by_match[matchup])["matrix"]
        for matchup in estimator.TARGET_MATCHES
    }

    rows = []
    for row in compare.load_sporttery_rows(estimator.TARGET_MATCHES):
        matrix = matrices[row["matchup"]]
        probability = compare.score_probability(matrix, row["market_type"], row["event_name"])
        expected_return = probability * row["odds"] - 1
        rows.append({**row, "probability": probability, "expected_return": expected_return})
    rows.sort(key=lambda item: item["expected_return"], reverse=True)
    return compare, matrices, rows


def percent(value: float, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.2f}%"


def covariance_matrix(compare: object, matrices: dict[str, np.ndarray], rows: list[dict[str, object]]) -> np.ndarray:
    n = len(rows)
    sigma = np.zeros((n, n))
    masks = [
        compare.score_mask(matrices[row["matchup"]], row["market_type"], row["event_name"])
        for row in rows
    ]

    for i, row_i in enumerate(rows):
        for j, row_j in enumerate(rows):
            if row_i["matchup"] != row_j["matchup"]:
                sigma[i, j] = 0.0
                continue
            matrix = matrices[row_i["matchup"]]
            joint_probability = float(matrix[masks[i] & masks[j]].sum())
            sigma[i, j] = (
                row_i["odds"]
                * row_j["odds"]
                * (joint_probability - row_i["probability"] * row_j["probability"])
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
    for i in range(n):
        point = np.zeros(n)
        point[i] = 1.0
        initial_points.append(point)
    rng = np.random.default_rng(42)
    for _ in range(100):
        initial_points.append(rng.dirichlet(np.ones(n)))

    best = None
    for x0 in initial_points:
        result = minimize(
            objective,
            x0=x0,
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
    compare, matrices, rows = matrix_rows()
    positive = [row for row in rows if row["expected_return"] > 0]
    best = rows[0]

    print(f"候选体彩数字盘口数：{len(rows)}")
    print(f"正期望盘口数：{len(positive)}")
    print()

    if not positive:
        print("最优投注方案：不下注，现金仓位 100%。")
        print()
        print("| 仓位 | 权重 | 说明 |")
        print("|---|---:|---|")
        print("| 现金 / 不下注 | 100.0% | 所有可投体彩盘口的估计期望收益均小于 0 |")
        print("| 体彩事件组合 | 0.0% | 无正期望事件，不构建风险仓位 |")
        print()
        print("如果必须进行最小金额试投，只能选择期望亏损最小的事件：")
        print()
        print("| 比赛 | 玩法 | 事件 | 注数 | 投注金额 | 体彩赔率 | Polymarket估计概率 | 期望收益 | 期望盈亏 |")
        print("|---|---|---|---:|---:|---:|---:|---:|---:|")
        expected_pnl = 2 * best["expected_return"]
        print(
            f"| {best['matchup']} | {best['market_type']} | {best['event_name']} | "
            f"1注 | 2元 | {best['odds']:.2f} | {percent(best['probability'])} | "
            f"{percent(best['expected_return'], signed=True)} | {expected_pnl:.2f}元 |"
        )
        print()
        print("如果被要求把 30 元全部下注，单纯按期望收益最大化会全部押到同一事件，但该方案仍为负期望，不能视为推荐：")
        print()
        print("| 比赛 | 玩法 | 事件 | 注数 | 投注金额 | 体彩赔率 | Polymarket估计概率 | 期望收益 | 期望盈亏 |")
        print("|---|---|---|---:|---:|---:|---:|---:|---:|")
        expected_pnl_30 = 30 * best["expected_return"]
        print(
            f"| {best['matchup']} | {best['market_type']} | {best['event_name']} | "
            f"15注 | 30元 | {best['odds']:.2f} | {percent(best['probability'])} | "
            f"{percent(best['expected_return'], signed=True)} | {expected_pnl_30:.2f}元 |"
        )
        return

    sigma = covariance_matrix(compare, matrices, positive)
    weights = optimize_weights(positive, sigma)
    units = discrete_units(weights)
    total_stake = int(units.sum() * 2)
    expected_pnl = float(sum(2 * unit * row["expected_return"] for unit, row in zip(units, positive)))
    mu = np.array([row["expected_return"] for row in positive], dtype=float)
    portfolio_expected_return = float(weights @ mu)
    portfolio_sd = float(np.sqrt(weights @ sigma @ weights))
    reward_risk = portfolio_expected_return / portfolio_sd if portfolio_sd > 0 else 0.0

    print("最优投注方案：存在正期望盘口，按收益风险比构建组合。")
    print(f"连续组合期望收益率：{percent(portfolio_expected_return, signed=True)}")
    print(f"连续组合收益标准差：{portfolio_sd:.4f}")
    print(f"连续组合收益风险比：{reward_risk:.4f}")
    print()
    print("| 比赛 | 玩法 | 事件 | 连续权重 | 注数 | 投注金额 | 体彩赔率 | Polymarket估计概率 | 期望收益 | 期望盈亏 |")
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
    print("说明：注数按最小成本法从连续权重离散化，实际权重会偏离连续最优权重。")


if __name__ == "__main__":
    main()
