---
name: sanctions-screening
description: 全球制裁筛查智能引擎。对公司/个人进行全球制裁匹配、出口管制检查，聚合初筛覆盖 100+ 名单，官方核验覆盖 13 个源（含中国 3 个），生成含证据截图合规报告。默认执行变体确认；若用户明确给出一个或多个名称并要求只按这些名称搜索，则必须严格使用该名称集合，禁止自动扩展变体。
---

# 全球制裁筛查智能引擎

## 架构概览

```
三层筛查 + 法律影响分析 + 专业 PDF 报告

用户输入实体名
      │
      ▼
┌─────────────────┐
│ 第一层：初筛      │ ← OpenSanctions API (/match 端点)
│ (第三方聚合)      │    覆盖 100+ 全球制裁名单
│                  │    ⚠️ 无 API key 时降级为网页抓取，须显式标注
└────────┬────────┘
         │ 命中？
    ┌────┴────┐
    │ 否      │ 是
    ▼         ▼
  清除    ┌────────────────┐
          │ 第二层：官方校验  │ ← 浏览器直连 13 个官方源
          │ (政府官网)       │    10 个国际源 + 中国 3 源
          │                 │    截图取证 + 时间戳 + 覆盖率披露
          └────────┬───────┘
                   ▼
          ┌────────────────┐
          │ 第三层：情报搜索  │ ← Tavily API（可选，无 key 则跳过并标注）
          └────────┬───────┘
                   ▼
          ┌────────────────┐
          │ 法律影响分析     │ ← 参见 references/legal_implications.md
          └────────┬───────┘
                   ▼
          ┌────────────────┐
          │ PDF 专业报告     │ ← scripts/generate_report.py (Edge Headless)
          └────────────────┘
```

> **关键原则：** OpenSanctions 是第三方聚合数据，仅用于初筛。即使初筛未命中，也会生成官方校验计划（防止 API 故障导致漏报）。

## 🔴🔴🔴 强制执行工作流（含路径确认） 🔴🔴🔴

> **最高优先级指令：** 当用户要求对某实体进行制裁筛查时，**必须先完成「阶段 0：路径确认」，获得用户明确回复后，才能执行任何脚本。禁止跳过路径确认直接运行命令。** 路径确认完毕后，阶段 1-4 必须全部自动执行，不得中途停下来询问用户。

### 阶段 0：意图识别 + 搜索模式确认 ⛔ 必须先完成，禁止跳过

**这是整个工作流中唯一一次交互暂停点。在用户回复之前，禁止执行任何 python 命令。**

收到筛查请求后，**你自己先做两件事**：

**（1）判断实体类型**：根据用户输入的名称，用你的知识判断是公司还是个人。

**（2）先判断搜索模式，再决定是否生成变体**：

#### 模式 A：全量变体模式（默认）

如果用户只是说“筛查华为 / Huawei / 大疆 / DJI”，**没有明确限制只搜一个字符串**，则进入默认模式：自动生成搜索变体。**🔴 你必须覆盖以下所有类别，通常至少生成 8-10 个变体。禁止只生成 2-3 个就交差。**（部分网站对大小写敏感，所以大小写变体必不可少）：

- 英文简称，**含大小写变体**（如 "DJI"、"dji"、"Dji"）
- 英文全名（如 "SZ DJI Technology Co., Ltd."）
- 英文通用名（如 "DJI Technology"）
- 中文全名（如 "深圳市大疆创新科技有限公司"）
- 中文简称（如 "大疆"、"大疆创新"）
- 拼音/音译变体（如 "dajiang"）
- 常见缩写（如 "HW" 代表华为）
- 已知重要子公司（如 "DJI Europe B.V."、"HiSilicon"）

然后**向用户展示变体列表 + 自然语言说明，请求确认**：

