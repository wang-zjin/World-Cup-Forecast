#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from generate_polymarket_odds import (
        BEIJING_TZ,
        OUTPUT as POLYMARKET_OUTPUT,
        ScheduleEntry,
        TEAM_EN_TO_ZH,
        load_schedule_from_odds,
        md_escape,
        schedule_date,
        tomorrow_beijing,
    )
except ModuleNotFoundError:
    from scripts.generate_polymarket_odds import (
        BEIJING_TZ,
        OUTPUT as POLYMARKET_OUTPUT,
        ScheduleEntry,
        TEAM_EN_TO_ZH,
        load_schedule_from_odds,
        md_escape,
        schedule_date,
        tomorrow_beijing,
    )


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "2026世界杯国际博彩赔率.md"
API_BASE = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"
DEFAULT_SPORT = "soccer_fifa_world_cup"
DEFAULT_REGIONS = "uk,eu,us,au"
DEFAULT_MARKETS = "h2h,spreads,totals"
ENV_API_KEYS = ("ODDS_API_KEY", "THE_ODDS_API_KEY")

TEAM_NAME_ALIASES = {
    "USA": "United States",
    "U.S.A.": "United States",
    "US": "United States",
    "Korea Republic": "South Korea",
    "Republic of Korea": "South Korea",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Cote D'Ivoire": "Cote d'Ivoire",
    "Côte D'Ivoire": "Côte d'Ivoire",
    "DR Congo": "Democratic Republic of the Congo",
    "Congo DR": "Democratic Republic of the Congo",
}

MARKET_ORDER = {
    "h2h": 0,
    "h2h_3_way": 0,
    "h2h_lay": 1,
    "draw_no_bet": 2,
    "spreads": 3,
    "totals": 4,
    "btts": 5,
}


@dataclass(frozen=True)
class OutputRow:
    sort_key: tuple[int, str, int, int, int]
    time_text: str
    matchup_zh: str
    bookmaker: str
    market_type: str
    event_name: str
    odds: str
    last_update: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 2026 世界杯国际博彩公司赔率 Markdown 表")
    parser.add_argument("--date", help="只生成指定北京时间日期的比赛，例如 2026-06-27")
    parser.add_argument("--tomorrow", action="store_true", help="只生成北京时间明天的比赛")
    parser.add_argument("--sport", default=DEFAULT_SPORT, help=f"The Odds API sport key，默认 {DEFAULT_SPORT}")
    parser.add_argument("--regions", default=DEFAULT_REGIONS, help=f"博彩公司区域，默认 {DEFAULT_REGIONS}")
    parser.add_argument("--bookmakers", help="逗号分隔的 bookmaker key；设置后优先于 regions")
    parser.add_argument("--markets", default=DEFAULT_MARKETS, help=f"逗号分隔的市场，默认 {DEFAULT_MARKETS}")
    parser.add_argument("--api-key", help="The Odds API key；也可用 ODDS_API_KEY 或 THE_ODDS_API_KEY")
    parser.add_argument("--input-json", type=Path, help="使用已有 The Odds API JSON 文件，跳过联网请求")
    parser.add_argument("--output", type=Path, default=OUTPUT, help=f"输出 Markdown 文件，默认 {OUTPUT.name}")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写入文件")
    parser.add_argument("--watch-interval", type=int, help="循环刷新间隔秒数；建议不低于 300")
    parser.add_argument("--timeout", type=int, default=45, help="联网请求超时秒数，默认 45")
    return parser.parse_args()


def resolve_target_date(args: argparse.Namespace) -> str | None:
    if args.tomorrow and args.date:
        raise ValueError("--date 和 --tomorrow 只能二选一")
    if args.tomorrow:
        return tomorrow_beijing()
    if args.date:
        datetime.strptime(args.date, "%Y-%m-%d")
        return args.date
    return None


def api_key_from(args: argparse.Namespace) -> str:
    if args.api_key:
        return args.api_key
    for key in ENV_API_KEYS:
        value = os.environ.get(key)
        if value:
            return value
    raise RuntimeError("缺少 The Odds API key：请设置 ODDS_API_KEY，或传入 --api-key")


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def beijing_day_bounds(date_text: str) -> tuple[str, str]:
    start = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return iso_z(start), iso_z(end)


