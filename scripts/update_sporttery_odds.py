#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ODDS_PATH = ROOT / "2026世界杯体彩赔率.md"
SCHEDULE_PATH = ROOT / "2026世界杯赛程_北京时间.md"
API_URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=had,hhad,crs"
BJT = timezone(timedelta(hours=8))

RESULT_KEY = {"胜": "h", "平": "d", "负": "a"}
SCORE_KEY = {
    "1:0": "s01s00",
    "2:0": "s02s00",
    "2:1": "s02s01",
    "3:0": "s03s00",
    "3:1": "s03s01",
    "3:2": "s03s02",
    "4:0": "s04s00",
    "4:1": "s04s01",
    "4:2": "s04s02",
    "5:0": "s05s00",
    "5:1": "s05s01",
    "5:2": "s05s02",
    "胜其他": "s1sh",
    "0:0": "s00s00",
    "1:1": "s01s01",
    "2:2": "s02s02",
    "3:3": "s03s03",
    "平其他": "s1sd",
    "0:1": "s00s01",
    "0:2": "s00s02",
    "1:2": "s01s02",
    "0:3": "s00s03",
    "1:3": "s01s03",
    "2:3": "s02s03",
    "0:4": "s00s04",
    "1:4": "s01s04",
    "2:4": "s02s04",
    "0:5": "s00s05",
    "1:5": "s01s05",
    "2:5": "s02s05",
    "负其他": "s1sa",
}

TEAM_ALIASES = {
    "阿尔及利": "阿尔及利亚",
    "刚果金": "刚果民主共和国",
    "沙特": "沙特阿拉伯",
    "乌兹别克": "乌兹别克斯坦",
}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://m.sporttery.cn/mjc/jsq/zqspf/",
    "Origin": "https://m.sporttery.cn",
    "X-Requested-With": "XMLHttpRequest",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新 2026 世界杯体彩足球赔率表")
    parser.add_argument(
        "--date",
        help="要更新的北京时间日期，格式 YYYY-MM-DD；默认取北京时间明天",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印更新统计，不写入文件",
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        help="使用已有体彩接口 JSON 文件，跳过联网请求；用于调试",
    )
    return parser.parse_args()


def target_date(value: str | None) -> str:
    if value:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    return (datetime.now(BJT).date() + timedelta(days=1)).isoformat()


def canonical_team(name: str) -> str:
    return TEAM_ALIASES.get(name.strip(), name.strip())


def canonical_matchup(home: str, away: str) -> str:
    return f"{canonical_team(home)} vs {canonical_team(away)}"


