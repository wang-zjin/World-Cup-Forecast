---
name: sporttery-football-odds-fetch
description: 从中国体育彩票移动端足球计算器页面获取竞彩足球赔率，并更新项目内体彩赔率表 Markdown 文件。用于需要抓取或刷新体彩足球胜平负 HAD、让球胜平负 HHAD、比分 CRS 赔率，处理 m.sporttery.cn/mjc/jsq/zqspf/、webapi.sporttery.cn 接口、赔率缺失填充为 -、让球数写回表格等任务。
---

# 体彩足球赔率数据获取

## 核心原则

用体彩移动端足球计算器页面的数据更新项目里的体彩赔率表。优先使用 `体彩赔率.md`；如果不存在，就查找当前目录里的 `*体彩赔率.md`，例如 `2025世界杯体彩赔率.md`。执行时遵守这些规则：

- 本项目已有脚本 `scripts/update_sporttery_odds.py`；刷新赔率时优先运行脚本，不要重复手写抓取和写回逻辑
- `胜负` 使用接口里的 `had`，也就是体彩 `HAD`
- `让负（让球数）` 使用接口里的 `hhad`，也就是体彩 `HHAD`
- `比分` 使用接口里的 `crs`，也就是体彩 `CRS`
- 没有开售、接口缺失、历史比赛获取不到、单个事件没有赔率时，赔率写 `-`
- 不要用 `0.00` 表示缺失赔率
- 只更新用户指定日期或指定比赛，除非用户明确要求全表刷新
- 写回 `HHAD` 时，同时把类型里的让球数更新为接口的 `goalLine`

## 获取接口

用户通常会给出或提到页面：

```text
https://m.sporttery.cn/mjc/jsq/zqspf/
```

这个页面本身是 HTML 壳，真实数据由脚本请求接口。页面源码里的关键配置是：

```js
jsCommonDataV1.webApi = "//webapi.sporttery.cn"
```

关键脚本：

```text
https://static.sporttery.cn/res_1_0/jcwm/default/jc/jsq/dataTransfer.js
https://static.sporttery.cn/res_1_0/jcwm/default/jc/jsq/lotJs.js
https://static.sporttery.cn/res_1_0/common/js/commonV1.js
```

`dataTransfer.js` 的 `getJsqMatchDate(pool, stype)` 调用足球计算器接口。`commonV1.js` 里 `comDataChannel` 是 `c`，所以请求用：

```text
https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=had,hhad,crs
```

## 推荐请求命令

直接裸请求接口可能被 WAF 拦截。按页面 AJAX 行为带移动端 UA、Referer、Origin 和 `X-Requested-With`。

```bash
curl -L 'https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=had,hhad,crs' \
  -H 'User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1' \
  -H 'Accept: application/json, text/javascript, */*; q=0.01' \
  -H 'Referer: https://m.sporttery.cn/mjc/jsq/zqspf/' \
  -H 'Origin: https://m.sporttery.cn' \
  -H 'X-Requested-With: XMLHttpRequest' \
  -o /tmp/sporttery_calc.json
```

如果返回 HTML，且标题或正文包含 `WAF拦截页面`，说明请求头不完整或访问方式被拦截。优先补齐以上请求头后重试。

## 返回结构

接口返回 JSON。重点字段：

```text
value.lastUpdateTime
value.matchInfoList[].businessDate
value.matchInfoList[].subMatchList[]
```

单场比赛常用字段：

```text
matchDate          比赛日期；本项目按北京时间日期匹配
matchTime          开球时间
matchNumStr        竞彩编号，如 周日011
leagueAbbName      赛事名
homeTeamAbbName    主队简称
awayTeamAbbName    客队简称
poolList           开售玩法列表
had                胜平负赔率对象，可能是 {}
hhad               让球胜平负赔率对象，含 goalLine
crs                比分赔率对象
```

匹配赔率表时优先使用：

```text
{matchDate} + {matchTime前5位} + "{homeTeamAbbName} vs {awayTeamAbbName}"
```

如果本地赛程与体彩队名存在译名差异，先建立队名映射，再写回。

## 胜负和让球映射

`had` 和 `hhad` 的事件字段一致：

```text
胜 -> h
平 -> d
负 -> a
```

`hhad.goalLine` 是让球数。写回时把类型更新为：

```text
让负（{goalLine}）
```

例子：

```text
让负（-3）
让负（+1）
```

如果 `had` 是 `{}`，说明该场胜负玩法没有可用赔率，对应三行写 `-`。

## 比分事件

体彩 `CRS` 比分玩法不是 `0:0` 到 `5:5` 的全矩阵，而是固定 31 个事件。所有场次使用同一套选项：

```text
主胜：
1:0、2:0、2:1、3:0、3:1、3:2、4:0、4:1、4:2、5:0、5:1、5:2、胜其他

平局：
0:0、1:1、2:2、3:3、平其他

客胜：
0:1、0:2、1:2、0:3、1:3、2:3、0:4、1:4、2:4、0:5、1:5、2:5、负其他
```

