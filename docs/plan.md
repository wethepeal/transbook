# 翻译工程 · 技术方案与实施计划书

**版本** v1.0（需求已确认，可开工）　**日期** 2026-09-17
**状态** 待你说"开始"　**取代** v0.1 / v0.2

> v1.0 变更：采纳全部答复（Q14~Q22）；**用你的两本样书做了实测**（结构/规模/成本，见 §2.5、§9）；
> 修正两处认知（**竖排不是问题**、**标题不是 h 标签**）；新增 **§14 实施步骤、调试与回滚策略**。

---

## 0. 已确认决策一览（全部）

| # | 决策 | 内容 |
|---|---|---|
| Q1 | 引擎 | **DeepSeek API 为主，本地为备选** |
| Q2 | 本地模型 | **Qwen3-8B 为默认**（GGUF）+ **Qwen3-14B 慢速质量模式**作为可选开关 |
| Q3 | PDF 输出 | **重排版** |
| Q4 | 元素保留 | **脚注必留、图片保留、表格 M2** |
| Q5 | 双语对照 | **两版都出**：先双语审核，通过后出纯中文；一般信任 API 质量，不做强制二审 |
| Q6 | PDF 类型 | **基本都是文字版** |
| Q8 | 分发/商用 | **不会，仅个人阅读学习** |
| Q14 | 本地方案 | **A + B**（8B 默认 + 14B 可选开关） |
| Q15 | 审核 | **①② 都要**（双语版阅读 + 可编辑文件回灌），但不强制二审 |
| Q16 | 样书 | `Z:\_temp\books` 两本日文 EPUB（已实测，见 §2.5） |
| Q17 | 规模 | 一次翻整本；按样书实测 ≈ **15.7～16.7 万字/本** |
| Q18 | 预算 | 正式翻译每本 **¥20~30 上限**；**测试阶段必须接近零成本** |
| Q20 | 版式 | **A5 单栏 + 思源宋体正文 / 黑体标题** |
| Q21 | 竖排 | 日文 **PDF 版会有竖排**（EPUB 版也有竖排 CSS，但已确认易处理，见 §6.1） |
| D-011 | 目录 | 项目=工作区；本地 AI=`Z:\AgentHub`；工具=`Z:\Tools`；缓存=`Z:\_cache` |

未答项按默认执行（可随时改）：**Q19** 暂无术语表/风格指南（先留空位）；**Q22** 插图严格对应原文位置；**Q23** 开工第一步 `git init`。

---

## 1. 需求（最终）

```
输入：EPUB（优先）或 PDF，英/日 → 中文；一次翻整本
  ↓ ① 抽取结构化
DocumentIR（章节/标题/段落/脚注/图片/表格）
  ↓ ② 清洗切分
Segments（稳定 ID）
  ↓ ③ 翻译（DeepSeek API 为主）
  ↓ ④ 渲染双语版（EPUB+PDF）→ 你审核（可选）
  ↓ ⑤ 回灌修改 → 渲染纯中文终版（EPUB+PDF）
```

**定位**：个人阅读学习，不分发不商用 → AGPL 可用，但主链路仍用宽松许可（PyMuPDF 仅作可选加速后端）。

---

## 2. 现状（实测）

### 2.1 硬件硬约束

| 项 | 实测 | 结论 |
|---|---|---|
| CPU / 内存 | i9-13900H（14C/20T）／ **63.8 GB** | 足够；内存足以支撑 14B 部分卸载 |
| GPU | RTX 4060 Laptop，**可用 6454 MiB** | 8B 可跑；**14B 装不下**（§4.4） |
| 磁盘 | C: **5.37 GB** ／ Z: **206.6 GB** | 一切落 Z 盘 |

### 2.2 软件

Python 3.12.1（`Z:\Python`，venv 可用）｜git ✅ ｜pandoc ✅ ｜**WSL2 运行时已装但无发行版** ❌
｜缺：uv、ollama/llama.cpp、typst、tesseract、ffmpeg（文字版路线下后两者非必需）

