#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[1]
SPORTTERY_ODDS = ROOT / "2026世界杯体彩赔率.md"
DEFAULT_CONDITION_MATCH = "法国 vs 瑞典"
DEFAULT_CONDITION_MARKET = "胜负"
DEFAULT_CONDITION_EVENT = "胜"
DEFAULT_MIN_KEEP_WEIGHT = 0.01


def load_estimator():
    path = ROOT / "scripts" / "estimate_score_distribution_limit_prob_space.py"
    spec = importlib.util.spec_from_file_location("estimate_score_distribution_limit_prob_space", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["estimate_score_distribution_limit_prob_space"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def csv_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def percent(value: float, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.2f}%"


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
                "match": matchup,
                "market": market_type,
                "event": event_name,
                "odds": float(odds_text),
            }
        )
    return rows


def condition_config() -> tuple[str, str, str]:
    return (
        os.environ.get("WORLDCUP_STRATEGY5_CONDITION_MATCH", DEFAULT_CONDITION_MATCH),
        os.environ.get("WORLDCUP_STRATEGY5_CONDITION_MARKET", DEFAULT_CONDITION_MARKET),
        os.environ.get("WORLDCUP_STRATEGY5_CONDITION_EVENT", DEFAULT_CONDITION_EVENT),
    )


def no_single_handicap_matches(target_matches: tuple[str, ...]) -> set[str]:
    raw = os.environ.get("WORLDCUP_NO_SINGLE_HANDICAP")
    if raw is not None:
        return csv_set(raw)
    return set(target_matches)


def min_keep_weight() -> float:
    raw = os.environ.get("WORLDCUP_STRATEGY5_MIN_KEEP_WEIGHT")
    if raw is None:
        return DEFAULT_MIN_KEEP_WEIGHT
    return float(raw)


def handicap_market(row: dict[str, object]) -> bool:
    return str(row["market"]).startswith("让负")


def condition_anchor(row: dict[str, object], condition: tuple[str, str, str]) -> bool:
    matchup, market, event = condition
    return row["match"] == matchup and row["market"] == market and row["event"] == event


def restricted_handicap(row: dict[str, object], restricted_matches: set[str]) -> bool:
    return handicap_market(row) and str(row["match"]) in restricted_matches


def single_allowed(
    row: dict[str, object],
    condition: tuple[str, str, str],
    restricted_matches: set[str],
) -> bool:
    return not condition_anchor(row, condition) and not restricted_handicap(row, restricted_matches)


def condition_result(estimator: object, result: dict[str, object], condition: tuple[str, str, str]) -> dict[str, object]:
    matchup, market, event = condition
    if result["matchup"] != matchup:
        return result

    condition_values = estimator.conditional_values(
        matchup,
        estimator.EventSpec(market, event),
        float(result["home_lambda"]),
        float(result["away_lambda"]),
    )
    probabilities = np.asarray(result["probabilities"], dtype=float)
    condition_probability = float(probabilities @ condition_values)
    if condition_probability <= 0:
        raise RuntimeError(f"条件事件概率为 0：{matchup} {market} {event}")

    conditioned = dict(result)
    conditioned["unconditioned_probabilities"] = probabilities
    conditioned["probabilities"] = probabilities * condition_values / condition_probability
    conditioned["condition_probability"] = condition_probability
    return conditioned


def solve_conditioned_results(estimator: object, condition: tuple[str, str, str]) -> dict[str, dict[str, object]]:
    events_by_match = estimator.collect_events()
    return {
        matchup: condition_result(
            estimator,
            estimator.solve_match(matchup, events_by_match[matchup]),
            condition,
        )
        for matchup in estimator.TARGET_MATCHES
    }


def row_probability(estimator: object, result: dict[str, object], row: dict[str, object]) -> float:
    return estimator.score_probability(result, str(row["market"]), str(row["event"]))


def base_joint_probability(
    estimator: object,
    results: dict[str, dict[str, object]],
    left: dict[str, object],
    right: dict[str, object],
) -> float:
    if left["match"] != right["match"]:
        return float(left["p"]) * float(right["p"])
    result = results[str(left["match"])]
    return estimator.joint_probability(
        result,
        str(left["market"]),
        str(left["event"]),
        str(right["market"]),
        str(right["event"]),
    )


