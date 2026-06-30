#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "2026世界杯赛程_北京时间.md"
PNG_PATH = ROOT / "2026世界杯赛程_北京时间.png"

FIXTURE_PAGE = "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures"
API_URL = "https://api.fifa.com/api/v3/calendar/matches?language=en&count=500&idCompetition=17&idSeason=285023"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 world-cup-schedule-refresh/1.0",
    "Accept": "application/json",
}
BJT = timezone(timedelta(hours=8))
WEEK_ZH = "一二三四五六日"

TEAM_ZH = {
    "Algeria": "阿尔及利亚",
    "Argentina": "阿根廷",
    "Australia": "澳大利亚",
    "Austria": "奥地利",
    "Belgium": "比利时",
    "Bosnia and Herzegovina": "波黑",
    "Brazil": "巴西",
    "Cabo Verde": "佛得角",
    "Canada": "加拿大",
    "Cape Verde": "佛得角",
    "Colombia": "哥伦比亚",
    "Croatia": "克罗地亚",
    "Curaçao": "库拉索",
    "Czechia": "捷克",
    "Czech Republic": "捷克",
    "DR Congo": "刚果民主共和国",
    "Congo DR": "刚果民主共和国",
    "Ecuador": "厄瓜多尔",
    "Egypt": "埃及",
    "England": "英格兰",
    "France": "法国",
    "Germany": "德国",
    "Ghana": "加纳",
    "Haiti": "海地",
    "Iran": "伊朗",
    "IR Iran": "伊朗",
    "Iraq": "伊拉克",
    "Côte d'Ivoire": "科特迪瓦",
    "Ivory Coast": "科特迪瓦",
    "Japan": "日本",
    "Jordan": "约旦",
    "Korea Republic": "韩国",
    "South Korea": "韩国",
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
    "Spain": "西班牙",
    "Sweden": "瑞典",
    "Switzerland": "瑞士",
    "Tunisia": "突尼斯",
    "Türkiye": "土耳其",
    "Turkey": "土耳其",
    "USA": "美国",
    "United States": "美国",
    "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦",
}

STAGE_ZH = {
    "Round of 32": "淘汰赛 32强",
    "Round of 16": "淘汰赛 16强",
    "Quarter-final": "淘汰赛 1/4决赛",
    "Semi-final": "淘汰赛 半决赛",
    "Play-off for third place": "淘汰赛 三四名决赛",
    "Final": "淘汰赛 决赛",
}


def localized_description(items: list[dict[str, str]] | None) -> str:
    if not items:
        return ""
    for item in items:
        if item.get("Locale", "").lower() in {"en-gb", "en"}:
            return item.get("Description", "")
    return items[0].get("Description", "")


def placeholder_name(value: str) -> str:
    value = value.strip()
    match = re.fullmatch(r"([12])([A-L])", value)
    if match:
        return f"{match.group(2)}组{'第一' if match.group(1) == '1' else '第二'}"

    match = re.fullmatch(r"3([A-L](?:/[A-L])*)", value)
    if match:
        return f"{'/'.join(match.group(1).split('/'))}组第三之一"

    match = re.fullmatch(r"3([A-L]{2,})", value)
    if match:
        return f"{'/'.join(match.group(1))}组第三之一"

    match = re.fullmatch(r"W(\d+)", value)
    if match:
        return f"第{match.group(1)}场胜者"

    match = re.fullmatch(r"(?:RU|L)(\d+)", value)
    if match:
        return f"第{match.group(1)}场负者"

    return value


def team_name(team: dict[str, Any] | None, placeholder: str | None) -> str:
    if not team:
        return placeholder_name(placeholder or "")
    raw = team.get("ShortClubName") or localized_description(team.get("TeamName")) or team.get("Abbreviation") or ""
    return TEAM_ZH.get(raw, raw)


def stage_name(match: dict[str, Any]) -> str:
    stage = localized_description(match.get("StageName"))
    if stage == "First Stage":
        group = localized_description(match.get("GroupName"))
        group_match = re.search(r"Group\s+([A-L])", group)
        return f"小组赛 {group_match.group(1)}组" if group_match else "小组赛"
    return STAGE_ZH.get(stage, stage)


def parse_bjt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(BJT)


def format_bjt_date(value: datetime) -> str:
    return f"{value:%Y-%m-%d}（周{WEEK_ZH[value.weekday()]}）"


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")


def result_text(match: dict[str, Any]) -> str:
    home_score = match.get("HomeTeamScore")
    away_score = match.get("AwayTeamScore")
    if home_score is None and match.get("Home"):
        home_score = match["Home"].get("Score")
    if away_score is None and match.get("Away"):
        away_score = match["Away"].get("Score")
    if home_score is None or away_score is None:
        return "待赛"

    result = f"{home_score}–{away_score}"
    home_penalty = match.get("HomeTeamPenaltyScore")
    away_penalty = match.get("AwayTeamPenaltyScore")
    if home_penalty is not None and away_penalty is not None:
        result += f"（点球 {home_penalty}–{away_penalty}）"
    return result


