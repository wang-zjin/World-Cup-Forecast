#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPORTTERY_ODDS = ROOT / "2026世界杯体彩赔率.md"
EXACT_HOME_WINS = {
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
}
EXACT_DRAWS = {"0:0", "1:1", "2:2", "3:3"}
EXACT_AWAY_WINS = {
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
}


def load_estimator():
    path = ROOT / "scripts" / "estimate_score_distribution.py"
    spec = importlib.util.spec_from_file_location("estimate_score_distribution", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["estimate_score_distribution"] = module
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


def score_mask(matrix: np.ndarray, market_type: str, event_name: str) -> np.ndarray:
    mask = np.zeros(matrix.shape, dtype=bool)
    for home_goals in range(matrix.shape[0]):
        for away_goals in range(matrix.shape[1]):
            score = f"{home_goals}:{away_goals}"

            if market_type == "胜负":
                hit = (
                    (event_name == "胜" and home_goals > away_goals)
                    or (event_name == "平" and home_goals == away_goals)
                    or (event_name == "负" and home_goals < away_goals)
                )
            elif market_type.startswith("让负"):
                handicap = int(re.search(r"（([+-]?\d+)）", market_type).group(1))
                adjusted = home_goals + handicap - away_goals
                hit = (
                    (event_name == "胜" and adjusted > 0)
                    or (event_name == "平" and adjusted == 0)
                    or (event_name == "负" and adjusted < 0)
                )
            elif market_type == "比分":
                if re.match(r"^\d+:\d+$", event_name):
                    hit = score == event_name
                elif event_name == "胜其他":
                    hit = home_goals > away_goals and score not in EXACT_HOME_WINS
                elif event_name == "平其他":
                    hit = home_goals == away_goals and score not in EXACT_DRAWS
                elif event_name == "负其他":
                    hit = home_goals < away_goals and score not in EXACT_AWAY_WINS
                else:
                    hit = False
            else:
                hit = False

            if hit:
                mask[home_goals, away_goals] = True
    return mask


def score_probability(matrix: np.ndarray, market_type: str, event_name: str) -> float:
    return float(matrix[score_mask(matrix, market_type, event_name)].sum())


def format_percent(value: float, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def main() -> None:
    estimator = load_estimator()
    events_by_match = estimator.collect_events()
    matrices = {
        matchup: estimator.solve_match(events_by_match[matchup])["matrix"]
        for matchup in estimator.TARGET_MATCHES
    }

    rows = []
    for row in load_sporttery_rows(estimator.TARGET_MATCHES):
        probability = score_probability(
            matrices[row["matchup"]],
            row["market_type"],
            row["event_name"],
        )
        expected_return = probability * row["odds"] - 1
        rows.append({**row, "probability": probability, "expected_return": expected_return})

    rows.sort(key=lambda item: item["expected_return"], reverse=True)

    print("| 比赛 | 玩法 | 事件 | 体彩赔率 | Polymarket 估计概率 | 期望收益 |")
    print("|---|---|---|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row['matchup']} | {row['market_type']} | {row['event_name']} | "
            f"{row['odds']:.2f} | {format_percent(row['probability'])} | "
            f"{format_percent(row['expected_return'], signed=True)} |"
        )


if __name__ == "__main__":
    main()
