#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT.parent / "world-cup-2026-arbitrage-trading-bot-main"
STRATEGY_DIR = ROOT / "下注策略"
SCHEDULE = ROOT / "2026世界杯赛程_北京时间.md"
ESTIMATOR = ROOT / "scripts" / "estimate_score_distribution.py"


def markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def target_date_from_strategy(path: Path) -> str:
    match = re.fullmatch(r"(\d{2})-(\d{2})下注策略\.md", path.name)
    if not match:
        raise ValueError(f"cannot infer target date from {path.name}")
    return f"2026-{match.group(1)}-{match.group(2)}"


def strategy_csv(path: Path) -> Path | None:
    text = path.read_text(encoding="utf-8")

    for line in text.splitlines():
        if "策略二增量信息比分分布" not in line:
            continue
        match = re.search(r"`([^`]*match-quotes/[^`]+\.csv)`", line)
        if match:
            return (ROOT / match.group(1)).resolve()

    # Early files may describe the source inline instead of in the data-source table.
    match = re.search(r"match-quotes/([^`\s]+\.csv)", text)
    if match:
        return BOT_ROOT / "data" / "match-quotes" / match.group(1)
    return None


def load_scores() -> dict[str, dict[str, Any]]:
    scores: dict[str, dict[str, Any]] = {}
    for line in SCHEDULE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| 场次") or line.startswith("|---"):
            continue
        cells = markdown_cells(line)
        if len(cells) < 6:
            continue
        date_text, matchup, score_text = cells[2], cells[4], cells[5]
        score = re.fullmatch(r"(\d+)[–-](\d+)", score_text)
        date = re.search(r"\d{4}-\d{2}-\d{2}", date_text)
        if not score or not date:
            continue
        scores[matchup] = {
            "date": date.group(0),
            "home_goals": int(score.group(1)),
            "away_goals": int(score.group(2)),
        }
    return scores


