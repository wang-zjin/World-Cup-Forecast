---
name: polymarket-worldcup-odds-fetch
description: 获取 Polymarket 2026 世界杯比赛市场数据，使用机器人导出的 Gamma/CLOB CSV，转换队名和北京时间，计算十进制赔率、交易量，并写入本项目的 2026世界杯polymarket赔率.md。适用于刷新 Polymarket 胜负、比分、让球、大小球、半场、角球、球员数据等赔率表。
---

# Polymarket 世界杯赔率数据获取与处理

## 使用场景

当用户需要刷新或生成本项目的 Polymarket 世界杯赔率表时使用本 skill。典型请求包括：

- 获取 Polymarket 最新世界杯赔率
- 更新 `2026世界杯polymarket赔率.md`
- 从机器人数据转换 Polymarket market、价格、赔率、交易量
- 对比 Polymarket 与体彩赔率表的时间和对阵
- 修复 Polymarket 队名、market 类型、球员数据翻译或赔率转换问题

## 项目文件约定

本项目目录：

```text
/Users/irtg/Documents/世界杯/2026世界杯预测
```

Polymarket 机器人目录在本项目上一级：

```text
../world-cup-2026-arbitrage-trading-bot-main
```

关键文件：

```text
2026世界杯体彩赔率.md                 用于提供北京时间和中文对阵
2026世界杯polymarket赔率.md          本 skill 生成或更新的目标文件
polymarket数据描述.md                数据口径说明
scripts/generate_polymarket_odds.py  转换脚本
scripts/refresh_polymarket_next_day.py  自动化刷新脚本：获取原始数据并生成次日比赛赔率
```

机器人导出的原始数据：

```text
../world-cup-2026-arbitrage-trading-bot-main/data/matches/*.csv
../world-cup-2026-arbitrage-trading-bot-main/data/match-quotes/*.csv
```

其中：

- `matches/*.csv` 来自 Polymarket Gamma API，主要是 market 元数据、事件标题、开赛时间、交易量等。
- `match-quotes/*.csv` 来自 Polymarket CLOB API，主要是 outcome 的实时买入价格。
- 生成赔率表时，价格以 `match-quotes` 里的 `prices` 为准。
- 交易量使用原始数据里的 `volume24h` 字段，单位是美元（USD）。

## 优先流程

如果用户要求“生成全部可匹配比赛”或“处理已有数据”，运行本项目脚本：

```bash
python3 scripts/generate_polymarket_odds.py
```

如果用户要求“次日比赛”或用于每日自动化，运行：

```bash
python3 scripts/generate_polymarket_odds.py --tomorrow
```

脚本会自动：

1. 读取最新的 `data/match-quotes/*.csv`。
2. 读取最新的 `data/matches/*.csv`。
3. 从 `2026世界杯体彩赔率.md` 提取北京时间和中文对阵。
4. 将 Polymarket UTC 时间换算为北京时间并校验。
5. 将 Polymarket 英文队名映射为中文队名。
6. 将 market 和 outcome 拆成 `类型`、`事件`。
7. 写入 `2026世界杯polymarket赔率.md`。

运行后关注终端输出：

```text
output_rows=...
time_match=...
skipped_non_match_h2h=...
```

解释：

- `output_rows` 是写入表格的数据行数。
- `time_match` 是 Polymarket 时间换算北京时间后与体彩模板一致的比赛数量。
- `skipped_non_match_h2h` 是跳过的非具体比赛球员 H2H 市场，通常可以接受。
- 如果出现 `time_mismatch:` 或大量 `unmatched:`，必须先检查队名映射、赛程时间或原始数据范围。

## 获取或刷新 Polymarket 原始数据

如果用户明确要求获取最新 Polymarket 数据，或现有 CSV 过旧，先刷新机器人数据。

无人值守自动化优先使用本项目封装脚本：

```bash
python3 scripts/refresh_polymarket_next_day.py
```

该脚本会：

1. 进入 `../world-cup-2026-arbitrage-trading-bot-main`。
2. 以 `LOCAL_DATA_ENABLED=true`、`LOCAL_DATA_FORMAT=csv`、`MARKET_SCOPE=matches` 运行机器人。
3. 向机器人标准输入发送 `quit`，避免长期停留在交互循环。
4. 获取并保存最新 `match-quotes` / `matches` CSV。
5. 回到本项目运行 `python3 scripts/generate_polymarket_odds.py --tomorrow`。

如果封装脚本失败，再按下面的手动方式排查。

进入机器人目录：

```bash
cd ../world-cup-2026-arbitrage-trading-bot-main
```

确认 `.env` 至少包含：

```text
LOCAL_DATA_ENABLED=true
LOCAL_DATA_DIR=./data
LOCAL_DATA_FORMAT=csv
MARKET_SCOPE=matches
MIN_MATCH_LIQUIDITY_USD=500
```

运行机器人：

```bash
npm start
```

机器人启动后会：

1. 从 Gamma API 获取世界杯比赛市场。
2. 从 CLOB API 获取当前盘口价格。
3. 将市场目录写入 `data/matches/`。
4. 将比赛报价写入 `data/match-quotes/`。

如果需要强制刷新，在机器人 CLI 中输入：

```text
markets
```

等待输出类似：

```text
Market state ready (... match rows, ... futures rows)
```