> 🏢 我判断 **「<用户输入>」** 是 **公司/法人实体**（如果判断错了请纠正）。
>
> 🔍 我拟使用以下变体对 13 个官方源（10 个国际源 + 中国 3 源）进行全量筛查。每个变体都会分别在各网站搜索框搜索并截图取证，因为部分制裁网站对大小写敏感，所以会覆盖大写、小写、首字母大写等多种形式。
>
> | # | 搜索变体 | 说明 |
> |---|---------|------|
> | 1 | DJI | 英文简称-大写（主搜索词） |
> | 2 | dji | 英文简称-小写 |
> | 3 | SZ DJI Technology Co., Ltd. | 英文注册全名 |
> | 4 | DJI Technology | 英文通用名 |
> | 5 | 大疆 | 中文简称 |
> | 6 | 大疆创新 | 中文常用名 |
> | 7 | 深圳市大疆创新科技有限公司 | 中文注册全名 |
> | 8 | dajiang | 拼音变体 |
> | 9 | DJI Europe B.V. | 已知子公司 |
>
> 📂 报告默认输出：
> - PDF（最终交付）：`<Skill根目录>/<实体名>_sanctions_report.pdf`
> - HTML（在线浏览）：`<Skill根目录>/<实体名>_sanctions_report.html`
>
> 请确认：回复**「确认」**开始筛查。你也可以告诉我需要**增加**或**删除**哪些变体（增加的变体同样会走完整筛查流程）。

#### 模式 B：用户指定名称集合模式（用户明确要求时）

如果用户明确表达以下意思之一，**必须进入“用户指定名称集合模式”，禁止自动生成变体，禁止再展示 8-10 个变体表**：

- “只筛这个名字”
- “只筛这几个名字”
- “单个名称 / 两个名称 / 三个名称 / 四个名称 / 多个名称”
- “exact name / exact string / exact names”
- “不要变体”
- “仅搜索 `huawei`”
- “只查 `DJI` 和 `dji`，不要扩展”
- “只查这 4 个名字，不要自动补全”

此时你只能使用用户明确给定的名称集合进行搜索，**不能自动补全全名、中文名、拼音、大小写变体、子公司**。用户给 1 个就搜 1 个，给 2 个就搜 2 个，给 N 个就搜 N 个。

此时请改为发送下面这种确认文案：

> 🏢 我判断 **「<用户输入>」** 是 **公司/法人实体**（如果判断错了请纠正）。
>
> 🔍 你要求执行**用户指定名称集合筛查**。我将只使用你明确给出的这些名称在全部制裁源中搜索，**不会自动扩展任何变体**（不补全全名、别名、中文名、拼音、子公司或大小写变体）。
>
> 本次将使用的名称集合（1-N 个，严格按你提供的列表执行）：
> | # | 名称 |
> |---|------|
> | 1 | `<名称1>` |
> | 2 | `<名称2>` |
> | 3 | `<名称3>` |
> | 4 | `<名称4>` |
>
> 📂 报告默认输出：
> - PDF（最终交付）：`<Skill根目录>/<实体名>_sanctions_report.pdf`
> - HTML（在线浏览）：`<Skill根目录>/<实体名>_sanctions_report.html`
>
> 请确认：回复**「确认」**开始按上述**名称集合**执行筛查；如果你希望改回全量变体模式，请直接说明。

**等待用户回复后**，你需要做以下准备（对全流程生效）：

```
实体类型  → 公司 → screen_entity 用 --type LegalEntity，browser_verify 用 --type Entity
         → 个人 → screen_entity 用 --type Person，browser_verify 用 --type Individual

搜索策略  → 全量变体模式：变体列表中第一个英文简称作为主搜索词（--entity 参数）
         → 全量变体模式：其余所有变体通过 --extra-variants 传入
         → 全量变体模式：🔄 脚本会在每个源上，用每个变体分别搜索+截图+取证（全量覆盖）
         → 用户指定名称集合模式：若只有 1 个名称，给 screen_entity.py 和 browser_verify.py 都加 --exact-name-only
         → 用户指定名称集合模式：若有 2-N 个名称，主名称作为 entity，其余名称通过 --extra-variants 传入
         → 用户指定名称集合模式：只搜索用户明确给出的名称集合，不自动生成任何变体
         → 每源结果自动合并：最严重状态优先，截图和匹配记录汇总

报告路径  → 默认路径，或用户指定的路径
```

> 🔴 **严格执行用户确认的变体列表**：如果用户回复中修改了变体列表（删除某些、只保留某些、或新增某些），你**必须严格按用户确认后的最终列表执行**，不要自行补充被用户删掉的变体（如全名）。用户说"只看 DJI 和 dji"，那就只传 DJI 和 dji，不要偷偷加回全名。

---

### 阶段 1：API 初筛（自动）

从 Skill 根目录运行：

