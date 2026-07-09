#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson


ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT.parent / "world-cup-2026-arbitrage-trading-bot-main"
SPORTTERY_ODDS = ROOT / "2026世界杯体彩赔率.md"
BEIJING_TZ = timezone(timedelta(hours=8))
FULL_GMAX = 25
RIDGE = 0.02
INCLUDED_TYPES = {
    "胜负",
    "比分",
    "全场大小球（1.5）",
    "全场大小球（2.5）",
    "全场大小球（3.5）",
    "全场大小球（4.5）",
    "全场大小球（9.5）",
    "双方进球",
}

EXACT_HOME_WINS = (
    "1:0",
    "2:0",
    "2:1",
    "3:0",
    "3:1",
    "3:2",
    "4:0",
    "4:1",
    "4:2",
    "5:0",
    "5:1",
    "5:2",
)
EXACT_DRAWS = ("0:0", "1:1", "2:2", "3:3")
EXACT_AWAY_WINS = (
    "0:1",
    "0:2",
    "1:2",
    "0:3",
    "1:3",
    "2:3",
    "0:4",
    "1:4",
    "2:4",
    "0:5",
    "1:5",
    "2:5",
)
STATE_LABELS = (
    *EXACT_HOME_WINS,
    "胜其他",
    *EXACT_DRAWS,
    "平其他",
    *EXACT_AWAY_WINS,
    "负其他",
)


@dataclass(frozen=True)
class EventSpec:
    market_type: str
    event_name: str
    raw_market: str = ""


def latest_csv(subdir: str) -> Path:
    files = sorted((BOT_ROOT / "data" / subdir).glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"未找到 Polymarket {subdir} CSV")
    return files[-1]


