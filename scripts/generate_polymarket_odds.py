#!/usr/bin/env python3
from __future__ import annotations

import csv
import argparse
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT.parent / "world-cup-2026-arbitrage-trading-bot-main"
SOURCE_ODDS = ROOT / "2026世界杯体彩赔率.md"
OUTPUT = ROOT / "2026世界杯polymarket赔率.md"
BEIJING_TZ = timezone(timedelta(hours=8))

KNOWN_SUFFIXES = (
    "More Markets",
    "Exact Score",
    "Total Corners",
    "Halftime Result",
    "First Team to Score",
    "Player Props",
    "Second Half Result",
)

VARIANT_ORDER = {
    "Main 1X2": 0,
    "More Markets": 1,
    "Exact Score": 2,
    "Halftime Result": 3,
    "Second Half Result": 4,
    "First Team to Score": 5,
    "Total Corners": 6,
    "Player Props": 7,
}

TEAM_EN_TO_ZH = {
    "Algeria": "阿尔及利亚",
    "Argentina": "阿根廷",
    "Australia": "澳大利亚",
    "Austria": "奥地利",
    "Belgium": "比利时",
    "Bosnia and Herzegovina": "波黑",
    "Bosnia & Herzegovina": "波黑",
    "Bosnia-Herzegovina": "波黑",
    "Brazil": "巴西",
    "Cabo Verde": "佛得角",
    "Cape Verde": "佛得角",
    "Canada": "加拿大",
    "Colombia": "哥伦比亚",
    "Côte d'Ivoire": "科特迪瓦",
    "Cote d'Ivoire": "科特迪瓦",
    "Croatia": "克罗地亚",
    "Curaçao": "库拉索",
    "Curacao": "库拉索",
    "Czech Republic": "捷克",
    "Czechia": "捷克",
    "DR Congo": "刚果民主共和国",
    "Democratic Republic of the Congo": "刚果民主共和国",
    "Ecuador": "厄瓜多尔",
    "Egypt": "埃及",
    "England": "英格兰",
    "France": "法国",
    "Germany": "德国",
    "Ghana": "加纳",
    "Haiti": "海地",
    "IR Iran": "伊朗",
    "Iran": "伊朗",
    "Iraq": "伊拉克",
    "Ivory Coast": "科特迪瓦",
    "Japan": "日本",
    "Jordan": "约旦",
    "Korea Republic": "韩国",
    "Mexico": "墨西哥",
    "Morocco": "摩洛哥",
    "Netherlands": "荷兰",
    "New Zealand": "新西兰",
    "Norway": "挪威",
    "Panama": "巴拿马",
    "Paraguay": "巴拉圭",
    "Portugal": "葡萄牙",
    "Qatar": "卡塔尔",
    "Saudi Arabia": "沙特阿拉伯",
    "Scotland": "苏格兰",
    "Senegal": "塞内加尔",
    "South Africa": "南非",
    "South Korea": "韩国",
    "Spain": "西班牙",
    "Sweden": "瑞典",
    "Switzerland": "瑞士",
    "Tunisia": "突尼斯",
    "Turkey": "土耳其",
    "Türkiye": "土耳其",
    "United States": "美国",
    "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦",
}


@dataclass(frozen=True)
class ScheduleEntry:
    index: int
    time_text: str
    matchup_zh: str


@dataclass(frozen=True)
class OutputRow:
    sort_key: tuple[int, int, int, int]
    time_text: str
    matchup_zh: str
    market_type: str
    event_name: str
    price: str
    odds: str
    volume: str


def latest_csv(subdir: str) -> Path:
    files = sorted((BOT_ROOT / "data" / subdir).glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"未找到 Polymarket {subdir} CSV")
    return files[-1]


def markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def load_schedule_from_odds() -> dict[str, ScheduleEntry]:
    schedule: dict[str, ScheduleEntry] = {}
    for line in SOURCE_ODDS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| 时间 ") or line.startswith("|---"):
            continue
        cells = markdown_cells(line)
        if len(cells) < 2:
            continue
        time_text, matchup = cells[0], cells[1]
        if matchup not in schedule:
            schedule[matchup] = ScheduleEntry(len(schedule), time_text, matchup)
    return schedule