### 2.3 目录约定

| 用途 | 路径 |
|---|---|
| 项目 | `Z:\AgentProjectHub\Translation_Engineering\` |
| 本地 AI | `Z:\AgentHub\`（`models/` `engines/` `serve/`） |
| 工具二进制 | `Z:\Tools\` |
| 缓存 | `Z:\_cache\` |

---

### 2.5 ⭐ 样书实测（`Z:\_temp\books`，两卷日文轻小说 EPUB）

| 指标 | 第 43 卷 | 第 44 卷 | 合计 |
|---|---|---|---|
| 文件大小 | 13.12 MB | 13.52 MB | 26.6 MB |
| **正文总字符** | **156,674** | **167,489** | **324,163** |
| 日文字符（假名+汉字） | 138,069（88.1%） | 149,486（89.3%） | 287,555 |
| 段落 `<p>` | 3,780 | 3,571 | **7,351** |
| 标题 `<h1-6>` | **0** ⚠️ | **0** ⚠️ | 0 |
| 注音 `<ruby>` | 1,459 | 1,403 | 2,862 |
| 图片 `<img>` | 12（13.66 MB） | 12（14.10 MB） | 24 |
| 表格 | 0 | 0 | 0 |
| NAV 目录项 | 15 | 13 | 28 |
| spine 文档 | 26 | 25 | 51 |
| 竖排 CSS | `vertical-rl` ×4 | ×4 | — |
| DRM | 无 ✅ | 无 ✅ | — |

**从实测直接得出的四条设计结论：**

1. ⚠️ **标题不是 `<h1>`~`<h6>`**（计数为 0）。该出版社的 EPUB 用 CSS 类名表达标题层级。
   → **结构必须优先取自 NAV/NCX 目录**，配合类名/字号启发式；**不能依赖语义标签**。这是 M1 必须解决的问题。
2. ✅ **竖排（縦書き）不是难题**：EPUB 的竖排是 **CSS `writing-mode` 层面的排版**，文字层仍是普通 Unicode。
   抽取完全正常，只需**不要沿用竖排 CSS**（中文输出按横排排，符合中文习惯）。
   → Q21 的"竖排风险"对 **EPUB 输入**基本不存在；**只有竖排 PDF** 才需要列序重排（M2 处理）。
3. ⚠️ **注音 `<ruby>` 数量很大（2,862 处）**：必须专门处理——默认**剥离 `<rt>` 只留基文**，
   可选保留为译名注记。否则假名注音会污染正文。
4. ✅ 段落数 7,351、正文 32 万字 → 批量翻译的批次规划有了真实依据（§9）。

---

## 3. 总体架构

```
book.epub / book.pdf
   │  ① INGEST：EPUB（zip+OPF+NAV）/ PDF（pypdfium2、pdfminer、可选中 Docling/PyMuPDF）
   ▼
DocumentIR (JSON)  ← 阶段契约：带结构、可序列化、可人眼检查
   │  ② PREP：去连字符/页眉页脚剔除/脚注关联/**ruby 剥离**/日文分词/竖排归一化/语言判定
   ▼
Segments（稳定 ID）→ SQLite
   │  ③ TRANSLATE：Provider 抽象 / 术语注入 / 三层上下文 / 批量并发 / TM / 校验修复 / 成本护栏
   ▼
TranslatedIR（原文+机翻+定稿+状态）
   │  ④ REVIEW：export-review(TSV) ←→ apply-review(按 seg_id 回灌)
   ▼
   │  ⑤ RENDER：IR 单真源 → EPUB3(XHTML) / PDF(Typst)，两种模式 bilingual | zh