```bash
# screen_entity.py 用官方全名 + LegalEntity/Person
python scripts/screen_entity.py "<官方全名>" --type <LegalEntity|Person> -o <report>.md

# 用户只给 1 个名称
python scripts/screen_entity.py "huawei" --type LegalEntity --exact-name-only -o <report>.md

# 用户明确只给 2 个名称
python scripts/screen_entity.py "Huawei" --type LegalEntity --extra-variants "huawei" -o <report>.md

# 用户明确给 4 个名称
python scripts/screen_entity.py "Huawei" --type LegalEntity --extra-variants "huawei" "HUAWEI" "Huawei Technologies" -o <report>.md
```

此步骤自动执行：OpenSanctions 初筛 + Tavily 情报搜索（可选）+ 生成浏览器校验计划。

**关于 `--no-intel`**：Tavily 是**付费**情报源，自 v1.1 起为**可选增强**，不再是硬性依赖。
- **已配置 `TAVILY_API_KEY(S)`**：默认执行情报搜索，不要加 `--no-intel`；控制成本用 `--intel-rounds`（见阶段 2）。
- **未配置 key**（多数零成本场景）：脚本自动跳过并写 `available=False / error=tavily_not_configured`，**不会中断**；此时无需手动加 `--no-intel`，但报告中必须写明「情报维度未查」。
- 只有在用户明确要求「不做情报搜索」时才主动加 `--no-intel`。

> ⚠️ **报告纪律**：情报维度不可用时，风险评分第 8 维会标 `assessed=False` 并注明「0 分表示未查，非无风险」。
> **禁止把「未查」写成「无风险」。**

**完成后立即进入阶段 2，不要停下来向用户报告中间结果。**

### 阶段 2：官方网站浏览器校验（自动）

> 🚫 **禁止自己写浏览器脚本。** 直接运行已封装好的 `scripts/browser_verify.py`。
> 🚫 **禁止裁减源列表。** 不要加 `--sources` 参数，必须一次性跑完全部 13 个官方源。

**命令模板（❗ 注意：主搜索词是最短常用名，不是全名）**：

```bash
# 公司示例（用户输入"大疆"或"dji"）
# ❗ --entity 用最短常用名 "DJI"（会在每个网站搜索框搜索）
# ❗ --extra-variants 只放用户确认的变体
# ❗ --api-report 传入阶段 1 的报告路径（嵌入 OpenSanctions 交叉验证）
python scripts/browser_verify.py "DJI" \
  --type Entity \
  --extra-variants "dji" \
  -o screenshots/ \
  --report DJI_sanctions_report.html \
  --api-report DJI_api_report.md

# 用户只给 1 个名称
python scripts/browser_verify.py "huawei" \
  --type Entity \
  --exact-name-only \
  -o screenshots/ \
  --report huawei_sanctions_report.html \
  --api-report huawei_api_report.md

# 用户明确只给 2 个名称
python scripts/browser_verify.py "Huawei" \
  --type Entity \
  --extra-variants "huawei" \
  -o screenshots/ \
  --report Huawei_sanctions_report.html \
  --api-report Huawei_api_report.md

# 用户明确给 4 个名称
python scripts/browser_verify.py "Huawei" \
  --type Entity \
  --extra-variants "huawei" "HUAWEI" "Huawei Technologies" \
  -o screenshots/ \
  --report Huawei_sanctions_report.html \
  --api-report Huawei_api_report.md

# 个人示例（用户输入"张三"）
python scripts/browser_verify.py "Zhang San" \
  --type Individual \
  --extra-variants "张三" "ZHANG SAN" "zhang san" \
  -o screenshots/ \
  --report ZhangSan_sanctions_report.html \
  --api-report ZhangSan_api_report.md
```

**关键说明**：
- **第一个参数（entity）用最短常用英文名**（如 "DJI"、"Huawei"）
- 默认模式下，`--extra-variants` — 官方全名/中文名/其他别名。🔄 **每个变体都会在每个源上全量搜索+截图+取证**
- 用户指定名称集合模式下，`--extra-variants` — 用户明确给出的其余名称（可传 1-N 个）；**只搜索用户给出的列表，不自动扩展**
- `--exact-name-only` — 只搜索输入的单个名称，**禁止自动扩展任何变体**
- `--type Entity|Individual` — 对应阶段 0 用户回答
- `--report <路径>` — 直接输出 HTML 报告
- `--stealth` — 反检测伪装，**默认关闭**。仅在目标站点因反爬拦截、且你已确认该行为符合站点条款与内部合规要求时才加；启用时终端会打印合规提示
- `--intel-rounds minimal|standard|full` — 情报轮次预算（默认 `standard`）
  - `minimal` = 1 轮（仅 general_sanctions），成本最低
  - `standard` = 4 轮（含 OFAC / 欧盟 / 出口管制），**默认**，成本约为原来的 57%
  - `full` = 7 轮，与改造前行为一致，成本最高
