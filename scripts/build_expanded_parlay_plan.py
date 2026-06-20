#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[1]
IRAN_MATCH = "伊朗 vs 新西兰"
IRAN_LEGS = ("胜", "平", "负")
SPORTTERY_ODDS = ROOT / "2026世界杯体彩赔率.md"
POLYMARKET_ODDS = ROOT / "2026世界杯polymarket赔率.md"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def percent(value: float, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.2f}%"


def line_date(time_text: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", time_text)
    return match.group(1) if match else None


def score_side(score: str) -> str:
    home_goals, away_goals = map(int, score.split(":"))
    if home_goals > away_goals:
        return "胜"
    if home_goals == away_goals:
        return "平"
    return "负"


def load_direct_rows() -> list[dict[str, object]]:
    estimator = load_module("estimate_score_distribution", ROOT / "scripts" / "estimate_score_distribution.py")
    target_matches = set(estimator.TARGET_MATCHES)
    target_date = os.environ.get("WORLDCUP_TARGET_DATE") or estimator.TARGET_DATE

    sporttery: dict[tuple[str, str, str], float] = {}
    for line in SPORTTERY_ODDS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| 时间 ") or line.startswith("|---"):
            continue
        cells = markdown_cells(line)
        if len(cells) != 5:
            continue
        time_text, matchup, market_type, event_name, odds_text = cells
        if matchup not in target_matches or line_date(time_text) != target_date or odds_text == "-":
            continue
        sporttery[(matchup, market_type, event_name)] = float(odds_text)

    polymarket: dict[tuple[str, str, str], float] = {}
    for line in POLYMARKET_ODDS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| 时间 ") or line.startswith("|---"):
            continue
        cells = markdown_cells(line)
        if len(cells) != 7:
            continue
        time_text, matchup, market_type, event_name, probability_text, _, _ = cells
        if matchup not in target_matches or line_date(time_text) != target_date:
            continue
        polymarket[(matchup, market_type, event_name)] = float(probability_text)

    rows = []
    for key, odds in sporttery.items():
        if key not in polymarket:
            continue
        matchup, market_type, event_name = key
        if market_type not in {"胜负", "比分"}:
            continue
        probability = polymarket[key]
        rows.append(
            {
                "match": matchup,
                "market": market_type,
                "event": event_name,
                "odds": odds,
                "p": probability,
                "er": probability * odds - 1,
            }
        )
    rows.sort(key=lambda row: row["er"], reverse=True)
    return rows


def direct_joint_probability(a: dict[str, object], b: dict[str, object]) -> float:
    if a["match"] != b["match"]:
        return float(a["p"]) * float(b["p"])
    if a["market"] == "比分" and b["market"] == "比分":
        return float(a["p"]) if a["event"] == b["event"] else 0.0
    if a["market"] == "胜负" and b["market"] == "胜负":
        return float(a["p"]) if a["event"] == b["event"] else 0.0
    if a["market"] == "胜负" and b["market"] == "比分":
        return float(b["p"]) if score_side(str(b["event"])) == a["event"] else 0.0
    if a["market"] == "比分" and b["market"] == "胜负":
        return float(a["p"]) if score_side(str(a["event"])) == b["event"] else 0.0
    return float(a["p"]) * float(b["p"])


def load_score_rows() -> tuple[object, dict[str, np.ndarray], list[dict[str, object]]]:
    plan = load_module("build_optimal_betting_plan", ROOT / "scripts" / "build_optimal_betting_plan.py")
    compare, matrices, raw_rows = plan.matrix_rows()
    rows = []
    for row in raw_rows:
        adapted = {
            "match": row["matchup"],
            "market": row["market_type"],
            "event": row["event_name"],
            "odds": float(row["odds"]),
            "p": float(row["probability"]),
            "er": float(row["expected_return"]),
        }
        adapted["mask"] = compare.score_mask(
            matrices[adapted["match"]],
            adapted["market"],
            adapted["event"],
        )
        rows.append(adapted)
    return compare, matrices, rows


def score_joint_probability(matrices: dict[str, np.ndarray], a: dict[str, object], b: dict[str, object]) -> float:
    if a["match"] != b["match"]:
        return float(a["p"]) * float(b["p"])
    matrix = matrices[a["match"]]
    return float(matrix[a["mask"] & b["mask"]].sum())


def build_variants(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    legs = {
        row["event"]: row
        for row in rows
        if row["match"] == IRAN_MATCH and row["market"] == "胜负" and row["event"] in IRAN_LEGS
    }
    missing = [leg for leg in IRAN_LEGS if leg not in legs]
    if missing:
        raise RuntimeError(f"缺少伊朗胜平负串关腿：{missing}")

    variants = []
    for row in rows:
        if row["match"] == IRAN_MATCH and row["market"] == "胜负":
            continue
        variants.append(
            {
                "label": f"{row['match']} {row['market']} {row['event']}",
                "kind": "单关",
                "base": row,
                "leg": None,
                "odds": row["odds"],
                "p": row["p"],
                "er": row["er"],
            }
        )

    for row in rows:
        if row["match"] == IRAN_MATCH:
            continue
        for leg_name in IRAN_LEGS:
            leg = legs[leg_name]
            probability = float(row["p"]) * float(leg["p"])
            odds = float(row["odds"]) * float(leg["odds"])
            variants.append(
                {
                    "label": f"{row['match']} {row['market']} {row['event']} × 伊朗 vs 新西兰 胜负 {leg_name}",
                    "kind": f"2串1-伊朗{leg_name}",
                    "base": row,
                    "leg": leg,
                    "odds": odds,
                    "p": probability,
                    "er": probability * odds - 1,
                }
            )
    variants.sort(key=lambda item: item["er"], reverse=True)
    return variants


def variant_conditions(variant: dict[str, object]) -> dict[str, dict[str, object]]:
    base = variant["base"]
    conditions = {base["match"]: base}
    if variant["leg"] is not None:
        conditions[IRAN_MATCH] = variant["leg"]
    return conditions


def variant_joint_probability(
    a: dict[str, object],
    b: dict[str, object],
    base_joint_probability,
) -> float:
    a_conditions = variant_conditions(a)
    b_conditions = variant_conditions(b)
    probability = 1.0
    for matchup in set(a_conditions) | set(b_conditions):
        left = a_conditions.get(matchup)
        right = b_conditions.get(matchup)
        if left is not None and right is not None:
            probability *= base_joint_probability(left, right)
        elif left is not None:
            probability *= float(left["p"])
        elif right is not None:
            probability *= float(right["p"])
    return probability


def covariance_matrix(variants: list[dict[str, object]], base_joint_probability) -> np.ndarray:
    n = len(variants)
    sigma = np.zeros((n, n))
    for i, left in enumerate(variants):
        for j, right in enumerate(variants):
            joint_probability = variant_joint_probability(left, right, base_joint_probability)
            sigma[i, j] = (
                float(left["odds"])
                * float(right["odds"])
                * (joint_probability - float(left["p"]) * float(right["p"]))
            )
    return sigma


def optimize_weights(variants: list[dict[str, object]], base_joint_probability) -> tuple[list[dict[str, object]], np.ndarray, np.ndarray, float, float, float, int, float]:
    positive = [variant for variant in variants if float(variant["er"]) > 1e-12]
    sigma = covariance_matrix(positive, base_joint_probability)
    mu = np.array([variant["er"] for variant in positive], dtype=float)
    n = len(positive)

    def objective(weights: np.ndarray) -> float:
        expected_return = float(weights @ mu)
        variance = float(weights @ sigma @ weights)
        if variance <= 1e-14:
            return 1e9
        return -expected_return / math.sqrt(variance)

    constraints = [{"type": "eq", "fun": lambda weights: np.sum(weights) - 1.0}]
    bounds = [(0.0, 1.0)] * n
    initial_points = [np.ones(n) / n]
    for i in range(n):
        point = np.zeros(n)
        point[i] = 1.0
        initial_points.append(point)
    rng = np.random.default_rng(42)
    for _ in range(min(200, max(100, n))):
        initial_points.append(rng.dirichlet(np.ones(n)))

    best = None
    for x0 in initial_points:
        result = minimize(
            objective,
            x0=x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 2000, "ftol": 1e-12},
        )
        if result.success and (best is None or result.fun < best.fun):
            best = result
    if best is None:
        raise RuntimeError("组合优化失败")

    weights = best.x
    weights[weights < 1e-8] = 0.0
    weights = weights / weights.sum()
    chosen_indexes = [index for index, weight in enumerate(weights) if weight > 0]
    chosen = [positive[index] for index in chosen_indexes]
    chosen_weights = weights[chosen_indexes]
    chosen_sigma = sigma[np.ix_(chosen_indexes, chosen_indexes)]
    chosen_mu = mu[chosen_indexes]

    portfolio_expected_return = float(chosen_weights @ chosen_mu)
    portfolio_sd = float(math.sqrt(max(0.0, chosen_weights @ chosen_sigma @ chosen_weights)))
    reward_risk = portfolio_expected_return / portfolio_sd if portfolio_sd > 0 else 0.0

    min_weight = float(chosen_weights[chosen_weights > 0].min())
    units = np.rint(chosen_weights / min_weight).astype(int)
    units[chosen_weights > 0] = np.maximum(units[chosen_weights > 0], 1)
    total_stake = int(units.sum() * 2)
    expected_pnl = float(sum(2 * unit * variant["er"] for unit, variant in zip(units, chosen)))

    return chosen, chosen_weights, units, portfolio_expected_return, portfolio_sd, reward_risk, total_stake, expected_pnl


def print_positive_table(variants: list[dict[str, object]]) -> None:
    print("| 投注项 | 执行方式 | 串关赔率 | 估计命中概率 | 期望收益 |")
    print("|---|---|---:|---:|---:|")
    for variant in variants:
        if float(variant["er"]) <= 0:
            continue
        print(
            f"| {variant['label']} | {variant['kind']} | {float(variant['odds']):.2f} | "
            f"{percent(float(variant['p']))} | {percent(float(variant['er']), signed=True)} |"
        )


def print_plan(chosen: list[dict[str, object]], weights: np.ndarray, units: np.ndarray) -> None:
    print("| 投注项 | 执行方式 | 连续权重 | 注数 | 投注金额 | 串关赔率 | 估计命中概率 | 期望收益 | 期望盈亏 |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    order = sorted(range(len(chosen)), key=lambda index: weights[index], reverse=True)
    for index in order:
        variant = chosen[index]
        unit = int(units[index])
        stake = unit * 2
        expected_pnl = stake * float(variant["er"])
        print(
            f"| {variant['label']} | {variant['kind']} | {percent(float(weights[index]))} | "
            f"{unit}注 | {stake}元 | {float(variant['odds']):.2f} | "
            f"{percent(float(variant['p']))} | {percent(float(variant['er']), signed=True)} | "
            f"{expected_pnl:.2f}元 |"
        )


def run_strategy(name: str, rows: list[dict[str, object]], base_joint_probability) -> None:
    variants = build_variants(rows)
    positive = [variant for variant in variants if float(variant["er"]) > 0]
    chosen, weights, units, expected_return, sd, reward_risk, stake, expected_pnl = optimize_weights(
        variants,
        base_joint_probability,
    )

    print(f"## {name}")
    print()
    print(f"基础事件数：{len(rows)}")
    print(f"扩展后可执行事件数：{len(variants)}")
    print(f"扩展后正期望事件数：{len(positive)}")
    print()
    print("扩展后正期望候选：")
    print()
    print_positive_table(variants)
    print()
    print("组合优化结果：")
    print()
    print(f"连续组合期望收益率：{percent(expected_return, signed=True)}")
    print(f"连续组合收益标准差：{sd:.4f}")
    print(f"连续组合收益风险比：{reward_risk:.4f}")
    print()
    print_plan(chosen, weights, units)
    print()
    print(f"总投入：{stake}元")
    print(f"组合期望盈亏：{expected_pnl:.2f}元")
    print()


def main() -> None:
    direct_rows = load_direct_rows()
    run_strategy("策略三：策略一事件空间扩展", direct_rows, direct_joint_probability)

    _, matrices, score_rows = load_score_rows()
    run_strategy(
        "策略四：策略二事件空间扩展",
        score_rows,
        lambda a, b: score_joint_probability(matrices, a, b),
    )


if __name__ == "__main__":
    main()