```

**三条铁律**：① 可断点续跑 ② 稳定 ID 永不变 ③ **IR 单一内容真源**（EPUB 与 PDF 是两个适配器，双语/纯中文是两种渲染模式）。

---

## 4. 技术栈（定稿）

| 层 | 选型 | 备注 |
|---|---|---|
| 语言/环境 | **Python 3.12 + uv**（装 `Z:\Tools`）+ 项目内 `.venv` | `UV_CACHE_DIR=Z:\_cache\uv` |
| **EPUB 输入** | **标准库 `zipfile` + `lxml`**（或 BeautifulSoup） | 已用标准库实测跑通两本样书 |
| PDF 输入 | pypdfium2（Apache/BSD）+ pdfminer.six（MIT）；Docling（MIT）处理复杂排版 | PyMuPDF（AGPL）**仅作可选加速后端** |
| 翻译 | **DeepSeek API**（`deepseek-flash` / `deepseek-v4-pro`）+ **OpenAI 兼容**本地端点 | 见 §4.4、§9 |
| 本地推理 | **llama.cpp / Ollama + GGUF**，模型落 `Z:\AgentHub\models` | A：Qwen3-8B 默认；B：Qwen3-14B 慢速开关 |
| 状态/TM | **SQLite（WAL）** + SQLAlchemy 2.0（早期可原生 sqlite3） | |
| 渲染 | **IR 单一真源** → EPUB（手写打包，IR→XHTML）+ **PDF 用 Typst**（M0 实测选定；WeasyPrint 因缺 GTK 失败已排除） | 字体：**Noto Serif SC（正文）/ Noto Sans SC（标题）**，**本机已装**（`C:\Windows\Fonts\NotoSerifSC-VF.ttf`） |
| 服务（M5+） | FastAPI + Pydantic v2 + 进程池 + SSE | |
| GUI（M6） | React + TS + Vite | 核心界面：段落级对照校对 |

### 4.4 本地模型（已确认 A+B）

| 模式 | 模型 | 显存 | 用途 |
|---|---|---|---|
| **A（默认）** | Qwen3-8B GGUF（Q5/Q8） | 约 5–6 GB ✅ | 流程调试、隐私模式、零成本跑通 |
| **B（开关）** | Qwen3-14B GGUF Q4_K_M + 部分层卸载到 64 GB 内存 | 约 8–9 GB 权重（**装不进显存，靠卸载**） | 质量优先、可忍受慢速 |

> ⚠️ **AWQ 不可用**：AWQ 属 vLLM 生态，而 vLLM 仅支持 Linux；本机 WSL2 有运行时但**无发行版**。
> 因此本地统一走 **GGUF**。Provider 接口与量化格式解耦，将来若上 WSL2 只需换 base_url。
> **这些数字会在 M0 用实测吞吐复验**（§12）。
>
> ✅ **M0 实测结论（2026-09-17，llama.cpp Vulkan build 11053）**：
> Qwen3-8B-Q5_K_M（5.44 GiB）**全 GPU 卸载可取** —— 生成 **32.2 tok/s**、提示处理 **1049.7 t/s**；
> 纯 CPU 仅 5.31 tok/s（**GPU 快 6.1 倍**）；部分卸载 `-ngl 16` 为 10.18 tok/s（即 14B 的降级路线）。
> OpenAI 兼容端点实测可译：`夢の城に帰り着いた。` → `回到了梦想中的城市。`
>
> ⚠️ **必须关闭思考模式**：Qwen3 是思考型模型，不关会在思考上耗尽 token 且 **content 返回空**。
> 本地引擎已在 `tp translate --engine local` 中默认传 `chat_template_kwargs.enable_thinking=false`。

---

## 5. 数据模型

### 5.1 DocumentIR

```jsonc
{ "schema": "transbook/ir@1",
  "doc": { "id":"re0-v43", "title":"...", "author":"...", "source_lang":"ja",
           "origin":"epub", "vertical":true, "ruby_dropped":1459 },
  "toc": [ { "level":1, "title":"第一章", "block_id":"b0001" } ],
  "blocks": [
    { "id":"b0001", "type":"heading", "level":1, "text":"...", "src":"nav" },
    { "id":"b0002", "type":"paragraph", "text":"...", "spans":[{"kind":"emphasis"}] },
    { "id":"b0003", "type":"image", "path":"assets/img01.jpg", "caption":"" }
  ] }
