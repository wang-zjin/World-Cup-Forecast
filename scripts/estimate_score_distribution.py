#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson


ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT.parent / "world-cup-2026-arbitrage-trading-bot-main"
SPORTTERY_ODDS = ROOT / "2026世界杯体彩赔率.md"
BEIJING_TZ = timezone(timedelta(hours=8))
GMAX = 10
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
GOALS = tuple(range(GMAX + 1))
SCORES = tuple((home, away) for home in GOALS for away in GOALS)


def event_mask(matchup: str, market_type: str, event_name: str, raw_market: str) -> np.ndarray:
    home_team, away_team = matchup.split(" vs ")
    mask: list[bool] = []

    for home_goals, away_goals in SCORES:
        if market_type == "胜负":
            hit = (
                (event_name == "胜" and home_goals > away_goals)
                or (event_name == "平" and home_goals == away_goals)
                or (event_name == "负" and home_goals < away_goals)
            )
        elif market_type == "比分":
            score = re.match(r"^(\d+):(\d+)$", event_name)
            hit = bool(
                score
                and home_goals == int(score.group(1))
                and away_goals == int(score.group(2))
            )
        elif market_type.startswith("全场大小球"):
            line = float(re.search(r"（([0-9.]+)）", market_type).group(1))
            hit = home_goals + away_goals > line if event_name == "大" else home_goals + away_goals < line
        elif market_type == "双方进球":
            hit = (home_goals > 0 and away_goals > 0) if event_name == "是" else (home_goals == 0 or away_goals == 0)
        elif market_type.startswith("让负"):
            spread = re.match(r"^(.+?)\s+\((-?[0-9.]+)\)$", raw_market)
            if not spread:
                hit = False
            else:
                spread_team = gpo.team_zh(spread.group(1))
                threshold = int(abs(float(spread.group(2))) + 0.5)
                diff = home_goals - away_goals if spread_team == home_team else away_goals - home_goals
                hit = diff >= threshold if event_name == spread_team else diff <= threshold - 1
        else:
            hit = False
        mask.append(hit)

    return np.array(mask, dtype=float)


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

            price = float(raw_price)
            volume = float(row.get("volume24h", "0") or 0)
            mask = event_mask(matchup, market_type, event_name, row["market"].strip())
            if mask.sum() == 0:
                continue

            events_by_match[matchup].append(
                {
                    "market_type": market_type,
                    "event_name": event_name,
                    "q": price,
                    "weight": volume * price,
                    "mask": mask,
                }
            )

    return events_by_match


def normalize_weights(weights: np.ndarray) -> np.ndarray:
    positive = weights > 0
    if not positive.any():
        return np.ones_like(weights)
    return weights / weights[positive].mean()


def poisson_prior(home_lambda: float, away_lambda: float) -> np.ndarray:
    home = np.array([poisson.pmf(goal, home_lambda) for goal in GOALS])
    away = np.array([poisson.pmf(goal, away_lambda) for goal in GOALS])
    prior = np.outer(home, away).reshape(-1)
    return prior / prior.sum()


def fit_poisson_prior(masks: np.ndarray, targets: np.ndarray, weights: np.ndarray) -> tuple[float, float]:
    def objective(theta: np.ndarray) -> float:
        home_lambda, away_lambda = np.exp(theta)
        prior = poisson_prior(home_lambda, away_lambda)
        residual = masks @ prior - targets
        return float(np.sum(weights * residual * residual))

    result = minimize(
        objective,
        x0=np.log([1.4, 1.1]),
        method="Nelder-Mead",
        options={"maxiter": 2000},
    )
    return tuple(np.exp(result.x))


def solve_match(events: list[dict[str, object]]) -> dict[str, object]:
    masks = np.vstack([event["mask"] for event in events])
    targets = np.array([event["q"] for event in events], dtype=float)
    weights = normalize_weights(np.array([event["weight"] for event in events], dtype=float))

    home_lambda, away_lambda = fit_poisson_prior(masks, targets, weights)
    prior = poisson_prior(home_lambda, away_lambda)

    def objective(x: np.ndarray) -> float:
        residual = masks @ x - targets
        regularizer = np.sum((x - prior) ** 2 / (prior + 1e-9))
        return float(np.sum(weights * residual * residual) + RIDGE * regularizer)

    result = minimize(
        objective,
        x0=prior,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * len(SCORES),
        constraints={"type": "eq", "fun": lambda x: np.sum(x) - 1.0},
        options={"ftol": 1e-12, "maxiter": 2000},
    )
    if not result.success:
        raise RuntimeError(result.message)

    return {
        "matrix": result.x.reshape((GMAX + 1, GMAX + 1)),
        "home_lambda": home_lambda,
        "away_lambda": away_lambda,
        "loss": result.fun,
        "event_count": len(events),
    }


def markdown_matrix(matrix: np.ndarray) -> str:
    header = "| 主队进球数 \\ 客队进球数 | " + " | ".join(str(goal) for goal in GOALS) + " |"
    separator = "|---:" + "|---:" * len(GOALS) + "|"
    rows = [header, separator]
    for home_goals in GOALS:
        values = " | ".join(f"{matrix[home_goals, away_goals] * 100:.2f}%" for away_goals in GOALS)
        rows.append(f"| {home_goals} | {values} |")
    return "\n".join(rows)


def main() -> None:
    if not TARGET_MATCHES:
        raise RuntimeError(f"未找到目标日期 {TARGET_DATE} 的体彩比赛")

    events_by_match = collect_events()
    for matchup in TARGET_MATCHES:
        if not events_by_match[matchup]:
            raise RuntimeError(f"未找到 {matchup} 的 Polymarket 可建模事件")
        result = solve_match(events_by_match[matchup])
        print(f"### {matchup}")
        print()
        print(
            f"纳入事件数：{result['event_count']}；"
            f"Poisson先验lambda：主队 {result['home_lambda']:.3f}，客队 {result['away_lambda']:.3f}；"
            f"目标函数值：{result['loss']:.6f}。"
        )
        print()
        print(markdown_matrix(result["matrix"]))
        print()


if __name__ == "__main__":
    main()