def schedule_date(time_text: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", time_text)
    return match.group(1) if match else None


def tomorrow_beijing() -> str:
    return (datetime.now(BEIJING_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")


def variant_of(event_title: str) -> str:
    if " - " not in event_title:
        return "Main 1X2"
    suffix = event_title.rsplit(" - ", 1)[1].strip()
    return suffix if suffix in KNOWN_SUFFIXES else "Main 1X2"


def base_event_title(event_title: str) -> str:
    title = event_title.strip()
    for suffix in KNOWN_SUFFIXES:
        title = re.sub(rf"\s+-\s+{re.escape(suffix)}\s*$", "", title)
    title = re.sub(r"\s+vs\.\s+", " vs ", title, flags=re.I)
    return title.strip()


def split_teams(base_title: str) -> tuple[str, str] | None:
    match = re.match(r"^(.+?)\s+vs\.?\s+(.+?)$", base_title, flags=re.I)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def team_zh(team_en: str) -> str:
    return TEAM_EN_TO_ZH.get(team_en.strip(), team_en.strip())


def matchup_zh_for_event(event_title: str) -> tuple[str, str, str] | None:
    teams = split_teams(base_event_title(event_title))
    if not teams:
        return None
    home_en, away_en = teams
    return home_en, away_en, f"{team_zh(home_en)} vs {team_zh(away_en)}"


def is_tournament_h2h(event_title: str) -> bool:
    return event_title.startswith("World Cup Goals H2H:") or event_title.startswith(
        "World Cup Goal Contributions H2H:"
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def price_to_odds(price: str) -> str:
    try:
        value = float(price)
    except ValueError:
        return "-"
    if value <= 0:
        return "-"
    return f"{1 / value:.2f}"


def price_value(price: str) -> str:
    price = price.strip()
    return price if price else "-"


def outcome_label(outcome: str) -> str:
    labels = {
        "Over": "大",
        "Under": "小",
        "Yes": "是",
        "No": "否",
        "Draw": "平",
        "Neither": "无进球",
        "Odd": "单",
        "Even": "双",
    }
    return labels.get(outcome, team_zh(outcome))


def result_side(market: str, home_en: str, away_en: str) -> str:
    if market == home_en:
        return "胜"
    if market == away_en:
        return "负"
    if market == "Draw" or market.startswith("Draw"):
        return "平"
    return team_zh(market)


def score_event(market: str) -> str:
    if market == "Any Other Score":
        return "其他比分"
    match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", market)
    if match:
        return f"{match.group(1)}:{match.group(2)}"
    return market


def line_from_ou(market: str) -> str | None:
    match = re.search(r"O/U\s+([0-9]+(?:\.[0-9]+)?)", market)
    return match.group(1) if match else None


def classify_more_market(market: str, outcome: str) -> tuple[str, str]:
    label = outcome_label(outcome)

    spread = re.match(r"^(.+?)\s+\(([+-]?\d+(?:\.\d+)?)\)$", market)
    if spread:
        return f"让负（{spread.group(2)}）", label

    if market == "Both Teams to Score":
        return "双方进球", label
    if market == "Both Teams to Score in First Half":
        return "上半场双方进球", label

    match = re.match(r"^(.+?)\s+1st Half O/U\s+([0-9]+(?:\.[0-9]+)?)$", market)
    if match:
        return f"上半场球队进球大小球（{team_zh(match.group(1))} {match.group(2)}）", label

    match = re.match(r"^1st Half O/U\s+([0-9]+(?:\.[0-9]+)?)$", market)
    if match:
        return f"上半场大小球（{match.group(1)}）", label

    match = re.match(r"^2nd Half O/U\s+([0-9]+(?:\.[0-9]+)?)$", market)
    if match:
        return f"下半场大小球（{match.group(1)}）", label

    match = re.match(r"^(.+?)\s+O/U\s+([0-9]+(?:\.[0-9]+)?)$", market)
    if match:
        return f"球队进球大小球（{team_zh(match.group(1))} {match.group(2)}）", label

    match = re.match(r"^O/U\s+([0-9]+(?:\.[0-9]+)?)$", market)
    if match:
        return f"全场大小球（{match.group(1)}）", label

    return "其他玩法", f"{market}-{label}"


def classify_corners_market(market: str, outcome: str) -> tuple[str, str]:
    label = outcome_label(outcome)
    line = line_from_ou(market)
    if "1st Half" in market and line:
        return f"上半场角球大小球（{line}）", label
    if "2nd Half" in market and line:
        return f"下半场角球大小球（{line}）", label
    if "Total Corners" in market and line:
        return f"角球大小球（{line}）", label
    if market == "Team to Take First Corner":
        return "首个角球球队", label
    if market == "Total Corners: Odd or Even":
        return "角球单双", label
    return "角球", f"{market}-{label}"


def selected_outcomes(variant: str, outcomes: list[str]) -> Iterable[int]:
    yes_only_variants = {
        "Main 1X2",
        "Exact Score",
        "Halftime Result",
        "Second Half Result",
        "First Team to Score",
    }
    if variant in yes_only_variants and outcomes == ["Yes", "No"]:
        return [0]
    return range(len(outcomes))


def player_prop_event(market: str, outcome: str) -> str:
    match = re.match(r"^(.+?):\s*(\d+)\+\s+(.+)$", market)
    if not match:
        event = market
    else:
        player, count, stat = match.groups()
        stat_labels = {
            "goals": "进球数",
            "shots": "射门数",
            "shots on target": "射正数",
            "assists": "助攻数",
            "saves": "扑救数",
            "goals + assists": "进球+助攻数",
        }
        event = f"{player}{stat_labels.get(stat, stat)}+{count}"
    if outcome == "No":
        return f"{event}-否"
    return event


def type_event_for(row: dict[str, str], outcome: str, home_en: str, away_en: str) -> tuple[str, str]:
    variant = variant_of(row["event"])
    market = row["market"].strip()

    if variant == "Main 1X2":
        return "胜负", result_side(market, home_en, away_en)
    if variant == "Exact Score":
        return "比分", score_event(market)
    if variant == "Halftime Result":
        return "半场胜负", result_side(market, home_en, away_en)
    if variant == "Second Half Result":
        return "下半场胜负", result_side(market, home_en, away_en)
    if variant == "First Team to Score":
        return "先进球球队", outcome_label(market)
    if variant == "More Markets":
        return classify_more_market(market, outcome)
    if variant == "Total Corners":
        return classify_corners_market(market, outcome)
    if variant == "Player Props":
        return "球员数据", player_prop_event(market, outcome)
    return variant, f"{market}-{outcome_label(outcome)}"


def make_rows(
    quotes: list[dict[str, str]],
    schedule: dict[str, ScheduleEntry],
    target_date: str | None = None,
) -> tuple[list[OutputRow], Counter[str]]:
    rows: list[OutputRow] = []
    stats: Counter[str] = Counter()

    for quote_index, row in enumerate(quotes):
        if is_tournament_h2h(row["event"]):
            stats["skipped_non_match_h2h"] += 1
            continue

        mapped = matchup_zh_for_event(row["event"])
        if not mapped:
            stats["unparsed_event"] += 1
            continue

        home_en, away_en, matchup_zh = mapped
        entry = schedule.get(matchup_zh)
        if not entry:
            stats[f"unmatched:{matchup_zh}"] += 1
            continue
        if target_date and schedule_date(entry.time_text) != target_date:
            stats["skipped_non_target_date"] += 1
            continue

        outcomes = row["outcomes"].split("|") if row["outcomes"] else []
        prices = row["prices"].split("|") if row["prices"] else []
        variant = variant_of(row["event"])
        variant_order = VARIANT_ORDER.get(variant, 99)

        for outcome_index in selected_outcomes(variant, outcomes):
            outcome = outcomes[outcome_index] if outcome_index < len(outcomes) else ""
            raw_price = prices[outcome_index].strip() if outcome_index < len(prices) else ""
            price = price_value(raw_price)
            market_type, event_name = type_event_for(row, outcome, home_en, away_en)
            rows.append(
                OutputRow(
                    sort_key=(entry.index, variant_order, quote_index, outcome_index),
                    time_text=entry.time_text,
                    matchup_zh=entry.matchup_zh,
                    market_type=market_type,
                    event_name=event_name,
                    price=price,
                    odds=price_to_odds(raw_price),
                    volume=row.get("volume24h", "").strip() or "-",
                )
            )
            stats["output_rows"] += 1

    rows.sort(key=lambda item: item.sort_key)
    return rows, stats


def bj_time_from_iso(value: str) -> str | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    bj = dt.astimezone(timezone(timedelta(hours=8)))
    return bj.strftime("%Y-%m-%d %H:%M")


def time_key_from_template(time_text: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2}).*?(\d{2}:\d{2})", time_text)
    if not match:
        return None
    return f"{match.group(1)} {match.group(2)}"


def validate_times(matches: list[dict[str, str]], schedule: dict[str, ScheduleEntry]) -> Counter[str]:
    stats: Counter[str] = Counter()
    seen: set[str] = set()
    for row in matches:
        if is_tournament_h2h(row["eventTitle"]):
            continue

        mapped = matchup_zh_for_event(row["eventTitle"])
        if not mapped:
            continue
        _, _, matchup_zh = mapped
        if matchup_zh in seen:
            continue
        seen.add(matchup_zh)
        entry = schedule.get(matchup_zh)
        if not entry:
            continue
        expected = time_key_from_template(entry.time_text)
        actual = bj_time_from_iso(row.get("endDate", ""))
        if expected and actual:
            if expected == actual:
                stats["time_match"] += 1
            else:
                stats[f"time_mismatch:{matchup_zh}:{expected}!={actual}"] += 1
    return stats


def md_escape(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")


def write_output(rows: list[OutputRow]) -> None:
    lines = [
        "| 时间 | 比赛对阵双方 | 类型 | 事件 | Polymarket价格 | 赔率 | 交易量 |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {md_escape(row.time_text)} | {md_escape(row.matchup_zh)} | "
            f"{md_escape(row.market_type)} | {md_escape(row.event_name)} | "
            f"{md_escape(row.price)} | {md_escape(row.odds)} | {md_escape(row.volume)} |"
        )
    lines.extend(
        [
            "",
            "注：交易量来自 Polymarket 原始数据 `volume24h` 字段，单位为美元（USD），表示该 market 的 24 小时交易量。",
        ]
    )
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 2026 世界杯 Polymarket 赔率 Markdown 表")
    parser.add_argument("--date", help="只生成指定北京时间日期的比赛，例如 2026-06-16")
    parser.add_argument("--tomorrow", action="store_true", help="只生成北京时间明天的比赛")
    args = parser.parse_args()
    target_date = tomorrow_beijing() if args.tomorrow else args.date

    quotes_path = latest_csv("match-quotes")
    matches_path = latest_csv("matches")
    schedule = load_schedule_from_odds()
    quotes = read_csv(quotes_path)
    matches = read_csv(matches_path)
    rows, output_stats = make_rows(quotes, schedule, target_date)
    time_stats = validate_times(matches, schedule)
    write_output(rows)

    print(f"quotes={quotes_path}")
    print(f"matches={matches_path}")
    print(f"output={OUTPUT}")
    if target_date:
        print(f"target_date={target_date}")
    print(f"output_rows={output_stats['output_rows']}")
    print(f"time_match={time_stats['time_match']}")
    for key, value in sorted(output_stats.items()):
        if key != "output_rows":
            print(f"{key}={value}")
    for key, value in sorted(time_stats.items()):
        if key != "time_match":
            print(f"{key}={value}")


if __name__ == "__main__":
    main()
