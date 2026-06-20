#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT.parent / "world-cup-2026-arbitrage-trading-bot-main"
QUOTE_DIR = BOT_ROOT / "data" / "match-quotes"
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def proxy_env_present(env: dict[str, str]) -> bool:
    return any(env.get(key) for key in PROXY_KEYS)


def macos_system_proxy_env() -> dict[str, str]:
    try:
        result = subprocess.run(
            ["scutil", "--proxy"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}

    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()

    proxies: dict[str, str] = {}
    if values.get("HTTPEnable") == "1" and values.get("HTTPProxy") and values.get("HTTPPort"):
        proxies["HTTP_PROXY"] = f"http://{values['HTTPProxy']}:{values['HTTPPort']}"
    if values.get("HTTPSEnable") == "1" and values.get("HTTPSProxy") and values.get("HTTPSPort"):
        proxies["HTTPS_PROXY"] = f"http://{values['HTTPSProxy']}:{values['HTTPSPort']}"
    if "HTTPS_PROXY" not in proxies and "HTTP_PROXY" in proxies:
        proxies["HTTPS_PROXY"] = proxies["HTTP_PROXY"]
    if values.get("SOCKSEnable") == "1" and values.get("SOCKSProxy") and values.get("SOCKSPort"):
        proxies["ALL_PROXY"] = f"socks5://{values['SOCKSProxy']}:{values['SOCKSPort']}"
    return proxies


def apply_macos_system_proxy(env: dict[str, str]) -> None:
    for key, value in macos_system_proxy_env().items():
        env.setdefault(key, value)


def proxy_keys_in(env: dict[str, str]) -> list[str]:
    return [key for key in PROXY_KEYS if env.get(key)]


def node_supports_env_proxy() -> bool:
    try:
        result = subprocess.run(
            ["node", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "--use-env-proxy" in f"{result.stdout}\n{result.stderr}"


def bot_command(env: dict[str, str]) -> list[str]:
    override = env.get("POLYMARKET_BOT_COMMAND")
    if override:
        return shlex.split(override)

    command = ["node"]
    if proxy_env_present(env) and node_supports_env_proxy():
        command.append("--use-env-proxy")
    command.extend(["--import", "tsx", str(ROOT / "scripts" / "refresh_polymarket_once.ts")])
    return command


def latest_quote() -> Path | None:
    files = sorted(QUOTE_DIR.glob("*.csv"))
    return files[-1] if files else None


def tail_text(text: str, lines: int = 80) -> str:
    return "\n".join(text.splitlines()[-lines:])


def refresh_timeout_sec() -> int:
    raw = os.environ.get("POLYMARKET_REFRESH_TIMEOUT_SEC", "300")
    try:
        value = int(raw)
    except ValueError:
        return 300
    return max(value, 15)


def refresh_raw_polymarket_data(timeout_sec: int | None = None) -> None:
    if not BOT_ROOT.exists():
        raise FileNotFoundError(f"未找到 Polymarket 机器人目录：{BOT_ROOT}")

    if timeout_sec is None:
        timeout_sec = refresh_timeout_sec()

    before = latest_quote()
    env = os.environ.copy()
    apply_macos_system_proxy(env)
    env.update(
        {
            "LOCAL_DATA_ENABLED": "true",
            "LOCAL_DATA_DIR": "./data",
            "LOCAL_DATA_FORMAT": "csv",
            "MARKET_SCOPE": "matches",
            "MIN_MATCH_LIQUIDITY_USD": "500",
        }
    )

    print(f"刷新 Polymarket 原始数据：{BOT_ROOT}")
    command = bot_command(env)
    print(f"启动命令：{' '.join(command)}")
    proxy_keys = proxy_keys_in(env)
    if proxy_keys:
        print(f"检测到代理环境：{', '.join(proxy_keys)}")
    else:
        print("未检测到代理环境；Node 将直连 Polymarket API。")
    print(f"刷新超时：{timeout_sec} 秒")
    process = subprocess.Popen(
        command,
        cwd=BOT_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    try:
        output, _ = process.communicate(input="quit\n", timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        output, _ = process.communicate(timeout=15)
        print("机器人刷新超时，已终止；如果已写出新 CSV，将继续生成赔率表。")
    except KeyboardInterrupt:
        os.killpg(process.pid, signal.SIGTERM)
        print("收到中断，已终止 Polymarket 机器人子进程。")
        raise

    if output:
        print(tail_text(output))

    after = latest_quote()
    if after:
        print(f"最新 match-quotes：{after}")

    if process.returncode not in (0, None):
        raise RuntimeError(f"Polymarket 一次性刷新失败，退出码：{process.returncode}。")
    if after == before:
        raise RuntimeError("Polymarket 一次性刷新未产生新的 match-quotes CSV。")


def generate_next_day_odds() -> None:
    print("生成北京时间次日比赛的 Polymarket 赔率表。")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_polymarket_odds.py"), "--tomorrow"],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    refresh_raw_polymarket_data()
    generate_next_day_odds()


if __name__ == "__main__":
    main()