```

### 5.2 段落表（SQLite）——翻译/审核/续跑/回滚的核心

| 字段 | 说明 |
|---|---|
| `seg_id` | 稳定 ID，**永不变**（双语对齐、审核回灌、重跑都靠它） |
| `block_id` / `order` | 回填结构 |
| `source_text` / `source_lang` / `text_hash` | 原文与 TM 键 |
| `translation` | 机翻（**永不覆盖**，保留可比对） |
| `final_translation` | 审核定稿（为空则渲染用 `translation`） |
| `status` | `pending/translating/done/failed/reviewed/skipped` |
| `engine`/`model`/`prompt_version`/`glossary_version` | 可追溯、可选择性重译（回滚依据） |
| `tokens_in/out`/`cost` | 成本核算与护栏 |
| `attempts`/`last_error` | 诊断 |

**渲染规则**：双语版 = 源文 + 定稿（无则机翻）；纯中文版 = 仅定稿（无则机翻）。

---

## 6. 关键模块设计

### 6.1 输入适配与预处理（样书实测驱动）

**EPUB（主路径）**
1. `zipfile` → `META-INF/container.xml` → OPF（metadata / manifest / spine）
2. **结构来源优先级**：`nav.xhtml`/NCX 目录 → 类名+字号启发式 →（样书 h 标签为 0，故此项不可依赖）
3. 逐章 XHTML → IR 块（`p`/`div`/`img`/`figure`/表格）
4. **ruby 处理**：`<ruby>基文<rt>注音</rt></ruby>` → 只留基文，剥离 `<rt>`；统计丢弃数量并记入 IR
5. **竖排**：识别 `writing-mode: vertical-*` → 记为元数据；**输出不沿用**（中文横排）
6. 图片：从 manifest 提取到 `assets/`，保留在原文位置（Q22）
7. 页眉页脚/页码剔除、全角半角与标点规范化

**PDF**：书签 → 字体统计 → Docling 三级策略；**竖排 PDF** 需列序重排（右→左、上→下）与旋转字判断（M2）；
英文去连字符/连字符合并。

### 6.2 分块与提示词

- 批次按 token 预算打包（约 2–4k 输入 token/批）——样书 7,351 段 → 预计 **约 250–400 批/本**
- JSON 数组带 ID，强制同 ID 返回；缺段/串号/解析失败 → 只重发失败部分
- 提示词外置版本化（`configs/prompts/*.md`）→ `prompt_version` 入库，可评估、可回滚
- 上下文三层：全书设定卡（书名/作者/文体/人物表/术语）→ 章节摘要 → 前后段落滑动窗口
- 滚动摘要：每章译完生成摘要，喂给后续章节（**长文一致性关键**）

### 6.3 成本护栏（直接对应 Q18）

| 机制 | 说明 |
|---|---|
| `--dry-run` | 只分块统计 token 与预估费用，**不调用 API**——花钱前先看价 |
| `--max-cost ¥N` | **硬上限**：累计达上限立即停止并保存进度（**默认测试 ¥0.5 / 正式 ¥25**） |
| `--limit N` / `--chapter X` | 只跑前 N 段或指定章 |
| 缓存命中 | 系统提示词+术语表+设定卡作为稳定前缀 → 命中缓存价（¥0.02/¥0.15 每百万） |
| 成本报表 | 每本结束输出：分章成本、缓存命中率、均价 |

### 6.4 TM 与术语

- TM 跨书共享，命中直接复用；`text_hash` 为键
- 术语表 YAML（强制/建议两级、正则、章节范围）；一旦修改，**只重译命中段落**（回滚友好）
- 可选：术语自动抽取预扫描（人名/专有名词）→ 你确认 → 强制注入

### 6.5 渲染

- XHTML 按章切分 + 统一 CSS；EPUB 打包与 PDF 渲染共用
- **A5 单栏**、思源宋体正文 / 黑体标题、首行缩进 2 字符、标点禁则、脚注、目录页码
- 两种模式：`--mode bilingual` / `--mode zh`

### 6.6 审核回流

```bash
tp render work/re0-v43/ --mode bilingual     # 出双语版（EPUB+PDF）阅读
tp export-review work/re0-v43/ --format tsv  # 导出可编辑校对文件
#   ← 你改译文列（不强制二审，改多少算多少）
tp apply-review work/re0-v43/review.tsv      # 按 seg_id 回灌，状态置 reviewed
tp render work/re0-v43/ --mode zh            # 出纯中文终版
```

改的是 **TSV + seg_id**，不是 HTML/PDF → 不会错位，且**双语版与终版内容强一致**。

---

## 7. 目录结构

```
Translation_Engineering/            # 项目（工作区）
├── AGENTS.md  PROJECT-MEMORY.md  docs/plan.md
├── pyproject.toml  uv.lock
├── configs/{glossary.yaml, style-guide.md, prompts/}
├── src/transbook/{cli,ir,ingest,prep,translate,memory,review,render,pipeline,store,cost,api}
├── tools/epub_probe.py             # 已有：EPUB 结构探测（M0/M1 复用）
├── tests/{unit,golden,fixtures}    # fixtures 用样书切片
├── data/{inbox,work,out}           # 输入书 / 中间产物 / 成品
└── .venv/

Z:\AgentHub\{models,engines,serve}  # 本地 AI
Z:\Tools\                            # uv.exe / typst.exe / ...
Z:\_cache\                           # 各类缓存
```

---

## 8. 里程碑与验收标准

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **M0** 环境与骨架 | uv venv、依赖锁定、三方目录、缓存重定向、`git init`、**§12 的 7 项实测** | `tp --version` 可跑；实测报告产出；C 盘零新增 |
| **M1** 垂直切片 ⭐ | **用样书第 1 章**跑通：EPUB → IR → Markdown 预览 → 分段入库 → DeepSeek 翻译 → **双语 EPUB** | 第 1 章产出可读双语 EPUB；段数对齐校验通过；中断可续跑；**总花费 < ¥0.5** |
| **M2** 抽取质量 | **NAV/类名结构识别**（样书 h=0 的坑）、脚注、图片、**竖排 PDF 列序重排**、日文分词与 ruby 策略 | 两本样书结构还原准确率报告；标题层级正确 |
| **M3** 翻译质量与成本 | TM、术语表、滚动摘要、批量并发、校验修复、成本报表；**本地 8B 接入对比** | 同章 API vs 本地对比报告；术语一致率量化；单本成本 < ¥25 |
| **M4** 双版本与审核回流 | 双语/纯中文渲染、`export-review`/`apply-review`、EPUB3 完整（封面/目录/字体/图片/脚注）、PDF 中文排版（A5） | 改完回灌后终版正确；EPUB 过 epubcheck；PDF 可打印无断行错乱 |
| **M5** 服务化 | FastAPI + 进程池 + SSE 进度 + 项目/段落/审核 API | 可用 HTTP 提交一本书、查进度、交审核 |
| **M6** Web GUI | 项目管理、进度看板、段落级对照校对、导出 | 不用命令行即可完成上传→翻译→校对→导出 |

---

## 9. 成本与规模（基于样书实测 + 官方现价）

**实测规模**：单本 15.7~16.7 万字符 / 约 3,600~3,800 段 / 3.2~4.9 万字符的单章最大块。

**Token 估算**（日文约 1~2 字符/token，含上下文开销 ×1.4~1.5）：

| 项 | 单本估算 |
|---|---|
| 输入 token | 约 15 万 ~ 25 万 |
| 输出 token（中文更紧凑，约 0.8×） | 约 8 万 ~ 12 万 |

**按 DeepSeek 官方现价（[定价页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)）试算单本：**

| 模型 | 输入(未命中) | 输出 | 单本估算（空闲时段） | 高峰时段 |
|---|---|---|---|---|
| `deepseek-flash` | ¥1/百万 | ¥4/百万 | **约 ¥0.5** | 约 ¥1 |
| `deepseek-v4-pro` | ¥4.5/百万 | ¥13.5/百万 | **约 ¥2** | 约 ¥4 |

> 🎉 **结论：你的 ¥20~30 预算绰绰有余**（约为实测的 5~15 倍）。
> 意味着：**可以直接用更强的 `deepseek-v4-pro`**，甚至跑一遍"快速译 + 二审"两遍法（约 ¥4~8），仍远低于预算。
> 空闲时段（非工作日 9-12 点/14-18 点）价格为高峰的一半——批量翻译安排在空闲时段更省。
>
> ✅ **已用真实样书 dry-run 校准（2026-09-17）**：3413 块 → 141 批，
> **预估 ¥0.5361（flash）/ ¥1.9680（v4-pro）**（空闲时段）——与上表估算吻合。
> 复核命令：`tp translate <workdir> --engine deepseek --dry-run --price-tier idle`（**不花钱**）。
>
> ✅ **本地模型实测（同日）**：Qwen3-8B-Q5_K_M 全 GPU 卸载 **32.2 tok/s**、提示处理 **1049.7 t/s**；
> 按 99k 输出 token 估算，**整本约 1~1.5 小时**（远快于原估的 6~20 小时）；纯 CPU 仅 5.31 tok/s。
>
> ⚠️ 真实用量仍受提示词长度、重试次数影响；以 `.env` 接入后跑单章实测做最终确认。

---

## 10. 风险与对策

| # | 风险 | 等级 | 对策 |
|---|---|---|---|
| 1 | **标题不是 h 标签**（样书实测 0 个）→ 结构识别失败 | 🔴 高 | 结构以 **NAV/NCX 为准**；类名+字号启发式兜底；M1 必须先用样书验证 |
| 2 | 竖排 PDF 的列序与旋转字 | 🟡 中（EPUB 竖排已确认无碍） | M2 专项：列序重排（右→左）+ 旋转检测；用真实竖排 PDF 验证 |
| 3 | ruby 注音污染正文 | 🟡 中（样书 2,862 处） | 默认剥离 `<rt>` 留基文；数量入 IR 便于核查 |
| 4 | 长文一致性（人名/称谓漂移） | 🟡 中 | 术语表 + 滚动摘要 + 全书设定卡 |
| 5 | 成本超支 | 🟢 低（实测远低于预算） | 仍上 **`--max-cost` 硬护栏** + `--dry-run` 预估 |
| 6 | 许可证（AGPL） | 🟢 低（仅自用） | 主链路宽松许可；PyMuPDF 可选 |
| 7 | 本地模型装不进显存 / AWQ 不兼容 | 🟡 中 | 走 GGUF；8B 默认，14B 靠内存卸载 |
| 8 | C 盘仅 5.37 GB | 🟡 中 | 全部落 Z 盘 |
| 9 | 大文件内存 | 🟡 中 | 按章/按页流式，禁止整本载入 |
| 10 | 版权 | 🟢 低（个人学习） | 已在风险表记录；建议以公版/自购材料为主 |

---

## 11. 问题清单

**已全部确认**（Q1~Q22）✅ ｜ 未答项按默认执行：Q19 术语表暂缺（留空位）、Q22 插图严格对应、Q23 现在 `git init`。

---

## 12. M0 必须先实测的技术点

1. uv 在 `Z:\Tools` 的安装与 `UV_CACHE_DIR` 落盘；venv 在 Z 盘。
2. **样书 NAV/类名结构识别**原型验证（针对 h 标签为 0 的坑）——**最高优先级**。
3. llama.cpp/Ollama 安装到 `Z:\AgentHub`（权重不落 C 盘），实测 Qwen3-8B 吞吐与显存占用。
4. Qwen3-14B GGUF 部分卸载的可行性与实际速度（决定开关 B 是否值得留）。
5. ~~WeasyPrint 中文排版实测~~ ✅ **已完成**：WeasyPrint 在 Windows **失败**（缺 `libgobject-2.0-0`/GTK）；
   **Typst 通过**（1.23 s 出 A5 PDF、文字抽取 5/5、渲染图人眼确认禁则与缩进、字体用本机 Noto Serif/Sans SC）。
6. ~~pypdfium2 / pdfminer.six 抽取对比~~ ✅ **已完成**（见 `docs/m0-report.md` §5）：
   **pypdfium2 与 PyMuPDF 抽取结果完全一致（156,881 字符）但快 3 倍**，且能直读 12 条书签；
   **pdfminer.six 因 Type3 字体只抽出 3% 被淘汰**。→ PDF 单引擎定为 **pypdfium2**（Apache/BSD）。
   遗留：**竖排 PDF 列序重排**待真实竖排样例验证（现有样书为横排）。
7. DeepSeek API 实测：并发限流表现、JSON 输出稳定性、缓存命中率、**单章真实成本**（校准 §9）。

---
---

## 14. 实施步骤、调试与回滚策略（本轮新增）

### 14.1 开发节奏：每阶段都是"能运行、能验收"的切片

每个里程碑固定四步走：

```
① 定契约（接口/数据结构先定） → ② 最小可用实现 → ③ 用真实样书验证 → ④ git tag + 更新记忆文件
```

**垂直切片优先**：M1 先打通"样书第 1 章 → 双语 EPUB"的完整链路，再横向加功能。
好处：**任何阶段中断，手上都有一个能跑的成品**，而不是一堆半成品模块。

### 14.2 版本控制与回滚基线

| 项 | 做法 |
|---|---|
| 仓库 | M0 第一步 `git init`（建议本地仓库即可） |
| 分支 | `main` 始终可运行；功能走 `feat/xxx`，合并前跑测试 |
| 里程碑标签 | `m0-env`、`m1-slice`、`m2-extract` … → **回滚就是 `git checkout <tag>`** |
| 提交规范 | `feat:` / `fix:` / `docs:` / `refactor:` + 一句话说明 |
| 依赖锁定 | `uv.lock` 入库；回滚依赖 = `git checkout uv.lock && uv sync` |
| 配置版本化 | `configs/`（术语表、风格指南、提示词）全部入库，改动可追溯 |

### 14.3 分层产物 = 天然调试路径

中间产物按阶段分目录落盘，**每一层都能单独检查、单独重跑**：

```
data/work/re0-v43/
├── 01_ingest/    book.ir.json      # 结构对不对？→ 可直接看 JSON
│                 preview.md        # ★ 人眼检查闸门（最重要的一步）
├── 02_prep/      segments.jsonl    # 分句/清洗/ruby 剥离对不对？
├── 03_translate/ translations.db   # 翻译状态、失败段落、成本
├── 04_review/    review.tsv        # 审核回流
└── 05_render/    book.zh.epub / book.bi.epub / *.pdf
```

- **某阶段出问题**：删掉该阶段目录重跑即可，**前面阶段不动、不重复花钱**
- `tp status data/work/re0-v43/` 一屏显示：各阶段完成度、失败段落清单、累计花费

### 14.4 零成本调试手段（直接对应你"测试别花这么多"）

| 手段 | 作用 |
|---|---|
| **FakeProvider** | 完全不调用 API，返回 `[译]原文` 伪译文 → 跑通全链路验证代码 |
| **录制/回放** | 真实调用**一次**存 fixture，之后所有测试回放 → **测试成本归零且结果稳定** |
| **`--dry-run`** | 只分块 + 统计 token + 预估费用，**一分钱不花先看价** |
| **`--limit N` / `--chapter X`** | 只跑 1 章（M1 全程只用样书第 1 章） |
| **`--max-cost ¥N`** | 硬上限护栏，达到即停并存进度（**测试默认 ¥0.5**） |
| **本地 8B 模型** | 用本地模型调试流程，API 只在验收时用 |

> 开发与测试的 API 花费目标：**接近 0 元**；M1 全部验收（含一次真实翻译）控制在 **¥0.5 以内**。

### 14.5 回滚矩阵（改坏了怎么办）

| 改动类型 | 回滚方式 | 粒度 |
|---|---|---|
| 代码 | `git checkout <tag>` / `git revert` | 需重跑受影响阶段 |
| 提示词 | 提示词版本化 + `prompt_version` 入库；`tp retranslate --prompt-version v3` 只重译受影响段落 | **旧译文保留**，可对比择优 |
| 术语表 | 文件入库；改后只重译命中术语的段落 | 段落级 |
| 译文（改错） | 段落表同时保留 `translation`（机翻）与 `final_translation`（定稿）；清空定稿即回到机翻 | **单段级** |
| 数据库 schema | Alembic 迁移 + downgrade；早期用 schema 版本号 + `.db` 备份 | 阶段级 |
| 产物 | `data/work/<book>/<阶段>/` 整目录删除重跑（**输入书只读，绝不动**） | 阶段级 |
| 依赖 | `uv.lock` 锁定 + `uv sync` | 环境级 |

**原则**：**任何"生成物"都可重建，任何"人工输入"都要备份**。
输入书与审核回灌的 TSV 归入"人工输入"，改动前自动备份一份带时间戳的副本。

### 14.6 测试策略

| 类型 | 内容 |
|---|---|
| 单元测试 | 分句、去连字符、ruby 剥离、**稳定 ID 生成**、成本计算、JSON 解析与修复、竖排列序 |
| 金文件回归 | 用**样书切片**做 fixture：抽取结果与译文快照对比（改动引入差异立即发现） |
| 端到端冒烟 | FakeProvider 跑通 M1 全链路（零成本，可进 CI） |
| **对齐校验（最重要的自动闸门）** | 源段落数 == 译段落数、ID 集合一致；不匹配立即中止 → **防止译文错位** |
| 质量抽检 | 术语一致率、漏译率、数字/专有名词保持率 |

### 14.7 M1 施工顺序（具体到可执行的小步）

1. 项目骨架：`pyproject.toml` + uv + CLI 骨架（`tp --version`）+ 结构化日志
2. IR 数据模型（Pydantic）+ schema 版本号
3. **EPUB ingest**：zip → OPF → **NAV 取结构** → 章节 XHTML → IR（**用样书 1 验证**）
4. `preview.md` 导出 → **你看一眼结构对不对**（第一个交付检查点）
5. 预处理：段落切分 + ruby 剥离 + 稳定 ID + 入 SQLite
6. Provider 接口 + FakeProvider + DeepSeek Provider
7. 批量翻译 + JSON 校验修复 + 断点续跑
8. 成本统计 + `--dry-run` + `--max-cost`
9. XHTML 渲染 + EPUB 打包（`bilingual` / `zh` 两模式）
10. **端到端**：样书第 1 章 → 双语 EPUB → 你验收（花费 < ¥0.5）

> 每完成 1 步就提交一次、可运行；**任何一步卡住都不会阻塞前面的成果**。

### 14.8 验收清单（每个里程碑的 Definition of Done）

- [ ] 能跑通（有可复现的命令）
- [ ] 有测试覆盖（单测 + 端到端）
- [ ] 产物可检查（中间文件可人眼验证）
- [ ] 花费有记录（成本报表 ≤ 预算）
- [ ] 记忆文件已更新（`PROJECT-MEMORY.md` 决策与进度）
- [ ] 已打 git tag

---

## 15. 暂不做

❌ GUI（M6）　❌ 原位保版式替换　❌ 实时协作　❌ 图片内文字翻译/语音字幕
❌ 自研模型训练/微调　❌ 扫描件 OCR 作为默认路径（文字版为主，按需再评估）

---

**下一步**：你说一声"开始"，我就从 **M0** 动手（第一件事是 §12 的 7 项实测 + `git init` + 骨架），
M0 完成后给你一份**实测报告**（含本地模型真实吞吐、结构识别原型结果、单章真实成本），再进 M1。