def default_target_date() -> str:
    return (datetime.now(BEIJING_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")


def markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def line_date(time_text: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", time_text)
    return match.group(1) if match else None


def load_target_matches(target_date: str) -> tuple[str, ...]:
    matches: list[str] = []
    seen: set[str] = set()
    for line in SPORTTERY_ODDS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| 时间 ") or line.startswith("|---"):
            continue
        cells = markdown_cells(line)
        if len(cells) < 2:
            continue
        time_text, matchup = cells[0], cells[1]
        if line_date(time_text) == target_date and matchup not in seen:
            seen.add(matchup)
            matches.append(matchup)
    return tuple(matches)


TARGET_DATE = os.environ.get("WORLDCUP_TARGET_DATE") or default_target_date()
TARGET_MATCHES = load_target_matches(TARGET_DATE)
SOURCE_CSV = (
    Path(os.environ["WORLDCUP_POLYMARKET_CSV"])
    if os.environ.get("WORLDCUP_POLYMARKET_CSV")
    else latest_csv("match-quotes")
)


def load_polymarket_helpers():
    path = ROOT / "scripts" / "generate_polymarket_odds.py"
    spec = importlib.util.spec_from_file_location("gpo", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gpo"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def included_type(market_type: str) -> bool:
    return market_type in INCLUDED_TYPES or market_type.startswith("让负")


gpo = load_polymarket_helpers()
FULL_GOALS = tuple(range(FULL_GMAX + 1))
FULL_SCORES = tuple((home, away) for home in FULL_GOALS for away in FULL_GOALS)
STATE_INDEX = {label: index for index, label in enumerate(STATE_LABELS)}
EXACT_STATE_LABELS = set(EXACT_HOME_WINS) | set(EXACT_DRAWS) | set(EXACT_AWAY_WINS)


def score_label(home_goals: int, away_goals: int) -> str:
    return f"{home_goals}:{away_goals}"


def state_label_for_score(home_goals: int, away_goals: int) -> str:
    label = score_label(home_goals, away_goals)
    if label in EXACT_STATE_LABELS:
        return label
    if home_goals > away_goals:
        return "胜其他"
    if home_goals == away_goals:
        return "平其他"
    return "负其他"


def sporttery_handicap_hit(market_type: str, event_name: str, home_goals: int, away_goals: int) -> bool:
    handicap = int(re.search(r"（([+-]?\d+)）", market_type).group(1))
    adjusted = home_goals + handicap - away_goals
    return (
        (event_name == "胜" and adjusted > 0)
        or (event_name == "平" and adjusted == 0)
        or (event_name == "负" and adjusted < 0)
    )


def total_goals_hit(event_name: str, home_goals: int, away_goals: int) -> bool:
    total = home_goals + away_goals
    exact = re.match(r"^(\d+)球$", event_name)
    if exact:
        return total == int(exact.group(1))
    return event_name in {"7+球", "7球及以上"} and total >= 7


def polymarket_handicap_hit(
    matchup: str,
    event_name: str,
    raw_market: str,
    home_goals: int,
    away_goals: int,
) -> bool:
    home_team, _ = matchup.split(" vs ")
    spread = re.match(r"^(.+?)\s+\((-?[0-9.]+)\)$", raw_market)
    if not spread:
        return False
    spread_team = gpo.team_zh(spread.group(1))
    threshold = int(abs(float(spread.group(2))) + 0.5)
    diff = home_goals - away_goals if spread_team == home_team else away_goals - home_goals
    return diff >= threshold if event_name == spread_team else diff <= threshold - 1


def event_hits_score(
    matchup: str,
    market_type: str,
    event_name: str,
    raw_market: str,
    home_goals: int,
    away_goals: int,
) -> bool:
    if market_type == "胜负":
        return (
            (event_name == "胜" and home_goals > away_goals)
            or (event_name == "平" and home_goals == away_goals)
            or (event_name == "负" and home_goals < away_goals)
        )
    if market_type == "比分":
        if re.match(r"^\d+:\d+$", event_name):
            return score_label(home_goals, away_goals) == event_name
        if event_name in {"胜其他", "平其他", "负其他"}:
            return state_label_for_score(home_goals, away_goals) == event_name
        return False
    if market_type.startswith("全场大小球"):
        line = float(re.search(r"（([0-9.]+)）", market_type).group(1))
        total = home_goals + away_goals
        return total > line if event_name == "大" else total < line
    if market_type == "双方进球":
        return (home_goals > 0 and away_goals > 0) if event_name == "是" else (home_goals == 0 or away_goals == 0)
    if market_type.startswith("让负"):
        if event_name in {"胜", "平", "负"}:
            return sporttery_handicap_hit(market_type, event_name, home_goals, away_goals)
        return polymarket_handicap_hit(matchup, event_name, raw_market, home_goals, away_goals)
    if market_type == "进球数":
        return total_goals_hit(event_name, home_goals, away_goals)
    return False


def event_has_support(matchup: str, event: EventSpec) -> bool:
    return any(
        event_hits_score(
            matchup,
            event.market_type,
            event.event_name,
            event.raw_market,
            home_goals,
            away_goals,
        )
        for home_goals, away_goals in FULL_SCORES
    )


def full_score_prior(home_lambda: float, away_lambda: float) -> np.ndarray:
    home = np.array([poisson.pmf(goal, home_lambda) for goal in FULL_GOALS])
    away = np.array([poisson.pmf(goal, away_lambda) for goal in FULL_GOALS])
    prior = np.outer(home, away).reshape(-1)
    return prior / prior.sum()


def state_prior(home_lambda: float, away_lambda: float) -> np.ndarray:
    full = full_score_prior(home_lambda, away_lambda)
    prior = np.zeros(len(STATE_LABELS), dtype=float)
    for probability, (home_goals, away_goals) in zip(full, FULL_SCORES):
        prior[STATE_INDEX[state_label_for_score(home_goals, away_goals)]] += probability
    return prior / prior.sum()


def conditional_values(
    matchup: str,
    event: EventSpec,
    home_lambda: float,
    away_lambda: float,
) -> np.ndarray:
    full = full_score_prior(home_lambda, away_lambda)
    numerators = np.zeros(len(STATE_LABELS), dtype=float)
    denominators = np.zeros(len(STATE_LABELS), dtype=float)
    for probability, (home_goals, away_goals) in zip(full, FULL_SCORES):
        state_index = STATE_INDEX[state_label_for_score(home_goals, away_goals)]
        denominators[state_index] += probability
        if event_hits_score(
            matchup,
            event.market_type,
            event.event_name,
            event.raw_market,
            home_goals,
            away_goals,
        ):
            numerators[state_index] += probability

    values = np.zeros(len(STATE_LABELS), dtype=float)
    positive = denominators > 0
    values[positive] = numerators[positive] / denominators[positive]
    return values


def conditional_joint_values(
    matchup: str,
    left: EventSpec,
    right: EventSpec,
    home_lambda: float,
    away_lambda: float,
) -> np.ndarray:
    full = full_score_prior(home_lambda, away_lambda)
    numerators = np.zeros(len(STATE_LABELS), dtype=float)
    denominators = np.zeros(len(STATE_LABELS), dtype=float)
    for probability, (home_goals, away_goals) in zip(full, FULL_SCORES):
        state_index = STATE_INDEX[state_label_for_score(home_goals, away_goals)]
        denominators[state_index] += probability
        left_hit = event_hits_score(
            matchup,
            left.market_type,
            left.event_name,
            left.raw_market,
            home_goals,
            away_goals,
        )
        right_hit = event_hits_score(
            matchup,
            right.market_type,
            right.event_name,
            right.raw_market,
            home_goals,
            away_goals,
        )
        if left_hit and right_hit:
            numerators[state_index] += probability

    values = np.zeros(len(STATE_LABELS), dtype=float)
    positive = denominators > 0
    values[positive] = numerators[positive] / denominators[positive]
    return values


def collect_events() -> dict[str, list[dict[str, object]]]:
    quotes = gpo.read_csv(SOURCE_CSV)
    events_by_match: dict[str, list[dict[str, object]]] = {match: [] for match in TARGET_MATCHES}

    for row in quotes:
        mapped = gpo.matchup_zh_for_event(row["event"])
        if not mapped:
            continue

        home_en, away_en, matchup = mapped
        if matchup not in events_by_match:
            continue

        outcomes = row["outcomes"].split("|") if row["outcomes"] else []
        prices = row["prices"].split("|") if row["prices"] else []
        variant = gpo.variant_of(row["event"])

        for outcome_index in gpo.selected_outcomes(variant, outcomes):
            outcome = outcomes[outcome_index] if outcome_index < len(outcomes) else ""
            raw_price = prices[outcome_index].strip() if outcome_index < len(prices) else ""
            if not raw_price:
                continue

            market_type, event_name = gpo.type_event_for(row, outcome, home_en, away_en)
            if not included_type(market_type):
                continue

            event = EventSpec(market_type, event_name, row["market"].strip())
            if not event_has_support(matchup, event):
                continue

            price = float(raw_price)
            volume = float(row.get("volume24h", "0") or 0)
            events_by_match[matchup].append(
                {
                    "market_type": market_type,
                    "event_name": event_name,
                    "raw_market": event.raw_market,
                    "q": price,
                    "weight": volume * price,
                }
            )

    return events_by_match


def normalize_weights(weights: np.ndarray) -> np.ndarray:
    positive = weights > 0
    if not positive.any():
        return np.ones_like(weights)
    return weights / weights[positive].mean()


def event_specs(events: list[dict[str, object]]) -> list[EventSpec]:
    return [
        EventSpec(
            str(event["market_type"]),
            str(event["event_name"]),
            str(event.get("raw_market", "")),
        )
        for event in events
    ]


def event_matrix(
    matchup: str,
    specs: list[EventSpec],
    home_lambda: float,
    away_lambda: float,
) -> np.ndarray:
    return np.vstack(
        [
            conditional_values(matchup, event, home_lambda, away_lambda)
            for event in specs
        ]
    )


def fit_poisson_prior(matchup: str, events: list[dict[str, object]]) -> tuple[float, float]:
    specs = event_specs(events)
    targets = np.array([event["q"] for event in events], dtype=float)
    weights = normalize_weights(np.array([event["weight"] for event in events], dtype=float))

    def objective(theta: np.ndarray) -> float:
        home_lambda, away_lambda = np.exp(theta)
        prior = state_prior(home_lambda, away_lambda)
        masks = event_matrix(matchup, specs, home_lambda, away_lambda)
        residual = masks @ prior - targets
        return float(np.sum(weights * residual * residual))

    result = minimize(
        objective,
        x0=np.log([1.4, 1.1]),
        method="Nelder-Mead",
        options={"maxiter": 2000},
    )
    return tuple(np.exp(result.x))


def solve_match(matchup: str, events: list[dict[str, object]]) -> dict[str, object]:
    specs = event_specs(events)
    targets = np.array([event["q"] for event in events], dtype=float)
    weights = normalize_weights(np.array([event["weight"] for event in events], dtype=float))

    home_lambda, away_lambda = fit_poisson_prior(matchup, events)
    prior = state_prior(home_lambda, away_lambda)
    masks = event_matrix(matchup, specs, home_lambda, away_lambda)

    def objective(x: np.ndarray) -> float:
        residual = masks @ x - targets
        regularizer = np.sum((x - prior) ** 2 / (prior + 1e-9))
        return float(np.sum(weights * residual * residual) + RIDGE * regularizer)

    result = minimize(
        objective,
        x0=prior,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * len(STATE_LABELS),
        constraints={"type": "eq", "fun": lambda x: np.sum(x) - 1.0},
        options={"ftol": 1e-12, "maxiter": 2000},
    )
    if not result.success:
        raise RuntimeError(result.message)

    probabilities = result.x
    return {
        "probabilities": probabilities,
        "matrix": probabilities,
        "state_labels": STATE_LABELS,
        "home_lambda": home_lambda,
        "away_lambda": away_lambda,
        "loss": result.fun,
        "event_count": len(events),
        "matchup": matchup,
    }


def score_probability(result: dict[str, object], market_type: str, event_name: str) -> float:
    event = EventSpec(market_type, event_name)
    values = conditional_values(
        str(result["matchup"]),
        event,
        float(result["home_lambda"]),
        float(result["away_lambda"]),
    )
    return float(np.asarray(result["probabilities"]) @ values)


def joint_probability(
    result: dict[str, object],
    left_market_type: str,
    left_event_name: str,
    right_market_type: str,
    right_event_name: str,
) -> float:
    values = conditional_joint_values(
        str(result["matchup"]),
        EventSpec(left_market_type, left_event_name),
        EventSpec(right_market_type, right_event_name),
        float(result["home_lambda"]),
        float(result["away_lambda"]),
    )
    return float(np.asarray(result["probabilities"]) @ values)


def state_probability_table(probabilities: np.ndarray) -> str:
    rows = [
        "| 有限比分状态 | 概率 |",
        "|---|---:|",
    ]
    for label, probability in zip(STATE_LABELS, probabilities):
        rows.append(f"| {label} | {probability * 100:.2f}% |")
    return "\n".join(rows)


def main() -> None:
    if not TARGET_MATCHES:
        raise RuntimeError(f"未找到目标日期 {TARGET_DATE} 的体彩比赛")

    events_by_match = collect_events()
    for matchup in TARGET_MATCHES:
        if not events_by_match[matchup]:
            raise RuntimeError(f"未找到 {matchup} 的 Polymarket 可建模事件")
        result = solve_match(matchup, events_by_match[matchup])
        print(f"### {matchup}")
        print()
        print(
            f"纳入事件数：{result['event_count']}；有限比分状态数：{len(STATE_LABELS)}；"
            f"Poisson先验lambda：主队 {result['home_lambda']:.3f}，客队 {result['away_lambda']:.3f}；"
            f"目标函数值：{result['loss']:.6f}。"
        )
        print()
        print(state_probability_table(result["probabilities"]))
        print()


if __name__ == "__main__":
    main()
