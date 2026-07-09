#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations, product
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_DATE = "2026-07-01"
DEFAULT_SOURCE_CSV = (
    ROOT.parent
    / "world-cup-2026-arbitrage-trading-bot-main"
    / "data"
    / "match-quotes"
    / "2026-06-30_15-00.csv"
)

FRANCE_MATCH = "法国 vs 瑞典"
FRANCE_CONDITION_MARKET = "胜负"
FRANCE_CONDITION_EVENT = "胜"


os.environ.setdefault("WORLDCUP_TARGET_DATE", DEFAULT_TARGET_DATE)
os.environ.setdefault("WORLDCUP_POLYMARKET_CSV", str(DEFAULT_SOURCE_CSV))


def load_estimator():
    path = ROOT / "scripts" / "estimate_score_distribution_limit_prob_space.py"
    spec = importlib.util.spec_from_file_location("estimate_score_distribution_limit_prob_space", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["estimate_score_distribution_limit_prob_space"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


estimator = load_estimator()


@dataclass(frozen=True)
class Leg:
    match: str
    market: str
    event: str
    odds: float

    @property
    def key(self) -> str:
        return f"{self.match}|{self.market}|{self.event}"

    @property
    def label(self) -> str:
        return f"{self.match} {self.market} {self.event}"


@dataclass(frozen=True)
class BetUnit:
    ticket: str
    legs: tuple[Leg, ...]
    count: int = 1

    @property
    def stake(self) -> float:
        return 2.0 * self.count

    @property
    def gross(self) -> float:
        odds_product = math.prod(leg.odds for leg in self.legs)
        return 2.0 * self.count * odds_product


@dataclass(frozen=True)
class TicketRecord:
    ticket_id: str
    ticket_type: str
    count: int
    groups: tuple[tuple[Leg, ...], ...]
    pass_sizes: tuple[int, ...]
    note: str

    @property
    def stake(self) -> float:
        return sum(unit.stake for unit in expand_ticket(self))


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def yuan(value: float) -> str:
    return f"{value:.2f}"


def expand_ticket(ticket: TicketRecord) -> list[BetUnit]:
    units: list[BetUnit] = []
    for pass_size in ticket.pass_sizes:
        for group_indexes in combinations(range(len(ticket.groups)), pass_size):
            selected_groups = [ticket.groups[index] for index in group_indexes]
            for legs in product(*selected_groups):
                units.append(BetUnit(ticket.ticket_id, tuple(legs), ticket.count))
    return units


def l(match: str, market: str, event: str, odds: float) -> Leg:
    return Leg(match, market, event, odds)


def ticket_records() -> list[TicketRecord]:
    france = FRANCE_MATCH
    civ = "科特迪瓦 vs 挪威"
    mexico = "墨西哥 vs 厄瓜多尔"
    return [
        TicketRecord(
            "票1",
            "单场固定",
            1,
            (
                (l(france, "比分", "4:0", 15.00),),
                (l(france, "比分", "5:0", 34.00),),
                (l(france, "比分", "胜其他", 20.00),),
            ),
            (1,),
            "法国比分 4:0、5:0、胜其他，各 1 注",
        ),
        TicketRecord(
            "票2",
            "单场固定",
            5,
            (
                (l(france, "比分", "3:0", 7.00),),
                (l(france, "比分", "4:1", 13.00),),
            ),
            (1,),
            "法国比分 3:0、4:1，各 5 注",
        ),
        TicketRecord(
            "票3",
            "3场-2,3关",
            3,
            (
                (l(france, "让负（-1）", "胜", 1.600),),
                (l(civ, "胜负", "负", 1.800),),
                (l(mexico, "让负（-1）", "负", 1.640),),
            ),
            (2, 3),
            "法国让胜 × 挪威胜 × 墨西哥让负，2串1+3串1，各 3 注",
        ),
        TicketRecord(
            "票4",
            "3场-2,3关",
            2,
            (
                (l(france, "让负（-1）", "胜", 1.600),),
                (l(civ, "胜负", "负", 1.800),),
                (l(mexico, "让负（-1）", "平", 3.150),),
            ),
            (2, 3),
            "法国让胜 × 挪威胜 × 墨西哥让平，2串1+3串1，各 2 注",
        ),
        TicketRecord(
            "票5",
            "3场-2,3关",
            2,
            (
                (l(france, "让负（-1）", "胜", 1.600),),
                (l(civ, "胜负", "平", 3.380),),
                (l(mexico, "让负（-1）", "负", 1.640),),
            ),
            (2, 3),
            "法国让胜 × 科挪平 × 墨西哥让负，2串1+3串1，各 2 注",
        ),
        TicketRecord(
            "票6",
            "3场-2,3关",
            1,
            (
                (l(france, "让负（-1）", "胜", 1.600),),
                (l(civ, "胜负", "平", 3.380),),
                (l(mexico, "让负（-1）", "平", 3.150),),
            ),
            (2, 3),
            "法国让胜 × 科挪平 × 墨西哥让平，2串1+3串1，各 1 注",
        ),
        TicketRecord(
            "票7",
            "单场固定",
            1,
            (
                (l(civ, "比分", "0:2", 10.50),),
                (l(civ, "比分", "1:2", 5.00),),
                (l(france, "比分", "3:0", 7.00),),
                (l(france, "比分", "3:1", 6.25),),
                (l(mexico, "比分", "0:0", 6.00),),
                (l(mexico, "比分", "1:2", 15.50),),
            ),
            (1,),
            "科特迪瓦 0:2/1:2，法国 3:0/3:1，墨西哥 0:0/1:2，各 1 注",
        ),
        TicketRecord(
            "票8",
            "3场-2,3关",
            1,
            (
                (l(civ, "比分", "0:2", 10.50), l(civ, "比分", "1:2", 5.00)),
                (l(france, "比分", "3:0", 7.00), l(france, "比分", "3:1", 6.25)),
                (l(mexico, "比分", "0:0", 6.00), l(mexico, "比分", "1:2", 15.50)),
            ),
            (2, 3),
            "三场各选两个比分，展开全部跨场 2串1 和 3串1",
        ),
    ]


def score_distribution(result: dict[str, object]) -> dict[tuple[int, int], float]:
    probabilities = np.asarray(result["probabilities"], dtype=float)
    home_lambda = float(result["home_lambda"])
    away_lambda = float(result["away_lambda"])
    full_prior = estimator.full_score_prior(home_lambda, away_lambda)

    denominators = np.zeros(len(estimator.STATE_LABELS), dtype=float)
    for probability, (home_goals, away_goals) in zip(full_prior, estimator.FULL_SCORES):
        state_index = estimator.STATE_INDEX[estimator.state_label_for_score(home_goals, away_goals)]
        denominators[state_index] += probability

    distribution: dict[tuple[int, int], float] = {}
    for prior_probability, (home_goals, away_goals) in zip(full_prior, estimator.FULL_SCORES):
        state_index = estimator.STATE_INDEX[estimator.state_label_for_score(home_goals, away_goals)]
        if denominators[state_index] <= 0:
            continue
        distribution[(home_goals, away_goals)] = float(
            probabilities[state_index] * prior_probability / denominators[state_index]
        )
    return distribution


def condition_distribution(
    distribution: dict[tuple[int, int], float],
    matchup: str,
    market: str,
    event: str,
) -> tuple[dict[tuple[int, int], float], float]:
    conditioned: dict[tuple[int, int], float] = {}
    condition_probability = 0.0
    for score, probability in distribution.items():
        if estimator.event_hits_score(matchup, market, event, "", score[0], score[1]):
            conditioned[score] = probability
            condition_probability += probability
    if condition_probability <= 0:
        raise RuntimeError("条件事件概率为 0")
    return {score: probability / condition_probability for score, probability in conditioned.items()}, condition_probability


def solve_score_distributions() -> tuple[dict[str, dict[tuple[int, int], float]], float]:
    events_by_match = estimator.collect_events()
    distributions: dict[str, dict[tuple[int, int], float]] = {}
    condition_probability = 0.0
    for matchup in estimator.TARGET_MATCHES:
        result = estimator.solve_match(matchup, events_by_match[matchup])
        distribution = score_distribution(result)
        if matchup == FRANCE_MATCH:
            distribution, condition_probability = condition_distribution(
                distribution,
                FRANCE_MATCH,
                FRANCE_CONDITION_MARKET,
                FRANCE_CONDITION_EVENT,
            )
        distributions[matchup] = distribution
    return distributions, condition_probability


def leg_hits(leg: Leg, score: tuple[int, int]) -> bool:
    return estimator.event_hits_score(leg.match, leg.market, leg.event, "", score[0], score[1])


def class_label(matchup: str, score: tuple[int, int], legs: tuple[Leg, ...]) -> str:
    home, away = score
    score_text = f"{home}:{away}"
    exact_events = {leg.event for leg in legs if leg.market == "比分" and leg.event == score_text}
    if exact_events:
        return score_text
    other_events = {leg.event for leg in legs if leg.market == "比分" and leg.event.endswith("其他") and leg_hits(leg, score)}
    if other_events:
        return sorted(other_events)[0]
    if matchup == FRANCE_MATCH:
        if home - away == 1:
            return "法国赢1球其他"
        return "法国赢2球以上其他"
    if matchup == "科特迪瓦 vs 挪威":
        if home > away:
            return "科特迪瓦胜其他"
        if home == away:
            return "平其他"
        return "挪威胜其他"
    if matchup == "墨西哥 vs 厄瓜多尔":
        if home - away >= 2:
            return "墨西哥赢2球以上其他"
        if home - away == 1:
            return "墨西哥赢1球其他"
        if home == away:
            return "平其他"
        return "厄瓜多尔胜其他"
    return "其他"


def match_classes(
    matchup: str,
    distribution: dict[tuple[int, int], float],
    legs: tuple[Leg, ...],
) -> list[dict[str, object]]:
    grouped: dict[tuple[tuple[str, bool], str], float] = defaultdict(float)
    for score, probability in distribution.items():
        mask = tuple(sorted((leg.key, leg_hits(leg, score)) for leg in legs))
        label = class_label(matchup, score, legs)
        grouped[(mask, label)] += probability

    classes = [
        {
            "match": matchup,
            "mask": dict(mask),
            "label": label,
            "probability": probability,
        }
        for (mask, label), probability in grouped.items()
    ]
    classes.sort(key=lambda item: float(item["probability"]), reverse=True)
    return classes


def unit_hits(unit: BetUnit, masks_by_match: dict[str, dict[str, bool]]) -> bool:
    return all(masks_by_match[leg.match].get(leg.key, False) for leg in unit.legs)


def distribution_analysis(
    tickets: list[TicketRecord],
    distributions: dict[str, dict[tuple[int, int], float]],
) -> dict[str, object]:
    units_by_ticket = {ticket.ticket_id: expand_ticket(ticket) for ticket in tickets}
    all_units = [unit for units in units_by_ticket.values() for unit in units]
    stake = sum(unit.stake for unit in all_units)
    legs_by_match: dict[str, dict[str, Leg]] = defaultdict(dict)
    for unit in all_units:
        for leg in unit.legs:
            legs_by_match[leg.match][leg.key] = leg

    classes_by_match = {
        matchup: match_classes(matchup, distribution, tuple(legs_by_match[matchup].values()))
        for matchup, distribution in distributions.items()
    }

    gross_distribution: dict[float, float] = defaultdict(float)
    expected_gross_by_ticket: dict[str, float] = defaultdict(float)
    scenarios: list[dict[str, object]] = []
    match_order = tuple(distributions.keys())

    for class_combo in product(*(classes_by_match[matchup] for matchup in match_order)):
        probability = math.prod(float(match_class["probability"]) for match_class in class_combo)
        if probability <= 0:
            continue
        masks_by_match = {str(match_class["match"]): match_class["mask"] for match_class in class_combo}
        gross_by_ticket = {
            ticket_id: sum(unit.gross for unit in units if unit_hits(unit, masks_by_match))
            for ticket_id, units in units_by_ticket.items()
        }
        gross = round(sum(gross_by_ticket.values()), 2)
        gross_distribution[gross] += probability
        for ticket_id, gross_value in gross_by_ticket.items():
            expected_gross_by_ticket[ticket_id] += probability * gross_value
        labels = {str(match_class["match"]): str(match_class["label"]) for match_class in class_combo}
        scenarios.append(
            {
                "probability": probability,
                "gross": gross,
                "net": gross - stake,
                "labels": labels,
            }
        )

    net_distribution = {
        round(gross - stake, 2): probability for gross, probability in gross_distribution.items()
    }
    expected_gross = sum(gross * probability for gross, probability in gross_distribution.items())
    expected_net = expected_gross - stake
    profit_probability = sum(probability for net, probability in net_distribution.items() if net > 0)
    lose_all_probability = net_distribution.get(round(-stake, 2), 0.0)

    return {
        "stake": stake,
        "units": all_units,
        "units_by_ticket": units_by_ticket,
        "classes_by_match": classes_by_match,
        "net_distribution": net_distribution,
        "expected_gross": expected_gross,
        "expected_net": expected_net,
        "profit_probability": profit_probability,
        "lose_all_probability": lose_all_probability,
        "expected_gross_by_ticket": dict(expected_gross_by_ticket),
        "scenarios": scenarios,
    }


def quantile(net_distribution: dict[float, float], q: float) -> float:
    cumulative = 0.0
    for net, probability in sorted(net_distribution.items()):
        cumulative += probability
        if cumulative + 1e-12 >= q:
            return net
    return max(net_distribution)


def bucket_distribution(net_distribution: dict[float, float], stake: float) -> list[tuple[str, float]]:
    buckets = [
        ("亏完本金 -142", lambda net: abs(net + stake) < 1e-9),
        ("亏 120-142", lambda net: -stake < net <= -120),
        ("亏 100-120", lambda net: -120 < net <= -100),
        ("亏 50-100", lambda net: -100 < net <= -50),
        ("亏 0-50", lambda net: -50 < net < 0),
        ("盈利 0-100", lambda net: 0 < net <= 100),
        ("盈利 100-300", lambda net: 100 < net <= 300),
        ("盈利 300-600", lambda net: 300 < net <= 600),
        ("盈利 600-1000", lambda net: 600 < net <= 1000),
        ("盈利 1000以上", lambda net: net > 1000),
    ]
    return [
        (label, sum(probability for net, probability in net_distribution.items() if predicate(net)))
        for label, predicate in buckets
    ]


def top_scenarios(scenarios: list[dict[str, object]], limit: int = 8) -> list[dict[str, object]]:
    positive = [scenario for scenario in scenarios if float(scenario["net"]) > 0]
    positive.sort(key=lambda item: (float(item["net"]), float(item["probability"])), reverse=True)
    return positive[:limit]


def key_class_probabilities(classes_by_match: dict[str, list[dict[str, object]]]) -> dict[str, list[tuple[str, float]]]:
    output: dict[str, list[tuple[str, float]]] = {}
    for matchup, classes in classes_by_match.items():
        output[matchup] = [
            (str(item["label"]), float(item["probability"]))
            for item in classes
            if str(item["label"])
            in {
                "3:0",
                "3:1",
                "4:0",
                "4:1",
                "5:0",
                "胜其他",
                "0:2",
                "1:2",
                "挪威胜其他",
                "平其他",
                "科特迪瓦胜其他",
                "0:0",
                "墨西哥赢1球其他",
                "墨西哥赢2球以上其他",
                "厄瓜多尔胜其他",
            }
        ]
    return output


def render_ticket_table(tickets: list[TicketRecord]) -> str:
    rows = [
        "| 票据 | 类型 | 倍数 | 投注内容 | 金额 |",
        "|---|---|---:|---|---:|",
    ]
    for ticket in tickets:
        rows.append(
            f"| {ticket.ticket_id} | {ticket.ticket_type} | {ticket.count} | {ticket.note} | {ticket.stake:.0f}元 |"
        )
    return "\n".join(rows)


def render_key_probabilities(class_probabilities: dict[str, list[tuple[str, float]]]) -> str:
    rows = [
        "| 比赛 | 结果类 | 条件/无条件概率 |",
        "|---|---|---:|",
    ]
    order = {
        FRANCE_MATCH: ("3:0", "3:1", "4:0", "4:1", "5:0", "胜其他"),
        "科特迪瓦 vs 挪威": ("0:2", "1:2", "挪威胜其他", "平其他", "科特迪瓦胜其他"),
        "墨西哥 vs 厄瓜多尔": ("0:0", "1:2", "墨西哥赢1球其他", "墨西哥赢2球以上其他", "厄瓜多尔胜其他", "平其他"),
    }
    for matchup, labels in order.items():
        lookup = defaultdict(float)
        for label, probability in class_probabilities.get(matchup, []):
            lookup[label] += probability
        for label in labels:
            if lookup[label] > 0:
                rows.append(f"| {matchup} | {label} | {pct(lookup[label])} |")
    return "\n".join(rows)


def render_expected_by_ticket(tickets: list[TicketRecord], expected_gross_by_ticket: dict[str, float]) -> str:
    rows = [
        "| 票据 | 投入 | 期望返奖 | 期望净盈亏 |",
        "|---|---:|---:|---:|",
    ]
    for ticket in tickets:
        gross = expected_gross_by_ticket[ticket.ticket_id]
        rows.append(
            f"| {ticket.ticket_id} | {ticket.stake:.0f}元 | {yuan(gross)}元 | {yuan(gross - ticket.stake)}元 |"
        )
    return "\n".join(rows)


def render_distribution_section(analysis: dict[str, object]) -> str:
    net_distribution = analysis["net_distribution"]
    stake = float(analysis["stake"])
    rows: list[str] = []
    rows.append("| 指标 | 数值 |")
    rows.append("|---|---:|")
    rows.append(f"| 总投入 | {stake:.0f}元 |")
    rows.append(f"| 展开投注单元 | {len(analysis['units'])} 个 |")
    rows.append(f"| 期望返奖 | {yuan(float(analysis['expected_gross']))}元 |")
    rows.append(f"| 期望净盈亏 | {yuan(float(analysis['expected_net']))}元 |")
    rows.append(f"| 期望 ROI | {pct(float(analysis['expected_net']) / stake)} |")
    rows.append(f"| 盈利概率 | {pct(float(analysis['profit_probability']))} |")
    rows.append(f"| 亏完本金概率 | {pct(float(analysis['lose_all_probability']))} |")
    rows.append(f"| 分布状态数 | {len(net_distribution)} |")
    return "\n".join(rows)


def render_quantiles(net_distribution: dict[float, float]) -> str:
    rows = [
        "| 分位点 | 净盈亏 |",
        "|---|---:|",
    ]
    for q in (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99):
        rows.append(f"| {int(q * 100)}% | {yuan(quantile(net_distribution, q))}元 |")
    return "\n".join(rows)


def render_buckets(net_distribution: dict[float, float], stake: float) -> str:
    rows = [
        "| 净盈亏区间 | 概率 |",
        "|---|---:|",
    ]
    for label, probability in bucket_distribution(net_distribution, stake):
        rows.append(f"| {label} | {pct(probability)} |")
    return "\n".join(rows)


def render_top_scenarios(scenarios: list[dict[str, object]]) -> str:
    rows = [
        "| 净盈亏 | 返奖 | 概率 | 法国 vs 瑞典 | 科特迪瓦 vs 挪威 | 墨西哥 vs 厄瓜多尔 |",
        "|---:|---:|---:|---|---|---|",
    ]
    for scenario in top_scenarios(scenarios):
        labels = scenario["labels"]
        rows.append(
            f"| {yuan(float(scenario['net']))}元 | {yuan(float(scenario['gross']))}元 | "
            f"{pct(float(scenario['probability']))} | {labels[FRANCE_MATCH]} | "
            f"{labels['科特迪瓦 vs 挪威']} | {labels['墨西哥 vs 厄瓜多尔']} |"
        )
    return "\n".join(rows)


def main() -> None:
    tickets = ticket_records()
    distributions, condition_probability = solve_score_distributions()
    analysis = distribution_analysis(tickets, distributions)
    class_probabilities = key_class_probabilities(analysis["classes_by_match"])

    print("## 策略六：最终实投记录与收益分布")
    print()
    print(
        "策略六按票面图片记录，合计投入 `142元`。收益分析沿用策略五的条件口径："
        f"`{FRANCE_MATCH} {FRANCE_CONDITION_MARKET} {FRANCE_CONDITION_EVENT}`，"
        f"即法国至少赢 1 球；该条件在策略三有限空间模型下的原始概率为 `{pct(condition_probability)}`。"
    )
    print()
    print("### 策略六实投记录")
    print()
    print(render_ticket_table(tickets))
    print()
    print("### 策略六关键概率")
    print()
    print(render_key_probabilities(class_probabilities))
    print()
    print("### 收益分布摘要")
    print()
    print(render_distribution_section(analysis))
    print()
    print("### 净盈亏分位数")
    print()
    print(render_quantiles(analysis["net_distribution"]))
    print()
    print("### 净盈亏区间分布")
    print()
    print(render_buckets(analysis["net_distribution"], float(analysis["stake"])))
    print()
    print("### 各票据期望返奖")
    print()
    print(render_expected_by_ticket(tickets, analysis["expected_gross_by_ticket"]))
    print()
    print("### 右尾高收益情景")
    print()
    print(render_top_scenarios(analysis["scenarios"]))
    print()
    print(
        "解读：策略六把策略五的 56 元条件组合扩展为 142 元实投组合，增加了多个精确比分单关和比分串关。"
        "这会显著放大右尾收益，但在当前条件概率模型下，期望净盈亏为负；主要原因是新增覆盖项中不少仍是负期望，"
        "它们提高命中后返奖弹性，同时拉低整体期望 ROI。"
    )


if __name__ == "__main__":
    main()
