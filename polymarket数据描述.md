# Polymarket 数据描述

## 目标

本文比较两类数据：

1. `2026世界杯体彩赔率.md`：项目内用于分析的标准赔率表结构。
2. `world-cup-2026-arbitrage-trading-bot-main/data/`：自动交易机器人从 Polymarket 获取并保存的世界杯市场数据。

当前目标是以 `2026世界杯体彩赔率.md` 的北京时间和中文对阵为基准，把 Polymarket 的各类 market 加工成 `2026世界杯polymarket赔率.md`。

## 当前 Polymarket 赔率表结构

`2026世界杯polymarket赔率.md` 当前使用 7 列：

| 字段 | 含义 |
|---|---|
| 时间 | 采用体彩模板中的北京时间；Polymarket UTC 时间换算到 UTC+8 后用于校验 |
| 比赛对阵双方 | 采用体彩模板中的中文对阵 |
| 类型 | Polymarket market 类型，例如 `胜负`、`让负（-2.5）`、`比分`、`球员数据` |
| 事件 | Polymarket outcome 或具体事件，例如 `胜`、`德国`、`3:0`、`Scott McTominay射门数+1` |
| Polymarket价格 | 对应 outcome 的 Polymarket best ask 价格 |
| 赔率 | 按 `1 / Polymarket价格` 折算的十进制赔率 |
| 交易量 | 来自 Polymarket 原始数据 `volume24h` 字段，单位为美元（USD） |

这样处理的原因是：Polymarket 的很多 market 是二项市场，一个 market 下会同时有 `Over/Under`、`Yes/No` 或两队 outcome。拆成 `类型` 和 `事件` 后，同一类玩法可以按事件清晰比较。

## 体彩赔率表结构

体彩赔率表是对照格式，字段固定：

| 字段 | 含义 |
|---|---|
| 时间 | 北京时间，例如 `2026-06-14（周日） 12:00` |
| 比赛对阵双方 | 本地中文队名，例如 `澳大利亚 vs 土耳其` |
| 类型 | 玩法类型，例如 `胜负`、`让负（0）`、`比分` |
| 事件 | 某个玩法下的具体结果，例如 `胜`、`平`、`负`、`2:1` |
| 赔率 | 十进制赔率；没有数据时写 `-` |

当前模板每场比赛主要包含三组玩法：

| 类型 | 事件集合 |
|---|---|
| 胜负 | `胜`、`平`、`负` |
| 让负（0） | `胜`、`平`、`负` |
| 比分 | `1:0`、`2:0`、`2:1`、...、`胜其他`、`平其他`、`负其他` |

## Polymarket 原始数据来源

机器人当前主要使用两类 Polymarket 接口：

| 来源 | 作用 | 当前本地文件 |
|---|---|---|
| Gamma API | 市场元数据：事件、标题、market id、玩法名称、流动性等 | `data/matches/*.csv`、`data/markets/*.csv` |
| CLOB API | 实时订单簿价格：某个 outcome token 的买卖价格 | `data/match-quotes/*.csv`、`data/quotes/*.csv` |

这两类数据不是互相替代关系：

- Gamma API 更像“市场目录”，告诉我们有哪些比赛、有哪些玩法、每个玩法叫什么。
- CLOB API 更像“当前盘口”，告诉我们现在买某个结果需要多少钱。
- 生成赔率表时，赔率应优先以 CLOB 的 best ask 为准，因为它代表当前可以买入 Yes 份额的价格。

## Polymarket 报价格式

`data/match-quotes/*.csv` 的字段示例：

| 字段 | 示例 | 含义 |
|---|---|---|
| event | `Australia vs. Türkiye` | Polymarket 事件标题 |
| homeTeam | `Australia` | 机器人解析出的主队 |
| awayTeam | `Türkiye` | 机器人解析出的客队 |
| market | `Australia` | 当前 market 的结果名称 |
| outcomes | `Yes\|No` | 该 market 的结果 token |
| prices | `0.5700\|0.4400` | Yes 和 No 的 best ask 价格 |
| sum | `1.01` | Yes ask + No ask |
| liquidity | `215269.2022` | 流动性 |
| status | `Near parity` | 机器人内部状态 |

Polymarket 的价格不是体彩小数赔率，而是概率型交易价格。转换成体彩式十进制赔率时，基本公式是：

```text
十进制赔率 = 1 / Polymarket Yes best ask
```

例如：

| Polymarket market | Yes 价格 | 转换后十进制赔率 |
|---|---:|---:|
| Australia | 0.5700 | 1.75 |
| Draw (Australia vs. Türkiye) | 0.3100 | 3.23 |
| Türkiye | 0.1300 | 7.69 |

No 价格通常不用于生成体彩式 `胜/平/负`，因为体彩表需要的是“这个结果发生”的赔率，对应 Polymarket 的 Yes。

## “多个市场”的含义

Polymarket 不是每场比赛只有一个三项盘口，而是把一场比赛拆成很多独立 market。

以 `Australia vs. Türkiye` 为例，可能存在这些事件标题：

| Polymarket event | 含义 | 是否用于当前体彩模板 |
|---|---|---|
| `Australia vs. Türkiye` | 主胜、平、客胜主市场 | 用于 `胜负` |
| `Australia vs. Türkiye - Exact Score` | 精确比分市场 | 部分用于 `比分` |
| `Australia vs. Türkiye - More Markets` | 大小球、让球、双方进球等扩展玩法 | 暂不直接用于当前模板 |
| `Australia vs. Türkiye - Total Corners` | 角球玩法 | 暂不用于当前模板 |