- `--no-pdf` — 跳过 PDF 渲染，只出 HTML

> ⚠️ **重要：不要加 `--sources` 参数！** 默认会自动校验全部 13 个官方源：
> 国际 10 源 `ofac` `bis_csl` `dod_1260h` `sam` `fcc` `uk` `un` `eu` `au` `ca`
> ＋ 中国 3 源 `cn_ucl`（不可靠实体清单）`cn_control`（出口管制管控名单）`cn_counter`（反制清单）。

此脚本自动完成：
- 访问全部 13 个官方源；默认模式下用**全名 + 所有变体**逐一搜索
- 若启用“用户指定名称集合模式”，则**只搜索用户明确给出的 1-N 个名称**，无论是 1 个、2 个、4 个还是更多
- **中国源自动翻页**：列表页按 layui 分页逐页翻到底，返回 `meta{pages_visited,total_items,exhausted}` 并在报告中披露覆盖率
- **分页截图**：如果搜索结果有多页，逐页翻页截图
- **PDF 逐页截图 + 精确高亮**：DoD 1260H 等 PDF 名单，逐页 PageDown + PyMuPDF 黄色高亮
- **XLSX 下载分析**：澳大利亚 DFAT 名单下载 XLSX，pandas 分析后渲染截图
- 截图取证并保存到指定目录
- 输出汇总结果表（命中/清除/需人工确认）

> 🔴 **只给中文名时的强制动作**：中国官方公告以「中文名（English Name）」并列列名，
> 且**同一实体在不同批次公告中中文译名可能不同**（英文名相对稳定）。
> 若 `--exact-name-only` 且名称只含中文，脚本会打印 `[WARN]` 并拒绝给出 clean 结论。
> 你必须向用户追问英文名或注册全名，再用 `--extra-variants` 补入后重跑。

**首次使用需安装依赖：**

```bash
pip install -r requirements.txt
```

> 💾 **磁盘空间紧张时（如 C 盘不足）**：不要下载 Chromium（约 400MB）。
> 脚本默认走系统已安装的 Chrome（`channel="chrome"`），只需 `pip install -r requirements.txt`。
> 若要把依赖装到非系统盘：
> ```bash
> python -m venv D:/AAAWorkBuddy/tools/screening-venv
> D:/AAAWorkBuddy/tools/screening-venv/Scripts/pip install -r requirements.txt
> set PIP_CACHE_DIR=D:/AAAWorkBuddy/tmp/pip-cache
> set PLAYWRIGHT_BROWSERS_PATH=D:/AAAWorkBuddy/tools/playwright-browsers
> set TMPDIR=D:/AAAWorkBuddy/tmp
> ```

**完成后立即进入阶段 2.5，不要停下来向用户报告中间结果。**

### 阶段 2.2：结果状态怎么读（禁止望文生义）

脚本对每个源输出一个 `status`。**只有 `hit` 和 `clean` 可以直接下结论**，其余都是「待处理」：

| status | 含义 | 报告里必须怎么写 |
|--------|------|------------------|
| `hit` | 在名单页检索到匹配记录 | 命中，进入误中审查 |
| `clean` | 名单完整加载且已比对，未发现匹配 | 未命中（**须同时披露覆盖率**） |
| `manual_review` | 程序性失败：解析失败 / 页面未加载 / 覆盖率不足 / 列表未翻完 | **「未能完成核查」**，不得写成「未列名」 |
| `error` | 请求层失败（超时、被拒绝、网络错误） | **「核查失败」**，须重跑或改人工 |
| `pending` | 本次未执行该源 | 未查，非无风险 |

> 🔴 **合规红线**：`manual_review` 与 `clean` 的法律后果完全不同。
> 把「没查成」写成「查了没有」，是本项目此前最严重的一类缺陷，已在 v1.1 修复，**不得回退**。
> 汇总时若任一源为 `manual_review`/`error`，最终结论不得写「全部未命中」，必须写「N 个源已完成，M 个源未完成」。