然后输入：

```text
quit
```

注意：刷新 Polymarket 数据需要联网。如果命令因网络沙箱失败，应按 Codex 权限规则使用 `require_escalated` 重新运行相同命令，请求用户批准网络访问。不要绕过审批。

## 写入目标表

刷新原始 CSV 后，回到本项目目录：

```bash
cd ../2026世界杯预测
python3 scripts/generate_polymarket_odds.py --tomorrow
```

目标表结构固定为 7 列：

```text
时间 | 比赛对阵双方 | 类型 | 事件 | Polymarket价格 | 赔率 | 交易量
```

表尾必须保留交易量单位说明：

```text
注：交易量来自 Polymarket 原始数据 `volume24h` 字段，单位为美元（USD），表示该 market 的 24 小时交易量。
```

## 价格和赔率口径

Polymarket 价格是 outcome 的买入价格，不是体彩小数赔率。转换公式：

```text
赔率 = 1 / Polymarket价格
```

示例：

```text
Polymarket价格 0.4100 -> 赔率 2.44
Polymarket价格 0.2500 -> 赔率 4.00
```

如果价格为空、无法解析或小于等于 0，价格和赔率写 `-`。

## 类型与事件映射

当前脚本会把 Polymarket market 拆成中文 `类型` 和 `事件`。

常见映射：

| Polymarket 来源 | 类型 | 事件 |
|---|---|---|
| 基础胜平负 | `胜负` | `胜`、`平`、`负` |
| Exact Score | `比分` | `2:1`、`0:0`、`其他比分` |
| Spread: Australia (-2.5) | `让负（-2.5）` | `澳大利亚` |
| O/U 2.5 | `全场大小球（2.5）` | `大`、`小` |
| Halftime Result | `半场胜负` | `胜`、`平`、`负` |
| Second Half Result | `下半场胜负` | `胜`、`平`、`负` |
| First Team to Score | `先进球球队` | 队名或 `无进球` |
| Total Corners: O/U 9.5 | `角球大小球（9.5）` | `大`、`小` |
| Team to Take First Corner | `首个角球球队` | 队名 |
| Player Props | `球员数据` | 例如 `Scott McTominay射门数+1` |

球员数据翻译规则：

| 英文字段 | 中文事件 |
|---|---|
| `goals` | `进球数` |
| `shots` | `射门数` |
| `shots on target` | `射正数` |
| `assists` | `助攻数` |
| `saves` | `扑救数` |
| `goals + assists` | `进球+助攻数` |

`No` outcome 行需要保留并在事件后加 `-否`，例如：

```text
Scott McTominay射门数+1
Scott McTominay射门数+1-否
```

## 队名和时间处理

不要直接信任机器人导出的 `homeTeam/awayTeam` 字段，因为扩展 market 会把后缀误拆成队名。例如：

```text
Australia vs. Türkiye - More Markets
```

错误拆分可能得到：

```text
homeTeam = Australia
awayTeam = Türkiye - More Markets
```

正确处理方式：

1. 先去掉事件后缀，如 `- More Markets`、`- Exact Score`、`- Total Corners`。
2. 得到基础对阵 `Australia vs. Türkiye`。
3. 再映射成中文 `澳大利亚 vs 土耳其`。
4. 用中文对阵去匹配 `2026世界杯体彩赔率.md` 中的比赛。

时间处理：

- Polymarket 的 `endDate` 是 UTC。
- 脚本会换算为北京时间 UTC+8。
- 输出第一列时间以 `2026世界杯体彩赔率.md` 为准。
- 换算结果只用于校验，不直接覆盖体彩模板时间。

## 校验步骤

每次生成后至少做这些检查：

```bash
python3 scripts/generate_polymarket_odds.py
```

确认没有 `time_mismatch`。

检查表格列数：

```bash
python3 - <<'PY'
from pathlib import Path
from collections import Counter
rows = []
for line in Path("2026世界杯polymarket赔率.md").read_text(encoding="utf-8").splitlines()[2:]:
    if line.startswith("| "):
        rows.append([c.strip() for c in line.strip().strip("|").split("|")])
print(Counter(len(row) for row in rows))
print("rows", len(rows))
PY
```

期望列数全部为 `7`。

检查交易量单位说明：

```bash
tail -n 3 2026世界杯polymarket赔率.md
```

应能看到 `volume24h` 和 `美元（USD）`。

## 常见问题

### 出现 unmatched

常见原因是 Polymarket 队名和本地中文队名没有映射。优先修改：

```text
scripts/generate_polymarket_odds.py
```

里的 `TEAM_EN_TO_ZH`。

### 价格为空

原始 `match-quotes` 的 `prices` 为空时，表中写 `-`。不要用 `0` 或 `0.00` 代替。

### 数据行数变化

这是正常现象。Polymarket market 会随时间开关、排序和流动性变化。只要时间校验通过、列数正确、重要类型存在，行数不需要固定。

如果使用 `--tomorrow`，行数只对应北京时间次日比赛，不会包含所有已发现比赛。

### 需要完整比分覆盖

当前机器人可能只保存部分 Exact Score market 的报价。若用户要求完整比分，需要修改机器人导出逻辑，使 `match-quotes` 完整保存所有 `Exact Score` market，而不是只保存每个事件前几个 market。