def venue_name(match: dict[str, Any]) -> str:
    stadium = match.get("Stadium") or {}
    name = localized_description(stadium.get("Name"))
    city = localized_description(stadium.get("CityName"))
    if name and city and city not in name:
        return f"{name}, {city}"
    return name or city


def fetch_payload() -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, 4):
        request = urllib.request.Request(API_URL, headers=REQUEST_HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            errors.append(f"urllib attempt {attempt}: {exc}")

    curl_cmd = [
        "curl",
        "--silent",
        "--show-error",
        "--location",
        "--retry",
        "3",
        "--retry-delay",
        "2",
        "--connect-timeout",
        "20",
        "--max-time",
        "90",
        "--http1.1",
        "--ipv4",
        API_URL,
    ]
    for key, value in REQUEST_HEADERS.items():
        curl_cmd.extend(["-H", f"{key}: {value}"])

    try:
        completed = subprocess.run(curl_cmd, check=True, capture_output=True, text=True)
        return json.loads(completed.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as exc:
        errors.append(f"curl fallback: {exc}")
        raise RuntimeError("FIFA API 请求失败：" + " | ".join(errors)) from exc


def fetch_rows() -> list[dict[str, Any]]:
    payload = fetch_payload()

    rows: list[dict[str, Any]] = []
    for match in payload.get("Results", []):
        match_no = int(match["MatchNumber"])
        bjt = parse_bjt(match["Date"])
        home = team_name(match.get("Home"), match.get("PlaceHolderA"))
        away = team_name(match.get("Away"), match.get("PlaceHolderB"))
        rows.append(
            {
                "match_no": match_no,
                "stage": stage_name(match),
                "bjt": bjt,
                "matchup": f"{home} vs {away}",
                "result": result_text(match),
                "venue": venue_name(match),
            }
        )

    rows.sort(key=lambda row: row["match_no"])
    match_numbers = [row["match_no"] for row in rows]
    if len(rows) != 104 or match_numbers != list(range(1, 105)):
        raise RuntimeError(f"FIFA data sanity check failed: count={len(rows)}, match_numbers={match_numbers[:5]}...")
    return rows


def write_markdown(rows: list[dict[str, Any]], fetched_at: datetime) -> None:
    lines = [
        "# 2026世界杯赛程表（北京时间）",
        "",
        f"- 数据来源：[FIFA 官方赛程]({FIXTURE_PAGE})；官方 API season id：`285023`。",
        f"- 核对时间：{fetched_at:%Y-%m-%d %H:%M}（北京时间，UTC+8）",
        "- 说明：本表按 FIFA 官方 `MatchNumber` 场次号排序；北京时间由 FIFA UTC 开球时间换算。",
        "- 状态：已赛比赛显示比分，未赛比赛标为“待赛”。",
        "",
        "| 场次 | 阶段 | 北京日期 | 北京时间 | 对阵 | 比分/状态 | 场馆 |",
        "|---:|---|---|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['match_no']} | {markdown_escape(row['stage'])} | {format_bjt_date(row['bjt'])} | {row['bjt']:%H:%M} | "
            f"{markdown_escape(row['matchup'])} | {markdown_escape(row['result'])} | {markdown_escape(row['venue'])} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_font() -> str | None:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    return next((candidate for candidate in candidates if Path(candidate).exists()), None)


FONT_PATH = find_font()


def load_font(size: int) -> ImageFont.ImageFont:
    if FONT_PATH:
        try:
            return ImageFont.truetype(FONT_PATH, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    if text_width(draw, text, font) <= max_width:
        return [text]

    tokens = re.split(r"(\s+)", text)
    lines: list[str] = []
    current = ""
    for token in tokens:
        if not token:
            continue
        candidate = current + token
        if current and text_width(draw, candidate, font) > max_width:
            lines.append(current.strip())
            current = token.lstrip()
        else:
            current = candidate
    if current.strip():
        lines.append(current.strip())

    fixed: list[str] = []
    for line in lines or [text]:
        if text_width(draw, line, font) <= max_width:
            fixed.append(line)
            continue
        current = ""
        for char in line:
            if current and text_width(draw, current + char, font) > max_width:
                fixed.append(current)
                current = char
            else:
                current += char
        if current:
            fixed.append(current)
    return fixed


def write_png(rows: list[dict[str, Any]], fetched_at: datetime) -> None:
    title_font = load_font(44)
    subtitle_font = load_font(21)
    header_font = load_font(22)
    cell_font = load_font(21)
    small_font = load_font(18)

    columns = [
        ("场次", 74, "center"),
        ("阶段", 150, "left"),
        ("北京日期", 218, "left"),
        ("时间", 92, "center"),
        ("对阵", 350, "left"),
        ("比分/状态", 112, "center"),
        ("场馆", 430, "left"),
    ]
    margin_x = 54
    table_width = sum(column[1] for column in columns)
    width = table_width + margin_x * 2
    scratch = Image.new("RGB", (width, 100), "white")
    draw = ImageDraw.Draw(scratch)

    header_height = 52
    title_height = 126
    footer_height = 72
    pad_x = 12
    pad_y = 9
    line_height = 29

    rendered = []
    for row in rows:
        cells = [
            str(row["match_no"]),
            row["stage"],
            format_bjt_date(row["bjt"]),
            f"{row['bjt']:%H:%M}",
            row["matchup"],
            row["result"],
            row["venue"],
        ]
        wrapped = []
        max_lines = 1
        for (_, column_width, _), cell in zip(columns, cells):
            cell_lines = wrap_text(draw, cell, cell_font, column_width - pad_x * 2)
            wrapped.append(cell_lines)
            max_lines = max(max_lines, len(cell_lines))
        rendered.append((row, wrapped, max(46, pad_y * 2 + max_lines * line_height)))

    height = title_height + header_height + sum(row_height for _, _, row_height in rendered) + footer_height
    image = Image.new("RGB", (width, height), "#f8faf7")
    draw = ImageDraw.Draw(image)

    draw.rectangle([0, 0, width, title_height], fill="#0b3d4c")
    draw.text((margin_x, 26), "2026世界杯赛程表（北京时间 UTC+8）", font=title_font, fill="white")
    draw.text(
        (margin_x, 82),
        f"按 FIFA 官方场次号排序｜共 {len(rows)} 场｜核对 {fetched_at:%Y-%m-%d %H:%M}",
        font=subtitle_font,
        fill="#dceee9",
    )

    y = title_height
    x = margin_x
    draw.rectangle([margin_x, y, margin_x + table_width, y + header_height], fill="#176b6b")
    for label, column_width, align in columns:
        draw.line([x, y, x, y + header_height], fill="#d5ebe5", width=1)
        text_x = x + pad_x if align != "center" else x + (column_width - text_width(draw, label, header_font)) / 2
        draw.text((text_x, y + 14), label, font=header_font, fill="white")
        x += column_width
    draw.line([margin_x + table_width, y, margin_x + table_width, y + header_height], fill="#d5ebe5", width=1)
    y += header_height

    section_breaks = {13, 25, 37, 49, 61, 73, 89, 97, 101, 103, 104}
    for index, (row, wrapped, row_height) in enumerate(rendered):
        fill = "#ffffff" if index % 2 == 0 else "#eef6f3"
        if row["result"] != "待赛":
            fill = "#fff7e6" if index % 2 == 0 else "#f8ecd3"
        draw.rectangle([margin_x, y, margin_x + table_width, y + row_height], fill=fill)
        draw.line(
            [margin_x, y, margin_x + table_width, y],
            fill="#d48528" if row["match_no"] in section_breaks else "#d7e2df",
            width=3 if row["match_no"] in section_breaks else 1,
        )
        x = margin_x
        for (_, column_width, align), cell_lines in zip(columns, wrapped):
            draw.line([x, y, x, y + row_height], fill="#d7e2df", width=1)
            text_y = y + (row_height - len(cell_lines) * line_height) / 2 - 1
            for line in cell_lines:
                text_x = x + pad_x if align != "center" else x + (column_width - text_width(draw, line, cell_font)) / 2
                draw.text((text_x, text_y), line, font=cell_font, fill="#182426")
                text_y += line_height
            x += column_width
        draw.line([margin_x + table_width, y, margin_x + table_width, y + row_height], fill="#d7e2df", width=1)
        y += row_height

    draw.line([margin_x, y, margin_x + table_width, y], fill="#176b6b", width=2)
    draw.text(
        (margin_x, y + 18),
        "说明：橙色行为已赛比赛；未赛比赛标为“待赛”。场次号按 FIFA 官方 MatchNumber 排序。",
        font=small_font,
        fill="#374a4c",
    )
    draw.text((margin_x, y + 43), "数据来源：FIFA 官方赛程 / API season id 285023", font=small_font, fill="#5c6d70")
    image.save(PNG_PATH)


def main() -> None:
    rows = fetch_rows()
    fetched_at = datetime.now(BJT)
    write_markdown(rows, fetched_at)
    write_png(rows, fetched_at)
    print(f"Updated {MD_PATH}")
    print(f"Updated {PNG_PATH}")
    print(f"Rows: {len(rows)}; fetched_at_bjt={fetched_at:%Y-%m-%d %H:%M}")


if __name__ == "__main__":
    main()