def build_rows(estimator: object, results: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in load_sporttery_rows(estimator.TARGET_MATCHES):
        probability = row_probability(estimator, results[str(row["match"])], row)
        expected_return = probability * float(row["odds"]) - 1
        rows.append({**row, "p": probability, "er": expected_return})
    rows.sort(key=lambda item: float(item["er"]), reverse=True)
    return rows


def variant_label(variant: dict[str, object]) -> str:
    legs = variant["legs"]
    if variant["kind"] == "单关":
        leg = legs[0]
        return f"{leg['match']} {leg['market']} {leg['event']}"
    left, right = legs
    return (
        f"{left['match']} {left['market']} {left['event']} × "
        f"{right['match']} {right['market']} {right['event']}"
    )


def build_variants(
    rows: list[dict[str, object]],
    condition: tuple[str, str, str],
    restricted_matches: set[str],
) -> list[dict[str, object]]:
    variants: list[dict[str, object]] = []
    for row in rows:
        if single_allowed(row, condition, restricted_matches):
            variants.append(
                {
                    "kind": "单关",
                    "legs": (row,),
                    "odds": row["odds"],
                    "p": row["p"],
                    "er": row["er"],
                }
            )

    for left_index, left in enumerate(rows):
        for right in rows[left_index + 1:]:
            if left["match"] == right["match"]:
                continue
            if not (
                restricted_handicap(left, restricted_matches)
                or restricted_handicap(right, restricted_matches)
            ):
                continue
            probability = float(left["p"]) * float(right["p"])
            odds = float(left["odds"]) * float(right["odds"])
            variants.append(
                {
                    "kind": "2串1",
                    "legs": (left, right),
                    "odds": odds,
                    "p": probability,
                    "er": probability * odds - 1,
                }
            )

    variants.sort(key=lambda item: float(item["er"]), reverse=True)
    return variants


def variant_conditions(variant: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(leg["match"]): leg for leg in variant["legs"]}


def variant_joint_probability(
    estimator: object,
    results: dict[str, dict[str, object]],
    left: dict[str, object],
    right: dict[str, object],
) -> float:
    left_conditions = variant_conditions(left)
    right_conditions = variant_conditions(right)
    probability = 1.0
    for matchup in set(left_conditions) | set(right_conditions):
        left_leg = left_conditions.get(matchup)
        right_leg = right_conditions.get(matchup)
        if left_leg is not None and right_leg is not None:
            probability *= base_joint_probability(estimator, results, left_leg, right_leg)
        elif left_leg is not None:
            probability *= float(left_leg["p"])
        elif right_leg is not None:
            probability *= float(right_leg["p"])
    return probability


def optimize_variants(
    estimator: object,
    results: dict[str, dict[str, object]],
    variants: list[dict[str, object]],
    keep_threshold: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]], np.ndarray, np.ndarray, float, float, float, int, float]:
    positive = [variant for variant in variants if float(variant["er"]) > 1e-12]
    if not positive:
        return positive, [], np.array([]), np.array([]), 0.0, 0.0, 0.0, 0, 0.0

    n = len(positive)
    sigma = np.zeros((n, n), dtype=float)
    for i, left in enumerate(positive):
        for j in range(i, n):
            right = positive[j]
            joint = variant_joint_probability(estimator, results, left, right)
            covariance = (
                float(left["odds"])
                * float(right["odds"])
                * (joint - float(left["p"]) * float(right["p"]))
            )
            sigma[i, j] = sigma[j, i] = covariance

    mu = np.array([float(variant["er"]) for variant in positive], dtype=float)

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
    for _ in range(min(300, max(120, n))):
        initial_points.append(rng.dirichlet(np.ones(n)))

    best = None
    for initial in initial_points:
        result = minimize(
            objective,
            initial,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-12, "maxiter": 2000},
        )
        if result.success and (best is None or result.fun < best.fun):
            best = result
    if best is None:
        raise RuntimeError("组合优化失败")

    weights = best.x
    weights[weights < 1e-8] = 0.0
    weights = weights / weights.sum()
    chosen_indexes = [index for index, weight in enumerate(weights) if weight >= keep_threshold]
    if not chosen_indexes:
        chosen_indexes = [int(np.argmax(weights))]
    chosen = [positive[index] for index in chosen_indexes]
    chosen_weights = weights[chosen_indexes]
    chosen_weights = chosen_weights / chosen_weights.sum()
    chosen_sigma = sigma[np.ix_(chosen_indexes, chosen_indexes)]
    chosen_mu = mu[chosen_indexes]

    expected_return = float(chosen_weights @ chosen_mu)
    sd = float(np.sqrt(max(0.0, chosen_weights @ chosen_sigma @ chosen_weights)))
    reward_risk = expected_return / sd if sd > 0 else 0.0

    min_weight = float(chosen_weights[chosen_weights > 0].min())
    units = np.rint(chosen_weights / min_weight).astype(int)
    units[chosen_weights > 0] = np.maximum(units[chosen_weights > 0], 1)
    stake = int(units.sum() * 2)
    expected_pnl = float(sum(2 * unit * variant["er"] for unit, variant in zip(units, chosen)))
    return positive, chosen, chosen_weights, units, expected_return, sd, reward_risk, stake, expected_pnl


