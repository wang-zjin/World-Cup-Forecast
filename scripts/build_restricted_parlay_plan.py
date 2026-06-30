#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import argparse
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[1]
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


def line_date(time_text: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", time_text)
    return match.group(1) if match else None


def percent(value: float, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.2f}%"


def numeric(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def csv_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def single_win_allowed_matches() -> set[str]:
    raw = os.environ.get("WORLDCUP_SINGLE_WIN_ALLOWED", "加纳 vs 巴拿马")
    return csv_set(raw)


def no_single_win_matches() -> set[str] | None:
    raw = os.environ.get("WORLDCUP_NO_SINGLE_WIN")
    if raw is None:
        return None
    return csv_set(raw)


def no_single_handicap_matches() -> set[str] | None:
    raw = os.environ.get("WORLDCUP_NO_SINGLE_HANDICAP")
    if raw is None:
        return None
    return csv_set(raw)


def uses_explicit_restrictions() -> bool:
    return (
        os.environ.get("WORLDCUP_NO_SINGLE_WIN") is not None
        or os.environ.get("WORLDCUP_NO_SINGLE_HANDICAP") is not None
    )


def handicap_market(row: dict[str, object]) -> bool:
    return str(row["market"]).startswith("让负")


def win_market(row: dict[str, object]) -> bool:
    return row["market"] == "胜负"


def score_market(row: dict[str, object]) -> bool:
    return row["market"] == "比分"


def side_market(row: dict[str, object]) -> bool:
    return win_market(row) or handicap_market(row)


def parlay_mode() -> str:
    return os.environ.get("WORLDCUP_PARLAY_MODE", "default")


def parlay_market_allowed(left: dict[str, object], right: dict[str, object]) -> bool:
    mode = parlay_mode()
    if mode == "side-only":
        return side_market(left) and side_market(right)
    if mode == "no-score-with-side":
        return not (
            (score_market(left) and side_market(right))
            or (side_market(left) and score_market(right))
        )
    return True


def single_restricted_by_explicit_rules(row: dict[str, object]) -> bool:
    no_single_win = no_single_win_matches() or set()
    no_single_handicap = no_single_handicap_matches() or set()
    return (
        row["market"] == "胜负"
        and row["match"] in no_single_win
    ) or (
        handicap_market(row)
        and row["match"] in no_single_handicap
    )


def event_mask(row: dict[str, object]) -> np.ndarray:
    mask = np.zeros((11, 11), dtype=bool)
    for home_goals in range(11):
        for away_goals in range(11):
            hit = False
            if row["market"] == "比分" and re.match(r"^\d+:\d+$", str(row["event"])):
                home, away = map(int, str(row["event"]).split(":"))
                hit = home_goals == home and away_goals == away
            elif row["market"] == "胜负":
                hit = (
                    (row["event"] == "胜" and home_goals > away_goals)
                    or (row["event"] == "平" and home_goals == away_goals)
                    or (row["event"] == "负" and home_goals < away_goals)
                )
            elif str(row["market"]).startswith("让负"):
                handicap = int(re.search(r"（([+-]?\d+)）", str(row["market"])).group(1))
                adjusted = home_goals + handicap - away_goals
                hit = (
                    (row["event"] == "胜" and adjusted > 0)
                    or (row["event"] == "平" and adjusted == 0)
                    or (row["event"] == "负" and adjusted < 0)
                )
            if hit:
                mask[home_goals, away_goals] = True
    return mask


def load_direct_rows() -> list[dict[str, object]]:
    estimator = load_module("estimate_score_distribution", ROOT / "scripts" / "estimate_score_distribution.py")
    target_date = os.environ.get("WORLDCUP_TARGET_DATE") or estimator.TARGET_DATE
    target_matches = set(estimator.TARGET_MATCHES)

    sporttery: dict[tuple[str, str, str], float] = {}
    for line in SPORTTERY_ODDS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| 时间 ") or line.startswith("|---"):
            continue
        cells = markdown_cells(line)
        if len(cells) != 5:
            continue
        time_text, matchup, market_type, event_name, odds_text = cells
        if matchup not in target_matches or line_date(time_text) != target_date or not numeric(odds_text):
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
        if matchup not in target_matches or line_date(time_text) != target_date or not numeric(probability_text):
            continue
        polymarket[(matchup, market_type, event_name)] = float(probability_text)

    rows: list[dict[str, object]] = []
    for key, odds in sporttery.items():
        if key not in polymarket:
            continue
        matchup, market_type, event_name = key
        if market_type not in {"胜负", "比分"}:
            continue
        probability = polymarket[key]
        row = {
            "match": matchup,
            "market": market_type,
            "event": event_name,
            "odds": odds,
            "p": probability,
            "er": probability * odds - 1,
        }
        row["mask"] = event_mask(row)
        rows.append(row)
    rows.sort(key=lambda item: float(item["er"]), reverse=True)
    return rows


def load_score_rows() -> tuple[list[dict[str, object]], dict[str, np.ndarray]]:
    plan = load_module("build_optimal_betting_plan", ROOT / "scripts" / "build_optimal_betting_plan.py")
    compare, matrices, raw_rows = plan.matrix_rows()
    rows: list[dict[str, object]] = []
    for raw in raw_rows:
        row = {
            "match": raw["matchup"],
            "market": raw["market_type"],
            "event": raw["event_name"],
            "odds": float(raw["odds"]),
            "p": float(raw["probability"]),
            "er": float(raw["expected_return"]),
        }
        row["mask"] = compare.score_mask(matrices[row["match"]], row["market"], row["event"])
        rows.append(row)
    rows.sort(key=lambda item: float(item["er"]), reverse=True)
    return rows, matrices


def single_allowed(row: dict[str, object]) -> bool:
    if uses_explicit_restrictions():
        return not single_restricted_by_explicit_rules(row)
    if row["market"] == "比分":
        return True
    return row["market"] == "胜负" and row["match"] in single_win_allowed_matches()


def restricted(row: dict[str, object]) -> bool:
    if uses_explicit_restrictions():
        return single_restricted_by_explicit_rules(row)
    return (
        row["market"] == "胜负"
        and row["match"] not in single_win_allowed_matches()
    ) or handicap_market(row)


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


def build_variants(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    variants: list[dict[str, object]] = []
    for row in rows:
        if single_allowed(row):
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
            if not (restricted(left) or restricted(right)):
                continue
            if not parlay_market_allowed(left, right):
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


def base_joint_probability(
    left: dict[str, object],
    right: dict[str, object],
    matrices: dict[str, np.ndarray] | None,
) -> float:
    if left["match"] != right["match"]:
        return float(left["p"]) * float(right["p"])
    if matrices is None:
        intersection = left["mask"] & right["mask"]
        if left is right:
            return float(left["p"])
        if not intersection.any():
            return 0.0
        if np.array_equal(intersection, left["mask"]):
            return float(left["p"])
        if np.array_equal(intersection, right["mask"]):
            return float(right["p"])
        return min(float(left["p"]), float(right["p"]))
    matrix = matrices[left["match"]]
    return float(matrix[left["mask"] & right["mask"]].sum())


def variant_conditions(variant: dict[str, object]) -> dict[str, dict[str, object]]:
    return {leg["match"]: leg for leg in variant["legs"]}


def variant_joint_probability(
    left: dict[str, object],
    right: dict[str, object],
    matrices: dict[str, np.ndarray] | None,
) -> float:
    left_conditions = variant_conditions(left)
    right_conditions = variant_conditions(right)
    probability = 1.0
    for matchup in set(left_conditions) | set(right_conditions):
        left_leg = left_conditions.get(matchup)
        right_leg = right_conditions.get(matchup)
        if left_leg is not None and right_leg is not None:
            probability *= base_joint_probability(left_leg, right_leg, matrices)
        elif left_leg is not None:
            probability *= float(left_leg["p"])
        elif right_leg is not None:
            probability *= float(right_leg["p"])
    return probability


def optimize_variants(
    variants: list[dict[str, object]],
    matrices: dict[str, np.ndarray] | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], np.ndarray, np.ndarray, float, float, float, int, float]:
    positive = [variant for variant in variants if float(variant["er"]) > 1e-12]
    n = len(positive)
    if not positive:
        return positive, [], np.array([]), np.array([]), 0.0, 0.0, 0.0, 0, 0.0

    sigma = np.zeros((n, n))
    for i, left in enumerate(positive):
        for j in range(i, n):
            right = positive[j]
            joint_probability = variant_joint_probability(left, right, matrices)
            covariance = (
                float(left["odds"])
                * float(right["odds"])
                * (joint_probability - float(left["p"]) * float(right["p"]))
            )
            sigma[i, j] = sigma[j, i] = covariance

    mu = np.array([float(variant["er"]) for variant in positive])

    def objective(weights: np.ndarray) -> float:
        expected_return = float(weights @ mu)
        variance = float(weights @ sigma @ weights)
        if variance <= 1e-14:
            return 1e9
        return -expected_return / math.sqrt(variance)

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
    chosen_indexes = [index for index, weight in enumerate(weights) if weight > 0]
    chosen = [positive[index] for index in chosen_indexes]
    chosen_weights = weights[chosen_indexes]
    chosen_sigma = sigma[np.ix_(chosen_indexes, chosen_indexes)]
    chosen_mu = mu[chosen_indexes]

    expected_return = float(chosen_weights @ chosen_mu)
    sd = float(math.sqrt(max(0.0, chosen_weights @ chosen_sigma @ chosen_weights)))
    reward_risk = expected_return / sd if sd > 0 else 0.0

    min_weight = float(chosen_weights[chosen_weights > 0].min())
    units = np.rint(chosen_weights / min_weight).astype(int)
    units[chosen_weights > 0] = np.maximum(units[chosen_weights > 0], 1)
    stake = int(units.sum() * 2)
    expected_pnl = float(sum(2 * unit * variant["er"] for unit, variant in zip(units, chosen)))
    return positive, chosen, chosen_weights, units, expected_return, sd, reward_risk, stake, expected_pnl


def print_report(
    title: str,
    rows: list[dict[str, object]],
    variants: list[dict[str, object]],
    matrices: dict[str, np.ndarray] | None,
) -> None:
    positive, chosen, weights, units, expected_return, sd, reward_risk, stake, expected_pnl = optimize_variants(
        variants,
        matrices,
    )

    print(f"## {title}")
    print()
    print(f"基础事件数：{len(rows)}")
    print(f"扩展后可执行事件数：{len(variants)}")
    print(f"扩展后正期望事件数：{len(positive)}")
    print()
    print("扩展后正期望候选（前 20）：")
    print()
    print("| 投注项 | 执行方式 | 串关赔率 | 估计命中概率 | 期望收益 |")
    print("|---|---|---:|---:|---:|")
    for variant in positive[:20]:
        print(
            f"| {variant_label(variant)} | {variant['kind']} | {float(variant['odds']):.2f} | "
            f"{percent(float(variant['p']))} | {percent(float(variant['er']), signed=True)} |"
        )
    print()
    print("组合优化结果：")
    print()
    print(f"连续组合期望收益率：{percent(expected_return, signed=True)}")
    print(f"连续组合收益标准差：{sd:.4f}")
    print(f"连续组合收益风险比：{reward_risk:.4f}")
    print()
    print("| 投注项 | 执行方式 | 连续权重 | 注数 | 投注金额 | 串关赔率 | 估计命中概率 | 期望收益 | 期望盈亏 |")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="按单关限制生成 2026 世界杯策略三/四串关扩展方案")
    parser.add_argument(
        "--base",
        choices=("direct", "score", "both"),
        default="both",
        help="选择直接同口径事件、比分分布事件，或两者都输出",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="只输出一个 base 时可覆盖报告标题",
    )
    parser.add_argument(
        "--single-win-allowed",
        default=os.environ.get("WORLDCUP_SINGLE_WIN_ALLOWED", "加纳 vs 巴拿马"),
        help="逗号分隔的可单关胜负比赛，默认读取 WORLDCUP_SINGLE_WIN_ALLOWED 或使用加纳 vs 巴拿马",
    )
    parser.add_argument(
        "--no-single-win",
        default=os.environ.get("WORLDCUP_NO_SINGLE_WIN"),
        help="逗号分隔的禁止胜负单关比赛；设置后优先使用显式限制口径",
    )
    parser.add_argument(
        "--no-single-handicap",
        default=os.environ.get("WORLDCUP_NO_SINGLE_HANDICAP"),
        help="逗号分隔的禁止让负单关比赛；设置后优先使用显式限制口径",
    )
    parser.add_argument(
        "--parlay-mode",
        choices=("default", "no-score-with-side", "side-only"),
        default=os.environ.get("WORLDCUP_PARLAY_MODE", "default"),
        help="串关市场限制：default 为旧规则，no-score-with-side 禁止比分和胜负/让负串，side-only 只允许胜负/让负串",
    )
    args = parser.parse_args()
    os.environ["WORLDCUP_SINGLE_WIN_ALLOWED"] = args.single_win_allowed
    os.environ["WORLDCUP_PARLAY_MODE"] = args.parlay_mode
    if args.no_single_win is not None:
        os.environ["WORLDCUP_NO_SINGLE_WIN"] = args.no_single_win
    if args.no_single_handicap is not None:
        os.environ["WORLDCUP_NO_SINGLE_HANDICAP"] = args.no_single_handicap

    if args.base in {"direct", "both"}:
        direct_title = args.title if args.base == "direct" and args.title else "策略三：策略一限制后事件空间"
        direct_rows = load_direct_rows()
        print_report(direct_title, direct_rows, build_variants(direct_rows), None)

    if args.base in {"score", "both"}:
        score_title = args.title if args.base == "score" and args.title else "策略四：策略二限制后事件空间"
        score_rows, matrices = load_score_rows()
        print_report(score_title, score_rows, build_variants(score_rows), matrices)


if __name__ == "__main__":
    main()