def fetch_sporttery() -> dict[str, Any]:
    errors: list[str] = []
    body = fetch_sporttery_via_curl(errors)
    if body is not None:
        return parse_payload(body)

    request = urllib.request.Request(API_URL, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
        return parse_payload(body)
    except (urllib.error.URLError, TimeoutError) as exc:
        errors.append(f"urllib fallback: {exc}")
        raise RuntimeError("体彩接口请求失败：" + " | ".join(errors)) from exc


def fetch_sporttery_via_curl(errors: list[str]) -> str | None:
    base_cmd = [
        "curl",
        "--silent",
        "--show-error",
        "--location",
        "--connect-timeout",
        "15",
        "--max-time",
        "60",
        "--http1.1",
        "--ipv4",
    ]
    header_args: list[str] = []
    for key, value in REQUEST_HEADERS.items():
        header_args.extend(["-H", f"{key}: {value}"])

    # macOS 自带 Python(LibreSSL) 对该域名 SSL 握手常超时；curl 更稳定。
    # 若默认解析到的 IP 握手失败(exit 35)，依次固定到已知可用 IP 重试。
    resolve_targets: list[str | None] = [
        None,
        "webapi.sporttery.cn:443:117.185.125.154",
        "webapi.sporttery.cn:443:183.192.184.85",
    ]
    for index, resolve in enumerate(resolve_targets, start=1):
        curl_cmd = base_cmd.copy()
        if resolve:
            curl_cmd.extend(["--resolve", resolve])
        curl_cmd.append(API_URL)
        curl_cmd.extend(header_args)
        label = resolve or "default"
        try:
            completed = subprocess.run(
                curl_cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=75,
            )
            if completed.stdout.strip():
                return completed.stdout
            errors.append(f"curl {label}: empty response")
        except FileNotFoundError as exc:
            errors.append(f"curl {label}: {exc}")
            return None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            errors.append(f"curl {label}: {exc}")
    return None


def parse_payload(body: str) -> dict[str, Any]:

    if "WAF拦截页面" in body or body.lstrip().startswith("<!DOCTYPE html"):
        raise RuntimeError("体彩接口返回 HTML/WAF 拦截页，未拿到 JSON")

    payload = json.loads(body)
    if str(payload.get("errorCode")) != "0":
        raise RuntimeError(f"体彩接口错误：{payload.get('errorCode')} {payload.get('errorMessage')}")
    return payload


def api_matches(payload: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    matches: dict[tuple[str, str, str], dict[str, Any]] = {}
    for group in payload.get("value", {}).get("matchInfoList", []):
        for match in group.get("subMatchList", []):
            match_date = match.get("matchDate", "")
            match_time = match.get("matchTime", "")[:5]
            matchup = canonical_matchup(match.get("homeTeamAbbName", ""), match.get("awayTeamAbbName", ""))
            if match_date and match_time and matchup != " vs ":
                matches[(match_date, match_time, matchup)] = match
    return matches


def load_schedule() -> dict[tuple[str, str], list[str]]:
    schedule: dict[tuple[str, str], list[str]] = {}
    if not SCHEDULE_PATH.exists():
        return schedule

    for line in SCHEDULE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| 场次 ") or line.startswith("|---"):
            continue
        cells = markdown_cells(line)
        if len(cells) < 5 or not cells[0].isdigit() or " vs " not in cells[4]:
            continue
        date_text = row_date(cells[2])
        time_text = cells[3].strip()
        home, away = (part.strip() for part in cells[4].split(" vs ", 1))
        matchup = canonical_matchup(home, away)
        key = (date_text, time_text)
        schedule.setdefault(key, [])
        if matchup not in schedule[key]:
            schedule[key].append(matchup)
    return schedule


def markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def row_date(time_text: str) -> str:
    return time_text.split("（", 1)[0].strip()


def row_time(time_text: str) -> str:
    return time_text.rsplit(" ", 1)[-1].strip()


def odds_value(pool: dict[str, Any], key: str | None) -> str:
    if not pool or not key:
        return "-"
    value = pool.get(key)
    return value if value else "-"


def update_lines(
    lines: list[str],
    matches: dict[tuple[str, str, str], dict[str, Any]],
    schedule: dict[tuple[str, str], list[str]],
    date_text: str,
) -> tuple[list[str], dict[str, Any]]:
    stats: dict[str, Any] = {
        "target_rows": 0,
        "updated_odds": 0,
        "dash_odds": 0,
        "changed_handicap_rows": 0,
        "changed_matchup_rows": 0,
        "matched_matchups": set(),
        "unmatched_matchups": set(),
        "schedule_resolved_matchups": set(),
    }
    new_lines: list[str] = []

    for line in lines:
        if not line.startswith("| ") or line.startswith("| 时间 ") or line.startswith("|---"):
            new_lines.append(line)
            continue

        cells = markdown_cells(line)
        if len(cells) != 5:
            new_lines.append(line)
            continue

        time_text, matchup, market_type, event_name, old_odd = cells
        if row_date(time_text) != date_text:
            new_lines.append(line)
            continue

        stats["target_rows"] += 1
        time_value = row_time(time_text)
        effective_matchup = matchup
        match = matches.get((date_text, time_value, effective_matchup))
        if not match:
            scheduled_matchups = schedule.get((date_text, time_value), [])
            if len(scheduled_matchups) == 1:
                effective_matchup = scheduled_matchups[0]
            elif matchup in scheduled_matchups:
                effective_matchup = matchup
            else:
                api_candidates = [
                    candidate
                    for candidate in scheduled_matchups
                    if (date_text, time_value, candidate) in matches
                ]
                if len(api_candidates) == 1:
                    effective_matchup = api_candidates[0]

            if effective_matchup != matchup:
                stats["changed_matchup_rows"] += 1
                stats["schedule_resolved_matchups"].add(f"{matchup} -> {effective_matchup}")
                match = matches.get((date_text, time_value, effective_matchup))

        odd = "-"
        new_market_type = market_type

        if not match:
            stats["unmatched_matchups"].add(effective_matchup)
        elif market_type == "胜负":
            stats["matched_matchups"].add(effective_matchup)
            odd = odds_value(match.get("had") or {}, RESULT_KEY.get(event_name))
        elif market_type.startswith("让负（"):
            stats["matched_matchups"].add(effective_matchup)
            pool = match.get("hhad") or {}
            if pool:
                new_market_type = f"让负（{pool.get('goalLine') or '0'}）"
                if new_market_type != market_type:
                    stats["changed_handicap_rows"] += 1
            odd = odds_value(pool, RESULT_KEY.get(event_name))
        elif market_type == "比分":
            stats["matched_matchups"].add(effective_matchup)
            odd = odds_value(match.get("crs") or {}, SCORE_KEY.get(event_name))

        if odd == "-":
            stats["dash_odds"] += 1
        elif odd != old_odd or new_market_type != market_type:
            stats["updated_odds"] += 1

        new_lines.append(f"| {time_text} | {effective_matchup} | {new_market_type} | {event_name} | {odd} |")

    return new_lines, stats


def main() -> None:
    args = parse_args()
    date_text = target_date(args.date)
    if not ODDS_PATH.exists():
        raise FileNotFoundError(f"未找到赔率表：{ODDS_PATH}")

    if args.input_json:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
        if str(payload.get("errorCode")) != "0":
            raise RuntimeError(f"体彩接口错误：{payload.get('errorCode')} {payload.get('errorMessage')}")
    else:
        payload = fetch_sporttery()
    matches = api_matches(payload)
    schedule = load_schedule()
    lines = ODDS_PATH.read_text(encoding="utf-8").splitlines()
    new_lines, stats = update_lines(lines, matches, schedule, date_text)

    if stats["target_rows"] == 0:
        raise RuntimeError(f"{ODDS_PATH.name} 中未找到 {date_text} 的比赛行")

    if not args.dry_run:
        ODDS_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    print(f"Target date: {date_text}")
    print(f"Source last update: {payload.get('value', {}).get('lastUpdateTime', '-')}")
    print(f"Target rows: {stats['target_rows']}")
    print(f"Matched matchups: {len(stats['matched_matchups'])} {sorted(stats['matched_matchups'])}")
    print(f"Unmatched matchups: {len(stats['unmatched_matchups'])} {sorted(stats['unmatched_matchups'])}")
    print(f"Schedule-resolved matchups: {len(stats['schedule_resolved_matchups'])} {sorted(stats['schedule_resolved_matchups'])}")
    print(f"Updated odds/type rows: {stats['updated_odds']}")
    print(f"Rows with '-': {stats['dash_odds']}")
    print(f"Changed handicap rows: {stats['changed_handicap_rows']}")
    print(f"Changed matchup rows: {stats['changed_matchup_rows']}")
    print(f"Dry run: {args.dry_run}")


if __name__ == "__main__":
    main()