**中国 3 源的额外披露要求**（`cn_ucl` / `cn_control` / `cn_counter`）：

- 报告须写明**翻了几页、该栏目共几条、是否翻到底**（`pages_visited` / `total_items` / `exhausted`）。
- 公告打开率低于 **`CN_MIN_COVERAGE = 0.6`** 时，脚本判定 `manual_review`，不判 clean。
- 中国清单以「商务部公告 / 商务部令」逐次发布，**不提供统一在线查询库**，因此走「栏目列表页 → 定位公告 → 进正文 → 全文比对 → 截图留痕」路径；栏目结构改版会导致漏检，须人工复核兜底。

### 阶段 2.5：误中审查（自动，禁止跳过）

> 🔴🔴🔴 **此步骤必须在撰写报告之前完成，绝对禁止跳过。**
> 跳过此步骤将导致报告包含大量虚假命中，质量不可接受。

**你必须按顺序执行以下操作**：

**步骤 1**：读取审查指南（完整的判断规则在这里）：
```
read_file: references/false_positive_check.md
```

**步骤 2**：**无需手动操作** — FP 分析已在 `browser_verify.py` 报告生成阶段**自动完成**：
1. 报告生成时自动调用 OpenSanctions API 获取交叉验证数据
2. 核心清单（ofac, bis_csl, dod_1260h, sam, uk, un）的 HIT → **一律标记为确认命中**
3. 泛化清单（eu, au, ca）的 HIT 自动检查：
   - ISO 国家代码碰撞 →  疑似误中
   - OpenSanctions 无该清单佐证 → 🟡 疑似误中
   - EU regime 列表泛化 →  疑似误中
4. 报告 HTML 中的侧栏图标、汇总表、源 section 标题、Evidence List badge、Executive Summary 数字**全部自动更新**

> ⚠️ **禁止**：不要手动写脚本修改报告 HTML 中的 badge/icon/数字。所有 FP 标记已在生成阶段完成。
> 如需调整判断规则，修改 `browser_verify.py` 中的 `SOURCE_TO_OS_DATASETS`、`CORE_HIT_SOURCES`、`FP_CANDIDATE_SOURCES` 配置。

**步骤 3**：检查报告中的判断结果，如有异议直接反馈给用户。

**完成后立即进入阶段 3。**


### 阶段 3：撰写完整证据报告（自动）

将阶段 1、阶段 2、**阶段 2.5 的误中判断**全部整合为一份完整的 Markdown 证据报告。报告必须包含：

1. **报告头部**：筛查编号、时间、实体信息、风险评级
2. **法律影响分析**：读取 `references/legal_implications.md` 模板，针对每个**确认命中**的清单给出法律影响
3. **OpenSanctions 初筛结果**：匹配实体、命中名单、匹配分数
4. **官方校验证据**：每个源的校验结果 + 截图内嵌 + **🟡 误中标注**（如有）
5. **情报摘要**：Tavily 新闻搜索结果（不可用则写明「情报维度未查」及原因）
6. **管理层底线结论**：参考 `references/legal_implications.md` 中的 Executive Action Plan 模板
7. **证据清单**：所有截图和数据文件的汇总表

**完成后立即进入阶段 4，不要停下来向用户报告中间结果。**

### 阶段 4：交付最终报告（自动）

`browser_verify.py` 已自动生成专业 HTML 报告和 PDF 报告（PDF 默认始终生成，可用 `--no-pdf` 跳过）。

**报告交付时，你必须在回复中附上以下内容（不可省略）**：

1. **PDF 报告文件路径**（最终交付物，自动通过 Playwright 从 HTML 渲染，含页眉页脚页码）
2. **HTML 报告文件路径**（在线浏览版，含侧栏目录、截图、交叉验证、误中标注）

3. **🧠 误中分析结论**（阶段 2.5 结果）——逐个 HIT 列出判断和理由：

```markdown
## 🧠 AI 合规分析师误中判断

| 源 | 判断 | 理由 |
|----|------|------|
| [源名] | 🔴 确认命中 | [命中记录全名与搜索实体一致的证据] |
| [源名] | 🟡 疑似误中 | [具体误中模式：如 ISO 碰撞/类型不匹配/部分匹配] |
```