这里的“多个市场”不是多个报价源互相冲突，而是 Polymarket 为同一场比赛创建了多个不同玩法市场。选择哪个为准，取决于要填体彩表的哪一类行：

| 体彩类型 | 应选择的 Polymarket market |
|---|---|
| 胜负 | 基础事件：`Team A vs. Team B`，不带 `- More Markets` 等后缀 |
| 比分 | `Team A vs. Team B - Exact Score` |
| 让负（0） | 当前没有严格等价的三项让球胜平负 market，先保留 `-` |

## 数据清洗问题说明

机器人当前用一个简单规则解析比赛队名：只要标题里包含 `vs.`，就把 `vs.` 左边当主队，右边当客队。

这个规则对基础事件是正确的：

```text
Australia vs. Türkiye
```

解析结果：

```text
homeTeam = Australia
awayTeam = Türkiye
```

但对扩展市场就会出错：

```text
Australia vs. Türkiye - More Markets
```

机器人仍然按 `vs.` 拆分，得到：

```text
homeTeam = Australia
awayTeam = Türkiye - More Markets
```

这里 `Türkiye - More Markets` 显然不是球队名。`- More Markets` 是 Polymarket 的玩法分组后缀，不属于客队名称。

同类问题还会出现在：

```text
Australia vs. Türkiye - Exact Score
Australia vs. Türkiye - Total Corners
```

因此从 Polymarket 数据匹配本地赛程时，不能直接信任机器人导出的 `homeTeam/awayTeam` 字段。更稳妥的做法是先把事件标题归一化：

```text
Australia vs. Türkiye - More Markets  -> Australia vs. Türkiye
Australia vs. Türkiye - Exact Score   -> Australia vs. Türkiye
Australia vs. Türkiye - Total Corners -> Australia vs. Türkiye
```

然后再做队名映射：

```text
Australia -> 澳大利亚
Türkiye -> 土耳其
```

## 与体彩模板的可映射关系

### 胜负

可直接映射。

| 体彩事件 | Polymarket market | 使用价格 |
|---|---|---|
| 胜 | 主队名称 market，例如 `Australia` | Yes best ask |
| 平 | `Draw (Australia vs. Türkiye)` 或 `Draw` | Yes best ask |
| 负 | 客队名称 market，例如 `Türkiye` | Yes best ask |

转换后写入 `赔率`：

```text
赔率 = 1 / Yes best ask
```

### 比分

可部分映射。

Polymarket 的比分市场示例：

```text
Exact Score: Australia 2 - 1 Türkiye?
```

可映射为体彩事件：

```text
2:1
```

但有两个限制：

1. 当前 `match-quotes` 文件每个比赛的比分报价没有完整保存，很多比分只有市场目录，没有实时报价。
2. Polymarket 的 `Any Other Score` 是一个总合并项，不能直接拆成体彩的 `胜其他`、`平其他`、`负其他`。

因此后续生成赔率时，建议：

- 能明确解析为 `x:y` 且有 Yes 价格的比分，写入转换后赔率。
- `胜其他`、`平其他`、`负其他` 先写 `-`。
- 没有报价的比分写 `-`。

### 让负（0）

暂不直接映射。

体彩的 `让负（0）` 是三项结果：让球后的胜、平、负。

Polymarket 当前常见的是二项 spread market，例如：

```text
Spread: Australia (-2.5)
outcomes = Australia|Türkiye
```

这不是三项让球胜平负，且没有“让球后平局”这一项。为了避免错误对齐，`让负（0）` 暂时保持 `-`。

## 当前转换规则

当前由 `scripts/generate_polymarket_odds.py` 生成 `2026世界杯polymarket赔率.md`：

1. 从 `data/match-quotes/` 选择最新 CSV 作为价格来源。
2. 从 `2026世界杯体彩赔率.md` 抽取每场比赛的北京时间和中文对阵。
3. 将 Polymarket 的 UTC `endDate` 换算到北京时间，校验是否与体彩模板时间一致。
4. 将 Polymarket 事件标题归一化，例如把 `Australia vs. Türkiye - More Markets` 归一为 `Australia vs. Türkiye`，再映射到 `澳大利亚 vs 土耳其`。
5. 将每个 outcome 展开为独立行，第四列写事件，第五列写 Polymarket 价格，第六列写 `1 / 价格`，第七列写 `volume24h` 交易量。

主要类型映射：

| Polymarket market | 输出类型 | 输出事件示例 |
|---|---|---|
| 基础胜平负 | `胜负` | `胜`、`平`、`负` |
| Spread | `让负（-2.5）` | `澳大利亚` |
| Exact Score | `比分` | `2:1` |
| O/U | `全场大小球（2.5）` | `大`、`小` |
| Halftime Result | `半场胜负` | `胜` |
| Second Half Result | `下半场胜负` | `平` |
| First Team to Score | `先进球球队` | `德国`、`无进球` |
| Total Corners | `角球大小球（9.5）` | `大` |
| Player Props | `球员数据` | `Scott McTominay射门数+1` |

如果希望提高比分覆盖率，需要调整机器人导出逻辑：

- `matches` 导出时保留 `clobTokenIds`，便于后续重新拉取价格。
- `match-quotes` 不要只保存每个事件前几个 market，应完整保存 `Main 1X2`、`Exact Score` 和其他需要保留的玩法 market。
- 队名解析时区分基础事件和玩法后缀，避免把 `- More Markets` 当成队名。