def main() -> None:
    estimator = load_estimator()
    condition = condition_config()
    restricted_matches = no_single_handicap_matches(estimator.TARGET_MATCHES)
    keep_threshold = min_keep_weight()
    results = solve_conditioned_results(estimator, condition)
    rows = build_rows(estimator, results)
    variants = build_variants(rows, condition, restricted_matches)
    positive, chosen, weights, units, expected_return, sd, reward_risk, stake, expected_pnl = optimize_variants(
        estimator,
        results,
        variants,
        keep_threshold,
    )

    condition_match, condition_market, condition_event = condition
    condition_probability = float(results[condition_match]["condition_probability"])

    print("## 策略五：已知法国至少赢 1 球")
    print()
    print(f"目标日期：{estimator.TARGET_DATE}")
    print(f"条件事件：{condition_match}，{condition_market} {condition_event}")
    print(f"条件事件原始估计概率：{percent(condition_probability)}")
    print(f"禁止让负单关比赛：{', '.join(sorted(restricted_matches))}")
    print(f"离散化保留阈值：连续权重不低于 {percent(keep_threshold)}")
    print()
    print("说明：条件事件本身不作为单关推荐；但可作为跨场 2 串 1 的锚点，用于满足让负盘口不能单买的限制。")
    print()
    print("条件概率下的体彩期望收益（前 30）：")
    print()
    print("| 比赛 | 玩法 | 事件 | 体彩赔率 | 条件概率 | 期望收益 | 单关处理 |")
    print("|---|---|---|---:|---:|---:|---|")
    for row in rows[:30]:
        if condition_anchor(row, condition):
            handling = "条件锚点，不单买"
        elif restricted_handicap(row, restricted_matches):
            handling = "让负不能单买，仅可串关"
        else:
            handling = "可单买"
        print(
            f"| {row['match']} | {row['market']} | {row['event']} | "
            f"{float(row['odds']):.2f} | {percent(float(row['p']))} | "
            f"{percent(float(row['er']), signed=True)} | {handling} |"
        )
    print()
    print(f"扩展后可执行事件数：{len(variants)}")
    print(f"扩展后正期望事件数：{len(positive)}")
    print()
    print("扩展后正期望候选（前 20）：")
    print()
    print("| 投注项 | 执行方式 | 串关赔率 | 条件命中概率 | 期望收益 |")
    print("|---|---|---:|---:|---:|")
    for variant in positive[:20]:
        print(
            f"| {variant_label(variant)} | {variant['kind']} | {float(variant['odds']):.2f} | "
            f"{percent(float(variant['p']))} | {percent(float(variant['er']), signed=True)} |"
        )
    print()

    if not chosen:
        print("策略五正式结论：不下注，现金仓位 100%。")
        print()
        print("完整投注方案如下：")
        print()
        print("不下注，现金仓位 100%。")
        print()
        print("每注 2 元，合计 0 注，总投入 0 元。")
        return

    print("策略五投注方案：存在正期望候选，按收益风险比构建组合。")
    print()
    print(f"连续组合期望收益率：{percent(expected_return, signed=True)}")
    print(f"连续组合收益标准差：{sd:.4f}")
    print(f"连续组合收益风险比：{reward_risk:.4f}")
    print()
    print("| 投注项 | 执行方式 | 连续权重 | 注数 | 投注金额 | 串关赔率 | 条件命中概率 | 期望收益 | 期望盈亏 |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for variant, weight, unit in zip(chosen, weights, units):
        stake_amount = int(unit * 2)
        print(
            f"| {variant_label(variant)} | {variant['kind']} | {percent(float(weight))} | "
            f"{int(unit)}注 | {stake_amount}元 | {float(variant['odds']):.2f} | "
            f"{percent(float(variant['p']))} | {percent(float(variant['er']), signed=True)} | "
            f"{stake_amount * float(variant['er']):.2f}元 |"
        )
    print()
    print(f"总投入：{stake}元")
    print(f"组合期望盈亏：{expected_pnl:.2f}元")
    print()
    print("完整投注方案如下：")
    print()
    for variant, unit in zip(chosen, units):
        if unit <= 0:
            continue
        print(f"{variant_label(variant)}，买 {int(unit)} 注。")
        print()
    print(f"每注 2 元，合计 {int(units.sum())} 注，总投入 {stake} 元。")


if __name__ == "__main__":
    main()