3. **综合风险等级**（排除疑似误中后的判断）
4. **建议**

## 环境与依赖

### 依赖安装

```bash
pip install -r requirements.txt
```

- `markdown` — 报告渲染
- `playwright` — 官网核验与 PDF 渲染（**默认复用系统 Chrome，不下载 Chromium**）
- `pandas` / `openpyxl` — 澳大利亚 DFAT XLSX 名单分析（可选）
- `pymupdf` (fitz) — DoD 1260H 等 PDF 名单的高亮与文本抽取（按需导入）

> 未装 `playwright` 时，`browser_verify.py` 仍可运行到「需要浏览器」的步骤才报错——
> 依赖是**惰性导入**的，不会在模块加载阶段整条链路崩掉。

### 工作目录

**所有脚本必须从项目根目录执行**（`scripts/` 之间有相互导入依赖）。

### API Key（两个都是可选的）

在根目录创建 `.env`（可参考 `.env.example`），脚本启动时由 `env_loader` 自动加载。

| 变量 | 作用 | 缺失时的行为 |
|------|------|--------------|
| `OPENSANCTIONS_API_KEY` | 聚合初筛 `/match`、`/search` | `/match`、`/search` 返 401 → 自动降级为**网页抓取**（`scrape_opensanctions`），初筛维度仍可出结果，但**必须标注数据来源为网页抓取** |
| `TAVILY_API_KEYS`（逗号分隔，配额轮换）/ `TAVILY_API_KEY` | 第三层情报 | 跳过，`available=False`，风险评分第 8 维标 `assessed=False` |

> 💰 **零成本可用**：两个 key 都不配也能跑完 13 源的官网核验，只是第一层降级、第三层缺失。
> 缺哪个维度就在报告里写明哪个维度未查，**禁止静默当作「无风险」**。

## 法律影响分析

对每个命中清单，报告中必须包含法律影响分析和咨询建议。模板参见 `references/legal_implications.md`。

结构：
1. **法律影响 (Legal Impact):** 引用具体法规/行政令号
2. **审批政策 (Licensing Policy):** 推定拒绝/逐案审查等
3. **咨询建议 (Advisory):** 面向客户的具体行动建议
4. **管理层底线结论 (Executive Action Plan):** 总结性决策建议

## 中文实体名处理

中文实体名在**默认模式**下须自动转换为多种搜索变体：
- 中文原名 → 拼音变体 → 英文商号 → 常用别名
- 例：`大疆` → `dji`, `SZ DJI Technology`, `Da Jiang Innovations`

变体生成逻辑在 `scripts/fuzzy_match.py` 中实现。若用户明确要求只按给定名称集合筛查，则**不要调用这套自动扩展逻辑**。

## 能力边界（不得越界承诺）

1. **只覆盖 13 个源**。不在列表内的中国管制物项清单（如《两用物项出口管制清单》本身、军品出口管理清单）、以及各国行业性许可名单（如 EAR 实体清单未涵盖的许可例外），**不在本工具核查范围内**，须另行检索。
2. **不能判定物项是否受管制**。本工具做的是**主体名称比对**；两用物项/ECCN 归类、最终用途与最终用户判断不在能力内。
3. **「未列入」不等于「不受限制」**。存在间接持股、50% 规则、穿透式管制、以及尚未公开的限制措施——工具看不到未公开信息。
4. **不做批量定性**。模糊匹配命中的候选只做**标记**，是否同一主体须人工按 ≥2 项身份要素交叉核验（统一社会信用代码 / 注册号 / LEI / 税号、注册地、成立日期、经营范围、集团结构）。
5. **不构成法律意见**。输出是事实整理与风险线索，最终结论由持牌律师出具。

## 基础假设（使用前先确认成立）

- **假设 1**：用户提供的名称是**可检索的正式名称或常用商号**；纯中文名且无英文名时，中国源命中率显著下降（见阶段 2 的强制追问规则）。
- **假设 2**：目标站点**当前可访问且结构未改版**。政务站点节流、改版、验证码都会导致 `manual_review`；此时应改用人工检索而非反复重跑。
- **假设 3**：筛查结论**仅在报告载明的检索时点成立**。制裁名单持续更新，报告建议有效期不超过 7 天；对持续交易应建立复核节奏。

