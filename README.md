# DataInfra · 跨境可信数据空间 — 全球制裁筛查引擎

面向**律师、合规官、贸易合规与 KYC/AML 团队**的开源筛查工具链：在**客户尽调、交易对手审查、出口前筛查、争议与调查支持**等场景中，协助快速拉通「第三方名单聚合 → 政府官网核验 → 公开情报摘要 → 结构化交付物」的工作流。

> **重要声明（请律师同仁首先阅读）**  
> 本仓库提供的是**技术辅助工具与流程模板**，输出内容为基于公开数据源与自动化抓取的**事实整理与风险线索汇总**，**不构成法律意见、监管解读或终局合规结论**。是否阻断交易、如何起草法律文件、是否满足特定义务，须由**具有执业资格的专业人士**结合具体事实管辖与业务场景独立判断。  
> 制裁与出口管制规则更新频繁，任何筛查报告的**时效性有限**；请以各司法辖区**官方最新公布文本与执法实践**为准。

**仓库地址：** [github.com/TracyWang95/DataInftra-CrossBoardTrustedDataPace-SanctionScreening](https://github.com/TracyWang95/DataInftra-CrossBoardTrustedDataPace-SanctionScreening)

---

## 本 Fork 的增量（中文实体适配）

本仓库基于上述开源项目二次开发，新增内容如下。原项目的核心架构、三层筛查方法与国际清单源实现均归原作者所有，特此致谢。

### 1. 新增中国管制清单核查源（3 个）

原工具的官方核验源覆盖 OFAC / BIS / DoD / SAM / FCC / UK / UN / EU / AU / CA，**不含任何中国清单**。本 fork 新增：

| 源标识 | 名称 | 法律依据 | 入口 |
|--------|------|----------|------|
| `cn_ucl` | 不可靠实体清单 | 《不可靠实体清单规定》（商务部令 2020 年第 4 号） | 商务部产业安全与进出口管制局 |
| `cn_control` | 出口管制管控名单 / 关注名单 | 《出口管制法》《两用物项出口管制条例》 | 同上 |
| `cn_counter` | 反制清单 | 《反外国制裁法》及商务部令 | 同上 |

**实现说明**：中国管制清单以「商务部公告 / 商务部令」形式逐次发布，不提供统一的在线查询数据库，因此检索路径为「法律政策栏目列表页 → 定位相关公告 → 进入正文 → 全文比对 → 截图留痕」，与国际源的结构化查询逻辑不同。

### 2. 修复中文变体在名单比对中被丢弃的问题

上游实现在 `verify_uk` / `verify_un` / `verify_ca` / `verify_sam` 等多处，以
`if not re.search(r'[\u4e00-\u9fff]', v): continue` 的方式**显式跳过中文变体**。
其隐含前提是「被检索名单页为英文」。当检索对象为中国官方公告时该前提不成立——
中文变体反而是公告的正式列名形式。

本 fork 新增 `match_variants_cn()`，按「中文优先、英文兜底」的顺序匹配，
并保留原英文匹配路径不受影响。

### 3. 新增中文实体制裁误命中判定规则库

见 [`references/cn_entity_false_positive.md`](references/cn_entity_false_positive.md)。
覆盖七类中文场景特有误报模式（高频字号撞名、简繁/异体字、音译分歧、中资离岸架构、
母子公司混同等），并给出分级判定流程与留痕要求。该部分为法律专业判断，非工程实现。

### 4. 政务站点访问容错

中国政务站点存在访问节流与首屏渲染延迟，直接套用国际源的等待节奏常拿到未渲染完成的
DOM。本 fork 为中文源单独实现 `load_cn_index()`（多次等待 + 重试 + 兜底入口），
并在程序性失败时输出 `manual_review` 而非 `clean`——**程序失败与「确认未列名」在合规
后果上完全不同，不得混同**。

### 快速上手（中国清单）

```bash
python scripts/browser_verify.py "埃比特系统美国分公司" --sources cn_ucl --exact-name-only
python scripts/browser_verify.py "某公司" --sources cn_ucl cn_control cn_counter --headless -o out
```

> **局限说明**：中国清单公告不定期发布、栏目结构可能调整，自动化检索存在漏检风险。
> 本工具输出仅为初筛线索，任何 `clean` 结果都不等同于「该实体未列入任何中国管制清单」，
> 重要交易仍须人工复核与法律评估。

### 运行样例（examples/）

[`examples/`](examples/) 目录收录一次真实运行的脱敏样例：对中国三个清单源筛查「斯凯迪奥公司」
（该公司确已列入不可靠实体清单）的截图留痕与核验记录，含公告正文全文比对截图。
注意样例中 `cn_ucl` 因政务站点节流返回 `manual_review`——这正是设计行为：
**程序性失败不得被报告为「未列名」**。

---

## 律师场景：本工具能做什么

| 场景 | 说明 |
|------|------|
| **客户/交易对手准入** | 对法人或自然人名称进行多源检索与官网核验，形成可归档的检索记录与截图证据链。 |
| **进出口与供应链** | 与美国 BIS、OFAC、DoD 1260H、欧盟/联合国等多类清单相关的**初筛与线索定位**（深度法律分析仍须专业意见）。 |
| **诉讼与调查支持** | 固定时间点、固定检索式下的网页与名单状态，作为**过程性材料**（是否满足证据规则由承办律师评估）。 |
| **律所知识管理** | 将 `SKILL.md` 与 `references/` 中的清单说明、红旗清单、法律影响模板嵌入内部知识库或 Agent 工作流。 |

本项目的 **`SKILL.md`** 是为 AI 助手（如 Claude/Cursor）编写的**操作规范**：何时必须向客户确认搜索变体、何时严格按用户给定字符串检索、如何串联脚本与交付物路径。人类律师可直接阅读该文件理解全流程逻辑。

---

## 方法论：三层筛查 + 误中审查

1. **第一层 — 聚合初筛（OpenSanctions）**  
   对接 [OpenSanctions](https://www.opensanctions.org/) 等聚合数据，覆盖全球 **100+** 制裁与涉敏名单数据集，用于**快速定位候选命中**。聚合数据可能存在延迟或匹配误差，**不能单独作为终局依据**。

2. **第二层 — 官方网站核验（Playwright）**  
   通过浏览器自动化访问多国/多机构**官方公开查询入口**（如 OFAC、BIS CSL、UN、EU、UK、澳大利亚 DFAT、加拿大 SEMA 等路径，以脚本内配置为准），进行搜索并**截图留痕**，便于工作底稿归档。

3. **第三层 — 受控情报摘要（Tavily）**  
   对限定域/受信新闻与公开网页进行检索，用于**补充上下文**（如公开报道、监管动态），不替代官方法律文本。

4. **误中审查（False Positive）**  
   对「泛化清单」与易混淆命中（如国家代码碰撞、部分字符串匹配）进行**自动标注与降级提示**，减少将无关实体误判为制裁对象的风险。具体规则见 `references/false_positive_check.md` 与 `scripts/browser_verify.py` 内配置。

最终可输出 **HTML 报告**（便于浏览与内部传阅）及 **PDF**（便于对外提交或归档），详见 `SKILL.md` 阶段 4。

---

## 快速开始

### 环境要求

- Python 3.10+（推荐 3.11）
- Windows / macOS / Linux 均可；浏览器自动化依赖 Chromium（由 Playwright 安装）

### 安装

```bash
cd sanctions-screening   # 或克隆后的项目根目录
pip install -r requirements.txt
python -m playwright install chromium
```

### 配置密钥（切勿提交 `.env`）

```bash
copy .env.example .env   # Windows；Unix 使用 cp
```

编辑 `.env`，填入：

- `OPENSANCTIONS_API_KEY` — [OpenSanctions API](https://www.opensanctions.org/api/)
- `TAVILY_API_KEYS` 或 `TAVILY_API_KEY` — [Tavily](https://tavily.com/)

本仓库 **`.gitignore` 已排除 `.env`**；若您曾将密钥写入其他文件并误提交，请立即**轮换密钥**并自 Git 历史中清除敏感提交（可使用 `git filter-repo` 等工具，或新建空仓库仅保留净提交）。

### 命令行示例（从项目根目录执行）

初筛与情报汇总（阶段 1 思路，具体参数见 `SKILL.md`）：

```bash
python scripts/screen_entity.py "Example Corp" --type LegalEntity -o Example_api_report.md
```

官网核验与 HTML/PDF 报告（阶段 2+，需已安装 Playwright）：

```bash
python scripts/browser_verify.py "Example" --type Entity --extra-variants "example corp" -o screenshots/ --report Example_sanctions_report.html --api-report Example_api_report.md
```

批量场景见 `scripts/batch_screen.py`。

---

## 目录结构（核心文件）

| 路径 | 用途 |
|------|------|
| `SKILL.md` | AI/自动化执行规范与完整阶段说明（**必读**） |
| `references/legal_implications.md` | 各清单法律影响叙述模板（撰写报告时引用） |
| `references/false_positive_check.md` | 误中判断指引 |
| `references/us_lists_coverage.md` | 美国主要清单覆盖说明 |
| `references/red_flags_checklist.md` | BIS「了解你的客户」类红旗检查参考 |
| `scripts/screen_entity.py` | 初筛 + 情报 + 校验计划整合 |
| `scripts/browser_verify.py` | 官网自动化检索与截图、报告生成 |
| `scripts/opensanctions_search.py` / `tavily_search.py` | API 封装 |
| `requirements.txt` | Python 依赖一览 |

**仓库范围：** 仅同步 `SKILL.md`、`references/`、`scripts/` 及上述配置文件。运行产生的 `screenshots/`、`*_sanctions_report.*`、`*_api_report.md` 以及微信推广类文稿等已列入 `.gitignore`，留在本地即可，不会进入 Git。

---

## 风险评级与报告解读

工具使用多维度评分输出风险等级（如 CRITICAL / HIGH / MEDIUM 等），区间与建议动作见 `SKILL.md`「报告解读」一节。律师使用时建议：

- 将**评级**视为**排程与复核优先级**的参考，而非替代《出口管理条例》《国际紧急经济权力法》等规则下的实体分析；
- 对**疑似误中**条目必须人工复核全名、地址、别名与主体类型；
- 在对外交付中明确**筛查时间、检索式、数据源版本与限制条件**。

---

## 隐私与职业伦理提示

- 仅在**合法、正当目的**下处理个人数据与商业秘密；遵守适用数据保护法与律所内部政策。
- 自动化访问政府网站时请遵守各站点 **Terms of Use** 与 robots/合理使用惯例；高频批量请求可能触发封禁。
- 若将本工具与生成式 AI 联用，应对输出进行**专业复核**，避免将未验证的陈述写入法律意见。

---

## 开源协议

本项目以 **MIT License** 发布（见 `LICENSE`）。使用、修改与再分发时请保留版权声明。

---

## 致谢与数据来源说明

筛查能力依赖 **OpenSanctions** 项目及各国政府**公开**名单与查询接口；具体数据集与官方 URL 以运行时代码与 `references/` 文档为准。感谢全球开放数据社群维护高质量结构化制裁信息。

---

**再次提醒：** 精品开源的目标是**透明方法论与可审计工具链**，而非替代持证律师的专业判断。欢迎在 Issues 中反馈清单更新、站点改版导致的脚本失效等问题，便于社区共同维护。