def load_estimator(target_date: str, csv_path: Path, index: int):
    os.environ["WORLDCUP_TARGET_DATE"] = target_date
    os.environ["WORLDCUP_POLYMARKET_CSV"] = str(csv_path)
    spec = importlib.util.spec_from_file_location(f"estimate_score_distribution_{index}", ESTIMATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"estimate_score_distribution_{index}"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def result_label(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "H"
    if home_goals == away_goals:
        return "D"
    return "A"


def poisson_pmf(goals: int, rate: float) -> float:
    return math.exp(-rate) * rate**goals / math.factorial(goals)


def poisson_draw_probability(home_rate: float, away_rate: float, max_goals: int = 30) -> float:
    return sum(
        poisson_pmf(goals, home_rate) * poisson_pmf(goals, away_rate)
        for goals in range(max_goals + 1)
    )


def pearson(xs: list[float], ys: list[float]) -> float:
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return float("nan")
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / math.sqrt(var_x * var_y)


def mae(xs: list[float], ys: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(xs, ys)) / len(xs)


def rmse(xs: list[float], ys: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(xs, ys)) / len(xs))


def binary_auc(probabilities: list[float], outcomes: list[int]) -> float:
    pairs = 0
    score = 0.0
    for probability, outcome in zip(probabilities, outcomes):
        if outcome != 1:
            continue
        for other_probability, other_outcome in zip(probabilities, outcomes):
            if other_outcome != 0:
                continue
            pairs += 1
            if probability > other_probability:
                score += 1.0
            elif probability == other_probability:
                score += 0.5
    return score / pairs if pairs else float("nan")


def compute_records() -> tuple[list[dict[str, Any]], list[str]]:
    scores = load_scores()
    records: list[dict[str, Any]] = []
    errors: list[str] = []

    strategy_paths = sorted(STRATEGY_DIR.glob("06-*.md"))
    for index, path in enumerate(strategy_paths, start=1):
        target_date = target_date_from_strategy(path)
        csv_path = strategy_csv(path)
        if csv_path is None:
            errors.append(f"{path.name}: no strategy-2 match-quotes CSV found")
            continue
        if not csv_path.exists():
            errors.append(f"{path.name}: CSV not found: {csv_path}")
            continue

        estimator = load_estimator(target_date, csv_path, index)
        events_by_match = estimator.collect_events()
        goals = np.arange(estimator.GMAX + 1)

        for matchup in estimator.TARGET_MATCHES:
            actual = scores.get(matchup)
            if actual is None:
                continue
            events = events_by_match.get(matchup, [])
            if not events:
                errors.append(f"{path.name}: no Polymarket events for {matchup}")
                continue

            result = estimator.solve_match(events)
            matrix = result["matrix"]
            posterior_home = float(matrix.sum(axis=1) @ goals)
            posterior_away = float(matrix.sum(axis=0) @ goals)
            mode_home, mode_away = np.unravel_index(int(np.argmax(matrix)), matrix.shape)

            home_goals = actual["home_goals"]
            away_goals = actual["away_goals"]
            actual_score_probability = (
                float(matrix[home_goals, away_goals])
                if home_goals <= estimator.GMAX and away_goals <= estimator.GMAX
                else 0.0
            )
            result_probabilities = {
                "H": float(np.tril(matrix, -1).sum()),
                "D": float(np.trace(matrix)),
                "A": float(np.triu(matrix, 1).sum()),
            }
            predicted_result = max(result_probabilities.items(), key=lambda item: item[1])[0]

            records.append(
                {
                    "date": target_date,
                    "strategy_file": path.name,
                    "csv": csv_path.name,
                    "matchup": matchup,
                    "events": int(result["event_count"]),
                    "prior_home": float(result["home_lambda"]),
                    "prior_away": float(result["away_lambda"]),
                    "posterior_home": posterior_home,
                    "posterior_away": posterior_away,
                    "posterior_total": posterior_home + posterior_away,
                    "posterior_diff": posterior_home - posterior_away,
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "actual_total": home_goals + away_goals,
                    "actual_diff": home_goals - away_goals,
                    "actual_result": result_label(home_goals, away_goals),
                    "predicted_result": predicted_result,
                    "mode_home": int(mode_home),
                    "mode_away": int(mode_away),
                    "mode_probability": float(matrix[mode_home, mode_away]),
                    "actual_score_probability": actual_score_probability,
                    "prior_draw_probability": poisson_draw_probability(
                        float(result["home_lambda"]),
                        float(result["away_lambda"]),
                    ),
                    "posterior_draw_probability": result_probabilities["D"],
                    "posterior_home_win_probability": result_probabilities["H"],
                    "posterior_away_win_probability": result_probabilities["A"],
                    "loss": float(result["loss"]),
                }
            )

    return records, errors


def metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    all_posterior: list[float] = []
    all_actual: list[float] = []
    all_prior: list[float] = []
    for record in records:
        all_posterior.extend([record["posterior_home"], record["posterior_away"]])
        all_actual.extend([record["home_goals"], record["away_goals"]])
        all_prior.extend([record["prior_home"], record["prior_away"]])

    posterior_total = [record["posterior_total"] for record in records]
    actual_total = [record["actual_total"] for record in records]
    posterior_diff = [record["posterior_diff"] for record in records]
    actual_diff = [record["actual_diff"] for record in records]
    prior_total = [record["prior_home"] + record["prior_away"] for record in records]
    prior_diff = [record["prior_home"] - record["prior_away"] for record in records]
    draw_outcomes = [1 if record["actual_result"] == "D" else 0 for record in records]
    prior_draw_probabilities = [record["prior_draw_probability"] for record in records]
    posterior_draw_probabilities = [record["posterior_draw_probability"] for record in records]
    actual_draw_rate = sum(draw_outcomes) / len(draw_outcomes)

    return {
        "n": len(records),
        "posterior_sum_home": sum(record["posterior_home"] for record in records),
        "actual_sum_home": sum(record["home_goals"] for record in records),
        "posterior_sum_away": sum(record["posterior_away"] for record in records),
        "actual_sum_away": sum(record["away_goals"] for record in records),
        "posterior_sum_total": sum(posterior_total),
        "actual_sum_total": sum(actual_total),
        "posterior_corr_all_team": pearson(all_posterior, all_actual),
        "posterior_corr_total": pearson(posterior_total, actual_total),
        "posterior_corr_diff": pearson(posterior_diff, actual_diff),
        "posterior_mae_team": mae(all_posterior, all_actual),
        "posterior_rmse_team": rmse(all_posterior, all_actual),
        "posterior_mae_total": mae(posterior_total, actual_total),
        "posterior_rmse_total": rmse(posterior_total, actual_total),
        "posterior_mae_diff": mae(posterior_diff, actual_diff),
        "posterior_rmse_diff": rmse(posterior_diff, actual_diff),
        "posterior_result_hits": sum(
            record["predicted_result"] == record["actual_result"] for record in records
        ),
        "posterior_exact_mode_hits": sum(
            (record["mode_home"], record["mode_away"])
            == (record["home_goals"], record["away_goals"])
            for record in records
        ),
        "posterior_avg_actual_score_probability": sum(
            record["actual_score_probability"] for record in records
        )
        / len(records),
        "posterior_avg_negative_log_probability": -sum(
            math.log(max(record["actual_score_probability"], 1e-15)) for record in records
        )
        / len(records),
        "prior_corr_all_team": pearson(all_prior, all_actual),
        "prior_corr_total": pearson(prior_total, actual_total),
        "prior_corr_diff": pearson(prior_diff, actual_diff),
        "prior_mae_team": mae(all_prior, all_actual),
        "prior_mae_total": mae(prior_total, actual_total),
        "prior_mae_diff": mae(prior_diff, actual_diff),
        "actual_draws": sum(draw_outcomes),
        "actual_draw_rate": actual_draw_rate,
        "prior_draw_mean": sum(prior_draw_probabilities) / len(prior_draw_probabilities),
        "posterior_draw_mean": sum(posterior_draw_probabilities)
        / len(posterior_draw_probabilities),
        "prior_draw_mean_when_draw": sum(
            probability
            for probability, outcome in zip(prior_draw_probabilities, draw_outcomes)
            if outcome
        )
        / sum(draw_outcomes),
        "prior_draw_mean_when_not_draw": sum(
            probability
            for probability, outcome in zip(prior_draw_probabilities, draw_outcomes)
            if not outcome
        )
        / (len(draw_outcomes) - sum(draw_outcomes)),
        "prior_draw_brier": sum(
            (probability - outcome) ** 2
            for probability, outcome in zip(prior_draw_probabilities, draw_outcomes)
        )
        / len(draw_outcomes),
        "posterior_draw_brier": sum(
            (probability - outcome) ** 2
            for probability, outcome in zip(posterior_draw_probabilities, draw_outcomes)
        )
        / len(draw_outcomes),
        "base_draw_brier": sum((actual_draw_rate - outcome) ** 2 for outcome in draw_outcomes)
        / len(draw_outcomes),
        "prior_draw_auc": binary_auc(prior_draw_probabilities, draw_outcomes),
        "posterior_draw_auc": binary_auc(posterior_draw_probabilities, draw_outcomes),
    }


def draw_threshold_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for threshold in (0.18, 0.20, 0.22, 0.24, 0.26, 0.28, 0.30):
        predictions = [
            1 if record["prior_draw_probability"] >= threshold else 0
            for record in records
        ]
        outcomes = [1 if record["actual_result"] == "D" else 0 for record in records]
        true_positive = sum(1 for prediction, outcome in zip(predictions, outcomes) if prediction and outcome)
        false_positive = sum(1 for prediction, outcome in zip(predictions, outcomes) if prediction and not outcome)
        false_negative = sum(1 for prediction, outcome in zip(predictions, outcomes) if not prediction and outcome)
        true_negative = sum(1 for prediction, outcome in zip(predictions, outcomes) if not prediction and not outcome)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append(
            {
                "threshold": threshold,
                "predicted_draws": true_positive + false_positive,
                "true_positive": true_positive,
                "false_positive": false_positive,
                "false_negative": false_negative,
                "true_negative": true_negative,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )
    return rows


def draw_probability_bins(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bins = (0.00, 0.12, 0.16, 0.20, 0.24, 0.28, 0.32, 1.00)
    rows: list[dict[str, Any]] = []
    for lower, upper in zip(bins, bins[1:]):
        matches = [
            record
            for record in records
            if lower <= record["prior_draw_probability"] < upper
        ]
        if not matches:
            continue
        draws = sum(record["actual_result"] == "D" for record in matches)
        rows.append(
            {
                "lower": lower,
                "upper": upper,
                "matches": len(matches),
                "draws": draws,
                "draw_rate": draws / len(matches),
                "average_probability": sum(
                    record["prior_draw_probability"] for record in matches
                )
                / len(matches),
            }
        )
    return rows


def print_report(records: list[dict[str, Any]], errors: list[str]) -> None:
    stats = metrics(records)
    n = stats["n"]
    print("# 策略二后验 lambda 回测")
    print()
    print("后验 lambda 定义为最终比分矩阵的边际期望：")
    print()
    print("```text")
    print("lambda_home_post = sum_{h,a} h * P(h:a)")
    print("lambda_away_post = sum_{h,a} a * P(h:a)")
    print("```")
    print()
    if errors:
        print("## 运行提示")
        print()
        for error in errors:
            print(f"- {error}")
        print()

    print("## 汇总指标")
    print()
    print("| 指标 | 数值 |")
    print("|---|---:|")
    print(f"| 样本场次 | {n} |")
    print(f"| 后验主队总 lambda / 实际主队进球 | {stats['posterior_sum_home']:.2f} / {stats['actual_sum_home']} |")
    print(f"| 后验客队总 lambda / 实际客队进球 | {stats['posterior_sum_away']:.2f} / {stats['actual_sum_away']} |")
    print(f"| 后验总 lambda / 实际总进球 | {stats['posterior_sum_total']:.2f} / {stats['actual_sum_total']} |")
    print(f"| 单队后验 lambda 与实际进球相关系数 | {stats['posterior_corr_all_team']:.3f} |")
    print(f"| 后验总 lambda 与实际总进球相关系数 | {stats['posterior_corr_total']:.3f} |")
    print(f"| 后验净胜球 lambda 与实际净胜球相关系数 | {stats['posterior_corr_diff']:.3f} |")
    print(f"| 单队进球 MAE / RMSE | {stats['posterior_mae_team']:.3f} / {stats['posterior_rmse_team']:.3f} |")
    print(f"| 总进球 MAE / RMSE | {stats['posterior_mae_total']:.3f} / {stats['posterior_rmse_total']:.3f} |")
    print(f"| 净胜球 MAE / RMSE | {stats['posterior_mae_diff']:.3f} / {stats['posterior_rmse_diff']:.3f} |")
    print(
        f"| 胜平负方向命中 | {stats['posterior_result_hits']}/{n} = "
        f"{stats['posterior_result_hits'] / n:.1%} |"
    )
    print(
        f"| 矩阵最高概率精确比分命中 | {stats['posterior_exact_mode_hits']}/{n} = "
        f"{stats['posterior_exact_mode_hits'] / n:.1%} |"
    )
    print(f"| 实际比分平均矩阵概率 | {stats['posterior_avg_actual_score_probability']:.2%} |")
    print(f"| 实际比分平均负对数概率 | {stats['posterior_avg_negative_log_probability']:.3f} |")
    print()

    print("## 平局概率诊断")
    print()
    print("| 指标 | 数值 |")
    print("|---|---:|")
    print(f"| 实际平局 | {stats['actual_draws']}/{n} = {stats['actual_draw_rate']:.1%} |")
    print(f"| 先验 Poisson 平局概率均值 | {stats['prior_draw_mean']:.1%} |")
    print(f"| 后验矩阵平局概率均值 | {stats['posterior_draw_mean']:.1%} |")
    print(f"| 实际平局场次的平均先验平局概率 | {stats['prior_draw_mean_when_draw']:.1%} |")
    print(f"| 非平局场次的平均先验平局概率 | {stats['prior_draw_mean_when_not_draw']:.1%} |")
    print(f"| 先验平局概率 AUC | {stats['prior_draw_auc']:.3f} |")
    print(f"| 后验平局概率 AUC | {stats['posterior_draw_auc']:.3f} |")
    print(f"| 先验平局概率 Brier / 基准 Brier | {stats['prior_draw_brier']:.3f} / {stats['base_draw_brier']:.3f} |")
    print(f"| 后验平局概率 Brier / 基准 Brier | {stats['posterior_draw_brier']:.3f} / {stats['base_draw_brier']:.3f} |")
    print()

    print("### 平局概率分桶")
    print()
    print("| 先验平局概率 | 场次 | 实际平局 | 实际平局率 | 平均概率 |")
    print("|---|---:|---:|---:|---:|")
    for row in draw_probability_bins(records):
        print(
            f"| {row['lower']:.0%}-{row['upper']:.0%} | {row['matches']} | "
            f"{row['draws']} | {row['draw_rate']:.1%} | {row['average_probability']:.1%} |"
        )
    print()

    print("### 平局阈值测试")
    print()
    print("| 阈值 | 预测平局 | TP | FP | FN | TN | Precision | Recall | F1 |")
    print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in draw_threshold_rows(records):
        print(
            f"| {row['threshold']:.0%} | {row['predicted_draws']} | "
            f"{row['true_positive']} | {row['false_positive']} | "
            f"{row['false_negative']} | {row['true_negative']} | "
            f"{row['precision']:.1%} | {row['recall']:.1%} | {row['f1']:.2f} |"
        )
    print()

    print("## 先验对照")
    print()
    print("| 指标 | 先验 lambda | 后验 lambda |")
    print("|---|---:|---:|")
    print(f"| 单队进球相关系数 | {stats['prior_corr_all_team']:.3f} | {stats['posterior_corr_all_team']:.3f} |")
    print(f"| 总进球相关系数 | {stats['prior_corr_total']:.3f} | {stats['posterior_corr_total']:.3f} |")
    print(f"| 净胜球相关系数 | {stats['prior_corr_diff']:.3f} | {stats['posterior_corr_diff']:.3f} |")
    print(f"| 单队进球 MAE | {stats['prior_mae_team']:.3f} | {stats['posterior_mae_team']:.3f} |")
    print(f"| 总进球 MAE | {stats['prior_mae_total']:.3f} | {stats['posterior_mae_total']:.3f} |")
    print(f"| 净胜球 MAE | {stats['prior_mae_diff']:.3f} | {stats['posterior_mae_diff']:.3f} |")
    print()

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_day[record["date"]].append(record)

    print("## 按日摘要")
    print()
    print("| 日期 | 场次 | 后验总 lambda | 实际总进球 | 胜平负命中 | 精确比分 mode 命中 | 单队 MAE | 总进球 MAE |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for date in sorted(by_day):
        rows = by_day[date]
        daily_posterior: list[float] = []
        daily_actual: list[float] = []
        for row in rows:
            daily_posterior.extend([row["posterior_home"], row["posterior_away"]])
            daily_actual.extend([row["home_goals"], row["away_goals"]])
        result_hits = sum(row["predicted_result"] == row["actual_result"] for row in rows)
        exact_hits = sum(
            (row["mode_home"], row["mode_away"]) == (row["home_goals"], row["away_goals"])
            for row in rows
        )
        print(
            f"| {date} | {len(rows)} | {sum(row['posterior_total'] for row in rows):.2f} | "
            f"{sum(row['actual_total'] for row in rows)} | {result_hits} | {exact_hits} | "
            f"{mae(daily_posterior, daily_actual):.2f} | "
            f"{mae([row['posterior_total'] for row in rows], [row['actual_total'] for row in rows]):.2f} |"
        )
    print()

    print("## 逐场后验 lambda")
    print()
    print("| 日期 | 比赛 | 先验 lambda | 后验 lambda | 实际比分 | 后验总球 | 实际总球 | 预测方向 | 实际方向 | mode 比分 | 实际比分概率 |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in records:
        print(
            f"| {row['date']} | {row['matchup']} | "
            f"{row['prior_home']:.3f}-{row['prior_away']:.3f} | "
            f"{row['posterior_home']:.3f}-{row['posterior_away']:.3f} | "
            f"{row['home_goals']}-{row['away_goals']} | "
            f"{row['posterior_total']:.3f} | {row['actual_total']} | "
            f"{row['predicted_result']} | {row['actual_result']} | "
            f"{row['mode_home']}:{row['mode_away']} | "
            f"{row['actual_score_probability']:.2%} |"
        )
    print()

    print("## 最大总进球误差")
    print()
    print("| 日期 | 比赛 | 后验 lambda | 实际比分 | 后验总球 | 实际总球 | mode 比分 | 实际比分概率 |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for row in sorted(records, key=lambda item: abs(item["posterior_total"] - item["actual_total"]), reverse=True)[:10]:
        print(
            f"| {row['date']} | {row['matchup']} | "
            f"{row['posterior_home']:.3f}-{row['posterior_away']:.3f} | "
            f"{row['home_goals']}-{row['away_goals']} | "
            f"{row['posterior_total']:.3f} | {row['actual_total']} | "
            f"{row['mode_home']}:{row['mode_away']} | {row['actual_score_probability']:.2%} |"
        )


def main() -> None:
    records, errors = compute_records()
    if not records:
        raise RuntimeError("no completed strategy-2 records found")
    print_report(records, errors)


if __name__ == "__main__":
    main()