**建议复核节奏**：首次筛查 → 交易前复核 → 名单重大更新后复核 → 长期合作方季度复核。每次复核都重新出报告并归档，不要复用旧截图。

## 与 `cn-sanctions-retrieval-skill` 的交接

两个工具分工不同，**互补而非替代**：

| | 本工具（screening） | `cn-sanctions-retrieval-skill` |
|---|---|---|
| 干什么 | 13 源**自动化**批量核验，出带截图的报告 | 中国 4 张无接口清单的**人工检索** SOP |
| 适用 | 名称明确、可走搜索框/列表页的源 | 无查询接口、须逐条翻公告比对 |
| 产物 | HTML/PDF 报告 + 截图 | 核查记录 + 留痕模板 |

**交接触发条件**：当本工具出现以下任一情况时，转人工检索 skill 处理，并在报告中写明「已转人工核查」：

- 中国 3 源任一支返回 `manual_review` 或 `error`；
- 出现模糊命中待定性（同名/近名）；
- 需要核查的实体不在 13 源覆盖范围内（如恶意实体清单等尚未纳入自动化的清单）。

## 报告解读

### 风险等级

| 评分 | 等级 | 含义 | 行动建议 |
|------|------|------|---------|
| 56-80 | 🛑 CRITICAL | 极高风险 | 立即停止交易 |
| 36-55 | 🔴 HIGH | 高风险 | 升级审批 + 法律意见 |
| 20-35 | 🟠 MEDIUM | 中风险 | 增强尽职调查 |
| 8-19 | 🟡 LOW | 低风险 | 持续监控 |
| 0-7 | 🟢 CLEAR | 清除 | 正常推进 |

## 参考文档

详细信息参见 `references/` 目录：

- **us_lists_coverage.md** — 美国 5 大核心清单 + 13 个 CSL 子清单完整覆盖指南
- **legal_implications.md** — 各清单法律影响分析模板和咨询意见模板
- **country_risk_tiers.md** — 国家/地区风险分级表
- **red_flags_checklist.md** — BIS "Know Your Customer" 红旗检查清单
- **false_positive_check.md** — 误中判定规则（阶段 2.5 必读）
- **cn_entity_false_positive.md** — 中文实体七类特有误报模式（高频字号撞名、简繁异体、音译分歧、中资离岸架构、母子公司混同等）

## 脚本索引

| 脚本 | 用途 |
|------|------|
| `browser_verify.py` | **主入口**。13 源官网自动化核验 + 截图 + HTML/PDF 报告 |
| `screen_entity.py` | 阶段 1 筛查引擎（初筛 + 情报 + 生成核验计划） |
| `risk_scorer.py` | 8 维度风险评分引擎（未查维度标 `assessed=False`） |
| `opensanctions_search.py` | OpenSanctions API 查询（含网页抓取兜底） |
| `tavily_search.py` | Tavily 情报搜索（可选依赖，无 key 优雅降级） |
| `report_assets.py` | 报告渲染的 CSS/JS 静态资源（自 `browser_verify.py` 外提） |
| `official_verify.py` | 官方源 URL 与背景元数据 |
| `generate_report.py` | PDF 报告生成（封面页 + 证据内嵌） |
| `batch_screen.py` | 批量筛查（CSV / 逗号分隔列表） |
| `fuzzy_match.py` | 模糊匹配和中文名变体生成 |
| `env_loader.py` | `.env` 加载 |
| `tests/test_screening.py` | 36 项回归测试（纯 unittest，`python -m unittest discover -s tests`） |

### 回归测试

改动 `scripts/` 后应跑一遍；测试不联网、不需要 key：

```bash
python -m unittest discover -s tests -v     # 期望 Ran 36 tests ... OK
```

## 免责声明

- 本工具仅供参考，不构成法律意见
- OpenSanctions 为第三方聚合数据，命中结果需通过官方源校验；无 API key 时降级为网页抓取，须在报告中注明
- **`manual_review` / `error` 表示「没查成」，不得表述为「未列名」**
- 中国清单自动化检索存在漏检风险（栏目改版、公告不定期发布、译名不一致）
- 制裁名单持续更新，筛查报告建议有效期不超过 7 天
- `--stealth` 涉及对目标站点的指纹伪装，启用前须确认符合站点条款与内部合规要求
- 最终合规决策应咨询具有执业资格的律师或合规专家