def fetch_odds(args: argparse.Namespace, target_date: str | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    params = {
        "apiKey": api_key_from(args),
        "markets": args.markets,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    if args.bookmakers:
        params["bookmakers"] = args.bookmakers
    else:
        params["regions"] = args.regions
    if target_date:
        params["commenceTimeFrom"], params["commenceTimeTo"] = beijing_day_bounds(target_date)

    url = API_BASE.format(sport=urllib.parse.quote(args.sport, safe="")) + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8")
            headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"The Odds API 请求失败：HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"The Odds API 请求失败：{exc}") from exc

    payload = json.loads(body)
    if isinstance(payload, dict):
        raise RuntimeError(f"The Odds API 返回错误：{payload}")
    if not isinstance(payload, list):
        raise RuntimeError("The Odds API 返回了非列表 JSON，无法解析")
    return payload, headers


def load_payload(args: argparse.Namespace, target_date: str | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if args.input_json:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError(f"{args.input_json} 不是 The Odds API /odds 列表响应")
        return payload, {}
    return fetch_odds(args, target_date)


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def beijing_date_from_iso(value: str) -> str | None:
    dt = parse_iso(value)
    if not dt:
        return None
    return dt.astimezone(BEIJING_TZ).date().isoformat()


def beijing_time_from_iso(value: str) -> str:
    dt = parse_iso(value)
    if not dt:
        return "-"
    return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def normalized_team_name(name: str) -> str:
    stripped = name.strip()
    return TEAM_NAME_ALIASES.get(stripped, stripped)


def team_zh(name: str) -> str:
    normalized = normalized_team_name(name)
    return TEAM_EN_TO_ZH.get(normalized, normalized)


def matchup_for_event(
    event: dict[str, Any],
    schedule: dict[str, ScheduleEntry],
) -> tuple[ScheduleEntry | None, str, str, str | None]:
    home_zh = team_zh(str(event.get("home_team") or ""))
    away_zh = team_zh(str(event.get("away_team") or ""))
    forward = f"{home_zh} vs {away_zh}"
    reverse = f"{away_zh} vs {home_zh}"
    if forward in schedule:
        return schedule[forward], home_zh, away_zh, forward
    if reverse in schedule:
        return schedule[reverse], away_zh, home_zh, reverse
    return None, home_zh, away_zh, None


def format_point(value: Any) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def format_odds(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number <= 0:
        return "-"
    return f"{number:.2f}"


def side_label(outcome_name: str, local_home: str, local_away: str) -> str:
    if outcome_name == "Draw":
        return "平"
    outcome_zh = team_zh(outcome_name)
    if outcome_zh == local_home:
        return "胜"
    if outcome_zh == local_away:
        return "负"
    return outcome_zh


def outcome_label(outcome: dict[str, Any], market_key: str, local_home: str, local_away: str) -> tuple[str, str]:
    name = str(outcome.get("name") or "")
    point = outcome.get("point")
    if market_key in {"h2h", "h2h_3_way"}:
        return "胜负", side_label(name, local_home, local_away)
    if market_key == "h2h_lay":
        return "胜负反向", side_label(name, local_home, local_away)
    if market_key == "draw_no_bet":
        return "平局退回", side_label(name, local_home, local_away)
    if market_key == "spreads":
        return f"让球（{format_point(point)}）", team_zh(name)
    if market_key == "totals":
        labels = {"Over": "大", "Under": "小"}
        return f"全场大小球（{format_point(point)}）", labels.get(name, name)
    if market_key == "btts":
        labels = {"Yes": "是", "No": "否"}
        return "双方进球", labels.get(name, name)

    event_name = team_zh(name)
    if point is not None:
        return f"{market_key}（{format_point(point)}）", event_name
    return market_key, event_name


def make_rows(
    events: list[dict[str, Any]],
    schedule: dict[str, ScheduleEntry],
    target_date: str | None,
) -> tuple[list[OutputRow], Counter[str]]:
    rows: list[OutputRow] = []
    stats: Counter[str] = Counter()

    for event_index, event in enumerate(events):
        event_date = beijing_date_from_iso(str(event.get("commence_time") or ""))
        if target_date and event_date != target_date:
            stats["skipped_non_target_date"] += 1
            continue

        entry, local_home, local_away, matchup_zh = matchup_for_event(event, schedule)
        if not entry or not matchup_zh:
            stats[f"unmatched:{team_zh(str(event.get('home_team') or ''))} vs {team_zh(str(event.get('away_team') or ''))}"] += 1
            continue
        if target_date and schedule_date(entry.time_text) != target_date:
            stats["skipped_schedule_date_mismatch"] += 1
            continue

        for bookmaker in event.get("bookmakers", []):
            bookmaker_title = str(bookmaker.get("title") or bookmaker.get("key") or "-")
            bookmaker_update = str(bookmaker.get("last_update") or "")
            for market_index, market in enumerate(bookmaker.get("markets", [])):
                market_key = str(market.get("key") or "")
                market_update = str(market.get("last_update") or bookmaker_update)
                for outcome_index, outcome in enumerate(market.get("outcomes", [])):
                    market_type, event_name = outcome_label(outcome, market_key, local_home, local_away)
                    rows.append(
                        OutputRow(
                            sort_key=(
                                entry.index,
                                bookmaker_title.lower(),
                                MARKET_ORDER.get(market_key, 99),
                                market_index,
                                outcome_index,
                            ),
                            time_text=entry.time_text,
                            matchup_zh=matchup_zh,
                            bookmaker=bookmaker_title,
                            market_type=market_type,
                            event_name=event_name,
                            odds=format_odds(outcome.get("price")),
                            last_update=beijing_time_from_iso(market_update),
                        )
                    )
                    stats["output_rows"] += 1
        stats["matched_events"] += 1

    rows.sort(key=lambda row: row.sort_key)
    return rows, stats


def write_output(rows: list[OutputRow], output_path: Path) -> None:
    lines = [
        "| 时间 | 比赛对阵双方 | 博彩公司 | 类型 | 事件 | 赔率 | 更新时间 |",
        "|---|---|---|---|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {md_escape(row.time_text)} | {md_escape(row.matchup_zh)} | "
            f"{md_escape(row.bookmaker)} | {md_escape(row.market_type)} | "
            f"{md_escape(row.event_name)} | {md_escape(row.odds)} | {md_escape(row.last_update)} |"
        )
    lines.extend(
        [
            "",
            "注：数据来源为 The Odds API 官方接口；赔率为十进制赔率，更新时间为接口返回的 bookmaker/market `last_update` 转北京时间。",
            f"注：本表只作为国际博彩公司赔率快照，不会改写 `{POLYMARKET_OUTPUT.name}` 或体彩赔率表。",
        ]
    )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_stats(
    args: argparse.Namespace,
    output_path: Path,
    target_date: str | None,
    events: list[dict[str, Any]],
    stats: Counter[str],
    headers: dict[str, str],
) -> None:
    print(f"sport={args.sport}")
    print(f"markets={args.markets}")
    if args.bookmakers:
        print(f"bookmakers={args.bookmakers}")
    else:
        print(f"regions={args.regions}")
    if target_date:
        print(f"target_date={target_date}")
    print(f"source_events={len(events)}")
    print(f"matched_events={stats['matched_events']}")
    print(f"output_rows={stats['output_rows']}")
    print(f"output={output_path}")
    for key in ("x-requests-last", "x-requests-used", "x-requests-remaining"):
        if key in headers:
            print(f"{key}={headers[key]}")
    for key, value in sorted(stats.items()):
        if key not in {"matched_events", "output_rows"}:
            print(f"{key}={value}")
    print(f"dry_run={args.dry_run}")


def run_once(args: argparse.Namespace) -> None:
    target_date = resolve_target_date(args)
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    events, headers = load_payload(args, target_date)
    schedule = load_schedule_from_odds()
    rows, stats = make_rows(events, schedule, target_date)
    if not args.dry_run:
        write_output(rows, output_path)
    print_stats(args, output_path, target_date, events, stats, headers)


def main() -> None:
    args = parse_args()
    if args.watch_interval is None:
        run_once(args)
        return

    interval = max(args.watch_interval, 60)
    while True:
        print(f"refresh_at={datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S %z')}")
        run_once(args)
        print(f"sleep_seconds={interval}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