因此：

- `3:4`、`3:5` 归入 `负其他`
- `4:3`、`5:3` 归入 `胜其他`
- `4:4`、`5:5` 归入 `平其他`
- `6:0`、`0:6` 等分别归入 `胜其他` / `负其他`

## 比分字段映射

`crs` 字段到本地 `事件` 的映射：

```text
1:0 -> s01s00
2:0 -> s02s00
2:1 -> s02s01
3:0 -> s03s00
3:1 -> s03s01
3:2 -> s03s02
4:0 -> s04s00
4:1 -> s04s01
4:2 -> s04s02
5:0 -> s05s00
5:1 -> s05s01
5:2 -> s05s02
胜其他 -> s1sh

0:0 -> s00s00
1:1 -> s01s01
2:2 -> s02s02
3:3 -> s03s03
平其他 -> s1sd

0:1 -> s00s01
0:2 -> s00s02
1:2 -> s01s02
0:3 -> s00s03
1:3 -> s01s03
2:3 -> s02s03
0:4 -> s00s04
1:4 -> s01s04
2:4 -> s02s04
0:5 -> s00s05
1:5 -> s01s05
2:5 -> s02s05
负其他 -> s1sa
```

若某个比分字段不存在、为空字符串，或 `crs` 为空对象，写 `-`。

## 写回流程

1. 先请求接口并保存到 `/tmp/sporttery_calc.json`。
2. 读取赔率表 Markdown 文件，保留表头和非目标行。
3. 对目标日期或目标比赛进行匹配。
4. 按 `HAD`、`HHAD`、`CRS` 映射写赔率。
5. 找不到比赛或玩法时写 `-`。
6. 写回后检查不应再出现 `0.00`：

```bash
rg -n '\| 0\.00 \|' <赔率表文件.md>
```

## 参考脚本片段

以下片段假设接口 JSON 已保存到 `/tmp/sporttery_calc.json`，当前目录有赔率表 Markdown 文件。按指定日期更新时，先判断 `time_text.startswith("YYYY-MM-DD")`，避免误改其他日期。

```python
from pathlib import Path
import json

md_path = Path("体彩赔率.md")
if not md_path.exists():
    matches = sorted(Path(".").glob("*体彩赔率.md"))
    if not matches:
        raise FileNotFoundError("未找到 体彩赔率.md 或 *体彩赔率.md")
    md_path = matches[0]
data = json.loads(Path("/tmp/sporttery_calc.json").read_text(encoding="utf-8"))

result_key = {"胜": "h", "平": "d", "负": "a"}
score_key = {
    "1:0": "s01s00", "2:0": "s02s00", "2:1": "s02s01",
    "3:0": "s03s00", "3:1": "s03s01", "3:2": "s03s02",
    "4:0": "s04s00", "4:1": "s04s01", "4:2": "s04s02",
    "5:0": "s05s00", "5:1": "s05s01", "5:2": "s05s02",
    "胜其他": "s1sh",
    "0:0": "s00s00", "1:1": "s01s01", "2:2": "s02s02",
    "3:3": "s03s03", "平其他": "s1sd",
    "0:1": "s00s01", "0:2": "s00s02", "1:2": "s01s02",
    "0:3": "s00s03", "1:3": "s01s03", "2:3": "s02s03",
    "0:4": "s00s04", "1:4": "s01s04", "2:4": "s02s04",
    "0:5": "s00s05", "1:5": "s01s05", "2:5": "s02s05",
    "负其他": "s1sa",
}

api_matches = {}
for group in data["value"]["matchInfoList"]:
    for match in group.get("subMatchList", []):
        matchup = f'{match["homeTeamAbbName"]} vs {match["awayTeamAbbName"]}'
        key = (match["matchDate"], match["matchTime"][:5], matchup)
        api_matches[key] = match

new_lines = []
for line in md_path.read_text(encoding="utf-8").splitlines():
    if not line.startswith("| ") or line.startswith("| 时间 ") or line.startswith("|---"):
        new_lines.append(line)
        continue

    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) != 5:
        new_lines.append(line)
        continue

    time_text, matchup, typ, event, old_odd = cells
    date = time_text.split("（", 1)[0]
    kick_time = time_text.rsplit(" ", 1)[-1]
    match = api_matches.get((date, kick_time, matchup))

    odd = "-"
    new_typ = typ
    if match and typ == "胜负":
        pool = match.get("had") or {}
        odd = pool.get(result_key.get(event, ""), "-") or "-"
    elif match and typ.startswith("让负（"):
        pool = match.get("hhad") or {}
        if pool:
            new_typ = f'让负（{pool.get("goalLine") or "0"}）'
            odd = pool.get(result_key.get(event, ""), "-") or "-"
    elif match and typ == "比分":
        pool = match.get("crs") or {}
        odd = pool.get(score_key.get(event, ""), "-") or "-"

    new_lines.append(f"| {time_text} | {matchup} | {new_typ} | {event} | {odd} |")

md_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
```
