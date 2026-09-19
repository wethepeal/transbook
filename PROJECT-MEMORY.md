# PROJECT-MEMORY — 翻译工程项目详细记忆

> **本文件是本项目的单一事实来源（single source of truth），按需读取，不自动注入。**
> 入口与协作约定见 `AGENTS.md`（自动注入）；机器级环境见 `$DSH_HOME/AGENTS.md`（自动注入）。
> 最后更新：2026-09-17

---

## 0. 如何使用与维护

**读**：开工前、需要环境/决策/任务/术语上下文时，先读本文件对应章节，不要凭记忆推测。

**写**（每次发生就更新，不要攒着）：
| 发生了什么 | 写到哪里 |
|---|---|
| 新决策（架构、依赖、破坏性变更） | 第 5 节追加一行，**不删旧记录** |
| 架构/技术栈确定 | 第 2 节 |
| 目录结构变化 | 第 4 节 |
| 任务开始/完成/阻塞 | 第 6 节 |
| 踩坑、风险、约束 | 第 7 节 |
| 遗留事项 | 第 8 节 |
| 新术语（含用户给的译法） | 第 9 节 |
| 任何更新 | 第 10 节留一行 |

**原则**：本文件记录"事实与决定"，不记录过程流水账；过程在会话里，结论在这里。

---

## 1. 项目概览

### 1.1 已知（用户 2026-09-17 亲述概要）

- 名称：Translation Engineering（翻译工程）
- 工作区：`Z:\AgentProjectHub\Translation_Engineering`
- 性质：**大型项目**，开发即将在本会话窗口开始
- 运行环境：Windows 11 + DeepSeek Harness（DSH）Web GUI，数据根与依赖均在 Z 盘
- 交流语言：中文
- **输入**：PDF 电子书（文件较大）
- **源语言**：英语、日语　**目标语言**：中文
- **输出**：中文 PDF 或 EPUB
- **流程**：PDF → 文本识别与结构化 → 翻译 → 输出 PDF/EPUB
- **翻译引擎**：DeepSeek API 或本地部署小模型
- **硬性要求**：保留章节划分、标题、正文段落、**脚注**、**图片**（表格 M2 处理）
- **交付节奏**：**先功能、后界面**；最终形态为 B/S 架构，Web 前端（体验类似 harness）；现阶段不做 GUI
- **输入格式（2026-09-17 追加）**：**PDF + EPUB 双输入**；PDF 基本都是**文字版** → OCR 不再是默认路径
- **输出（追加）**：**两个版本** —— ① 双语对照（供审核）② 纯中文（终版）；每个版本都出 EPUB + PDF
- **审核流程（追加）**：先出双语版 → 审核并修改译文 → 按 `seg_id` 回灌 → 再出纯中文终版
- **用途（追加）**：**仅个人阅读学习，不分发、不商用** → AGPL 组件可用（主链路仍建议宽松许可）
- **目录约定（追加）**：项目在工作区；本地 AI 部署在 `Z:\AgentHub`；工具二进制在 `Z:\Tools`

### 1.2 需求确认状态

- 概要已给出（§1.1）
- **第一批 6 项重要问题已确认**（2026-09-17）：引擎=DeepSeek API 为主／PDF=文字版为主／仅自用不分发／
  PDF=重排版／输出=双语+纯中文两版／脚注必留+图片保留+表格 M2
- **仍有 Q14~Q23 待确认**（本地模型方案、审核方式、样例书、规模、预算、术语资产、版式字体、竖排、插图、git），
  见 `docs/plan.md` §11.2
- 全部确认后：本文档升到 §2/§4 定稿、决策追加 §5、本小节记为"需求已确认"

### 1.3 成功判据

见 `docs/plan.md` §8 各里程碑的验收标准（待 Q14~Q23 确认后固化到本文件）。

---

## 2. 技术栈与架构

**状态：✅ 已定稿 v1.0（2026-09-17 全部决策确认）** → 完整方案见 `docs/plan.md`

- **语言/环境**：Python 3.12 + uv + 项目内独立 venv（全在 Z 盘）
- **核心架构**：五段流水线（INGEST → PREP → TRANSLATE → REVIEW → RENDER）+
  **格式中立的 DocumentIR（JSON）作为阶段间契约**（PDF 与 EPUB 双输入共用全部下游）
- **输入**：**EPUB 优先**（zip+OPF+NAV，已用样书实测跑通）；PDF 用 pypdfium2/pdfminer.six，
  复杂版面可选 Docling；PyMuPDF（AGPL）仅作可选加速后端（仅自用，许可证无忧）
- **翻译**：Provider 抽象；**DeepSeek API 为主**（flash / v4-pro）+ OpenAI 兼容端点接本地模型
  - 段落级稳定 ID 批量 + 术语注入 + 滚动摘要 + TM 去重 + JSON 校验修复 + **成本硬护栏**
- **本地模型**：**Qwen3-8B(GGUF) 默认** + Qwen3-14B 慢速质量开关（AWQ 不可用，见 D-012）
- **数据**：SQLite(WAL) 存状态/TM/段落；JSON 存中间产物（分层落盘，可单独重跑与回滚）
- **输出**：**IR 单一真源** → EPUB3（手写打包，IR→XHTML）+ PDF（**Typst**，M0 实测选定）；
  双语 / 纯中文两模式；A5 单栏 + **Noto Serif SC 正文 / Noto Sans SC 标题**（本机已装）
- **审核回流**：`export-review`(TSV) → 人工修改 → `apply-review` 按 `seg_id` 回灌 → 出终版
- **服务层（后置）**：FastAPI + 进程池 + SSE；GUI（M6）React+TS+Vite
- **实测成本**：单本约 **¥0.5（flash）/ ¥2（v4-pro）**，预算上限 ¥20~30 绰绰有余

**硬约束**：依赖/模型/缓存全部落 Z 盘；主链路只用 MIT/Apache/BSD 许可组件；
本地模型受可用显存 **6454 MiB** 限制（8B 可跑，14B 需内存卸载）。

---

## 3. 环境与工具链（均为本会话实测结论）

### 3.1 磁盘与路径

| 用途 | 位置 |
|---|---|
| 项目工作区 | `Z:\AgentProjectHub\Translation_Engineering` |
| DSH 数据根（会话/凭据/插件/profile） | `Z:\AgentPlugins\dsh-home` |
| npm / npx 缓存 | `Z:\AgentPlugins\npm-cache` |
| pnpm | `Z:\AgentPlugins\npm-global\pnpm.cmd` |
| Playwright 产物（截图/snapshot） | `Z:\AgentPlugins\playwright-output` |
| 启动器 | `Z:\AgentPlugins\start-dsh.cmd` |
| 环境记录文档 | 本目录 `dsh-plugins-setup.md` |
| **本地 AI 部署**（引擎/模型权重/本地服务） | `Z:\AgentHub`（已存在，空） |
| **工具二进制**（uv / Typst / Tesseract / ffmpeg…） | `Z:\Tools`（已存在，空） |
| **通用缓存**（pip / NuGet / CLIP / Codex / HF / uv / torch） | `Z:\_cache`（已建立） |
| **翻译样书**（M1 验证与规模估算用） | `Z:\_temp\books`（2 卷日文 EPUB，约 13 MB/卷，无 DRM） |

- 旧的 `C:\Users\wethe\.dsh` 已删除（其 `node_modules` 是 Junction，目标被清理后已失效）。
- C 盘剩余约 **5.4 GB**（清理前一度只剩 0.62 GB）；Z 盘剩余约 **206.6 GB**。

### 3.2 已启用插件（4 组，共 80 个工具）

| 插件 | 工具 | 用途 |
|---|---|---|
| `dsh-tool-cordis` | `cordis_inspect_list/query/self`、`cordis_define/run/stop/undefine`（7） | 运行时自造工具/服务/浏览器界面（进程内、临时、重启即失） |
| Playwright MCP | `mcp__playwright__*`（26） | 浏览器自动化：导航、点击、填表、截图、网络与控制台、可访问性快照 |
| GitHub MCP | `mcp__github__*`（45） | 仓库文件、分支、Issue、PR、Actions、搜索、secret scanning |
| Context7 MCP | `mcp__context7__*`（2） | 查最新库/框架文档，避免凭记忆写错 API |

**配置位置**：`Z:\AgentPlugins\dsh-home\profiles\web\cordis.patch.yml`（`patchReload: live`，改完即热重载）。

### 3.3 常用命令

```powershell
# 启动 / 重启 harness
Z:\AgentPlugins\start-dsh.cmd

# 查当前注册的工具（确认真实存在，而非凭记忆）
#   走 harness 工具：cordis_inspect_query(platform=host, provider=Tool, method=listTools)

# 新增插件包
Z:\AgentPlugins\npm-global\pnpm.cmd --dir Z:\AgentPlugins\dsh-home\profiles\web add <包名>
```

### 3.4 GitHub 令牌

- 存放：用户级环境变量 `GITHUB_PERSONAL_ACCESS_TOKEN`（启动器另有注册表兜底读取）。
- 类型：fine-grained PAT，账号 `wethepeal`；**当前仅授权 `wethepeal/pixel-ai`**。
  扩大范围 → 在 GitHub 给该令牌加仓库，**不需要换令牌**。
- 验证方式（不依赖 harness）：Node 直连 `https://api.githubcopilot.com/mcp/` 发
  `initialize` / `tools/list`，或发 `GET https://api.github.com/user`。
- 安全：令牌明文曾出现在会话记录中；**不要写进任何文件、不要提交**。若要轮换，
  自己在新终端执行 `setx` 并重启 harness（这样新令牌不会进入会话记录）。

### 3.5 已核实可用、但未启用的候选插件

| 插件 | 说明 | 代价 |
|---|---|---|
| `dsh-schedule`（+`dsh-time-context`，并把 `ui-schedule` 置 `disabled: false`） | 定时任务，带界面页 | 零下载 |
| `chrome-devtools-mcp` | 性能 trace / 网络 / 控制台（复用本机 Chrome） | npx 安装 |
| `@modelcontextprotocol/server-sequential-thinking` / `-memory` / `-filesystem` | 官方参考服务器：长链推理 / 记忆 / 文件 | npx 安装 |
| `dsh-hooks-claude-code` / `dsh-hooks-codex` | 接入对应 hooks.json 命令钩子 | 零下载，需 configPath |
| `dsh-webhook-github`（需同时插 `dsh-webhook`） | 接收 GitHub webhook | 零下载，需密钥 |
| `serena` / `mcp-server-fetch` | 语义代码检索 / 抓取 | 需先装 uv（本机没有） |

### 3.6 沙箱、权限与网络

- 默认 `workspace-write`：只有工作区可写；工作区外操作需申请 `danger-full-access`（弹批准）。
- 沙箱内 `curl.exe` 的 TLS 会失败（`schannel: SEC_E_NO_CREDENTIALS`），**不是网络问题**；
  用 Node `fetch` 或 `web_fetch`。
- 本地 GUI `http://127.0.0.1:3080` 有认证（根路径 401），Playwright 无法直接驱动本地 GUI；外部站点正常。
- 沙箱不限制出网；npm registry、GitHub、Context7 端点均实测可达。

---

## 4. 目录结构约定（2026-09-17 确定）

| 用途 | 路径 |
|---|---|
| 项目代码与文档 | `Z:\AgentProjectHub\Translation_Engineering\`（本工作区，含 `.venv`、`src/`、`data/`） |
| 本地 AI 部署 | `Z:\AgentHub\`（`models/`、`engines/`、`serve/`） |
| 工具二进制 | `Z:\Tools\` |
| 缓存 | `Z:\_cache\` |

项目内计划结构（详见 `docs/plan.md` §7）：

```
docs/plan.md ｜ pyproject.toml ｜ configs/（术语表/风格指南/prompts）
src/transbook/{cli,ir,ingest,prep,translate,memory,review,render,pipeline,store,api}
tests/ ｜ data/（输入书/输出，不入 git）｜ .venv/
```

---

## 5. 决策记录（ADR-lite，追加式）

| 编号 | 日期 | 决策 | 理由 | 影响 / 状态 |
|---|---|---|---|---|
| D-001 | 2026-09-16 | DSH 数据根与 npm 缓存从 C 盘迁到 `Z:\AgentPlugins`，用用户级环境变量 `DSH_HOME` / `npm_config_cache` 指定 | C 盘爆满（npm 缓存单项 3.16 GB） | 已生效；旧 C 盘根已删除。**教训**：`profiles\node_modules` 是指向 npm 缓存的 Junction，清理缓存会连带打断链接——迁移必须先拷贝、后清理 |
| D-002 | 2026-09-16 | 启用 `dsh-tool-cordis`、Playwright MCP、Context7 MCP、GitHub MCP，并开启会话全文检索 | 补齐浏览器自动化、仓库操作、文档查询、运行时自扩展四项能力 | 已生效（80 工具）；会话全文检索待首次搜索建库 |
| D-003 | 2026-09-17 | 记忆分三层：`$DSH_HOME/AGENTS.md`（机器级，全项目）/ 项目 `AGENTS.md`（入口，自动注入）/ `PROJECT-MEMORY.md`（详细，按需读） | 自动注入的内容每轮都占上下文，必须精简；细节要能无限增长 | 已生效（写入后 harness 立即注入并实证） |
| D-004 | 2026-09-17 | 用 Context7 + Playwright 复用系统 Chrome（零浏览器下载） | C 盘空间紧张、GitHub 下载不稳定 | 已生效，`playwright-browsers` 目录为空 |
| D-005 | 2026-09-17 | C 盘可回收缓存迁到 `Z:\_cache`，并在原位置留 **Junction** 做透明重定向（clip / nuget-packages / pip-cache / codex-runtimes） | C 盘一度只剩 0.62 GB | 已生效：`clip` 890 MB、`nuget` 1464 MB、`pip` 749 MB、`codex-runtimes` 1334 MB；C: 0.62 → 5.38 GB。**方法**：拷贝 → 逐文件核对 → 重命名原目录（可回滚）→ 建链接 → 验证可读 → 才删备份。**绝不允许先删后建** |
| D-006 | 2026-09-17 | 项目技术方案提案：五段流水线 + DocumentIR 契约 + Python 生态 | 见 `docs/plan.md` | v0.1 已评审；v0.2 采纳 6 项决策 |
| D-007 | 2026-09-17 | 引擎：**DeepSeek API 为主，本地模型为备选** | 质量差距明显；本地定位隐私/离线兜底 | 已确认 |
| D-008 | 2026-09-17 | 输入支持 **PDF + EPUB 双格式**，统一产出 DocumentIR；**能用 EPUB 就优先 EPUB**（结构由作者标注，无需版面推断） | 用户追加需求 | 已确认；EPUB 带 DRM 无法处理 |
| D-009 | 2026-09-17 | 输出 **双语对照版 + 纯中文版** 两套（各含 EPUB/PDF），并实现**审核回流**：导出可编辑审校文件 → 按 `seg_id` 回灌 → 再渲染终版 | 用户要求"先双语审核，通过后出纯中文" | 已确认；新增 `review/` 模块 |
| D-010 | 2026-09-17 | 因**仅个人自用、不分发不商用**，AGPL 组件（PyMuPDF/EbookLib）可用；但主链路仍保持宽松许可（pypdfium2/pdfminer/Docling/WeasyPrint），PyMuPDF 仅作可选加速后端 | 降低未来分享或开放 Web 访问时的许可证风险 | 已确认 |
| D-011 | 2026-09-17 | 目录约定：本地 AI → `Z:\AgentHub`；工具二进制 → `Z:\Tools`；缓存 → `Z:\_cache` | 用户指示 | 已确认 |
| D-012 | 2026-09-17 | 本地模型修正：**AWQ 依赖 vLLM（仅 Linux/WSL2，本机 WSL2 无发行版）**，且 **14B-4bit 约 8–9 GB > 可用显存 6454 MiB** | 实测显存与格式约束 | 用户采纳 **A+B**：Qwen3-8B(GGUF) 默认 + 14B 慢速质量开关 |
| D-013 | 2026-09-17 | **样书就位**：`Z:\_temp\books` 两卷日文 EPUB（Re:Zero 43/44，各约 13 MB，无 DRM） | 用户提供，用于 M1 验证与规模估算 | 实测：单本 15.7~16.7 万字符 / 3,600~3,800 段 / 24 张图 |
| D-014 | 2026-09-17 | ⚠️ **结构识别必须优先取 NAV/NCX**：样书 `<h1>`~`<h6>` 计数为 **0**（该出版社用 CSS 类名表达标题层级） | 实测发现，若依赖语义标签会导致结构全丢 | 写入计划书风险 #1（高） |
| D-015 | 2026-09-17 | ✅ **竖排不是难题**（EPUB 层面）：竖排是 CSS `writing-mode` 排版，文字层仍是普通 Unicode → 抽取正常，输出改用横排即可 | 样书实测（`vertical-rl` ×4，正文正常提取） | 仅**竖排 PDF** 需列序重排（M2） |
| D-016 | 2026-09-17 | 💰 **成本远低于预算**：按官方现价 + 样书规模，单本约 **¥0.5（flash）/ ¥2（v4-pro）**，而预算上限是 ¥20~30 | 实测规模 + 官方定价页 | 可直接用更强模型；仍上 `--max-cost` 硬护栏；**测试阶段目标花费≈0 元** |
| D-017 | 2026-09-17 | ✅ **结构还原算法确定（零启发式）**：NAV 目录给出 `(标题, 文件#锚点)`，正文里 `id=锚点` 的元素即章节标题块；**`<rt>` 注音必须剥离**（否则逐字注音的标题与 NAV 对不上） | M0 实测：两本样书 22 条目录中，带锚点的 **16/16 = 100%** 定位成功；剥离 rt 后标题 **16/16 完全一致** | 已写入 `tools/nav_anchor_check.py`；M1 直接复用 |
| D-018 | 2026-09-17 | 🖨️ **PDF 引擎改为 Typst**（备选 WeasyPrint 排除）：WeasyPrint 在本机 Windows 报 `libgobject-2.0-0` 缺失（需 GTK/Pango）；Typst 1.23 s 出 A5 PDF，文字抽取 5/5，渲染图人眼确认禁则/缩进/页码正确 | M0 实测 #5 | **架构影响**：内容真源由 XHTML 上提为 **IR**（EPUB→XHTML、PDF→Typst 两个适配器）；字体用本机 **Noto Serif SC / Noto Sans SC**（已装，无需下载） |
| D-019 | 2026-09-17 | 🌐 **网络实测**：HuggingFace 官方站不可达；**hf-mirror 首字节慢且会停滞** → 改用 **ModelScope**（模型）+ **gh-proxy/ghfast 代理**（GitHub 发布物），并加"断点续传 + 卡死重试"循环 | M0 实测 | 模型权重落 `Z:\AgentHub\models\gguf`；运行时选 **llama.cpp Vulkan 构建**（30 MB，远小于 CUDA 包 615 MB） |
| D-020 | 2026-09-17 | 🧲 **PDF 抽取定为 pypdfium2 单引擎**：质量与 PyMuPDF **完全等同**（156,881 字符）但**快 3 倍**、许可宽松，且**能直读书签**（12 条，与 EPUB NAV 对应）；**pdfminer.six 淘汰**（本书为 **Type3 字体**，只抽出 3%） | M0 实测 #6（373 页真实日文 PDF） | PDF 与 EPUB **共用同一「目录→章节」结构模型**；竖排 PDF 列序重排仍待验证（该样书是横排） |
| D-021 | 2026-09-17 | 🧱 **结构还原采用三层兜底**：① NAV 锚点 + NAV 标题（主路径）② NAV 无标题 → 用正文标题文本回填 ③ 无锚点 → 类名启发式（`bold/mfont/font-1xxper/title/heading`） | 真实样书里「あとがき」页的 **NAV 标题为空**，仅靠主路径会产出空标题目录项 | 已实现于 `src/transbook/ingest/epub.py`；实测 13 条目录**全部有标题** |
| D-022 | 2026-09-17 | 🧪 **M1 前三步完成**：IR 模型 + EPUB 抽取器 + Markdown 预览闸门，配 15 个自包含测试（代码生成最小 EPUB，不依赖用户文件） | 计划书 §14.7 第 2~4 步 | 真实样书验证：3413 块（标题 13/段落 3388/图片 12）、剥离注音 1977、提取 22 图；提交 `18cfd37` |

---

## 6. 任务板 / 里程碑

| 阶段 | 状态 |
|---|---|
| E0 环境就绪（harness 插件 + 磁盘迁移 + 记忆文件） | ✅ 完成 |
| E1 需求概要（用户已口述，见 §1.1） | ✅ 完成 |
| E2 技术方案计划书 `docs/plan.md` **v1.0** | ✅ 完成（需求全部确认，含 §14 实施/调试/回滚策略） |
| M0 环境与骨架（uv、git init、三方目录、**§12 七项实测**） | ⬜ **下一步**（等用户说"开始"） |
| M0 环境与骨架（uv venv、依赖锁定、缓存重定向、样例书） | ⬜ 未开始 |
| M1 垂直切片（小型英文 PDF → EPUB 端到端） | ⬜ 未开始 |
| M2 抽取质量（结构融合 / 脚注图片表格 / 扫描件与竖排日文 OCR 基准） | ⬜ 未开始 |
| M3 翻译质量与成本（TM / 术语表 / 滚动摘要 / 本地模型对比） | ⬜ 未开始 |
| M4 输出完善（EPUB3 完整 / PDF 中文排版 / 双语对照） | ⬜ 未开始 |
| M5 服务化（FastAPI + 任务队列 + 进度推送） | ⬜ 未开始 |
| M6 Web GUI（项目管理 / 段落级对照校对 / 导出） | ⬜ 未开始 |

---

## 7. 风险与已知坑

1. **C 盘空间**：清理后约 **5.4 GB** 可用（清理前一度只剩 0.62 GB）。已迁往 `Z:\_cache` 并留 Junction：
   `clip` / `nuget-packages` / `pip-cache` / `codex-runtimes`。**任何安装前先确认落盘位置**；
   仍在 C 盘的候选：`AppData\Local\Temp`（约 1.7 GB，可清）、Edge 缓存（小，浏览器运行时勿动）。
2. **令牌权限范围**：GitHub 令牌目前只覆盖 1 个仓库；跨仓库任务会 404。
3. **令牌暴露面**：明文存在于用户环境变量与会话记录中；会话记录若外传需轮换。
4. **Junction 计数陷阱**：`profiles\node_modules\*` 是 Junction，`-Recurse` / robocopy `/XJ`
   不穿越链接，会误判文件缺失。**核验完整性必须去掉 `/XJ`。**
5. **npm 缓存拷贝**：复制缓存目录会导致 `ECOMPROMISED / Lock compromised`，
   需删 `_locks` 与 `_cacache\tmp`。
6. **MCP 冷启动**：`npx` 首次拉取 Playwright MCP 需联网下载，首轮调用可能接近超时上限
   （已设 `toolCallTimeoutMs: 120000`）。
7. **长路径**：Windows 下深层 `node_modules` 路径可能超 260 字符，项目目录层级不宜过深。
8. **本地 GUI 认证**：无法用浏览器工具直接操作本地 GUI，界面类验收需用户配合或改用 API。
9. 🔴 **EPUB 标题不是 h 标签**（样书实测 h1~h6 = 0）：结构必须取 **NAV/NCX + 类名启发式**，
   不能依赖语义标签——否则整本书的章节层级全丢。
10. **ruby 注音量很大**（样书 2,862 处）：必须剥离 `<rt>` 只留基文，否则假名注音污染正文。
11. **样本文件位置**：`Z:\_temp\books`（用户提供，仅个人学习用途）。

---

## 7.1 开发与回滚纪律（对应计划书 §14）

- **分层产物**：`data/work/<book>/{01_ingest,02_prep,03_translate,04_review,05_render}`，
  每层可单独检查、单独删除重跑；**输入书只读，任何情况下不动**。
- **回滚基线**：每个里程碑打 git tag；`translation`（机翻）与 `final_translation`（定稿）分列保留，
  提示词/术语表版本化 → 任意改动都可回退或选择性重译。
- **零成本调试**：FakeProvider、录制回放、`--dry-run`（花钱前先看价）、`--limit/--chapter`、
  `--max-cost` 硬护栏（测试默认 ¥0.5）。
- **最重要的自动闸门**：源/译**段落数与 ID 集合必须一致**，不匹配立即中止（防译文错位）。

---

## 8. 遗留 TODO

- [ ] **会话全文检索**：在 Web 侧边栏搜一个词 → 确认 `Z:\AgentPlugins\dsh-home\session-search.db` 生成
      （前提已验证：`node:sqlite` 可用、SQLite 3.50.4 支持 FTS5、补丁行合成正确）
- [ ] C 盘 ~5.7 GB 可回收项的处理
- [ ] GitHub 令牌按需扩大仓库范围
- [ ] 视需要启用 §3.5 的候选插件
- [ ] `git init` 与分支/提交规范（待用户确认）
- [ ] 回答 §1.2 的项目问题并补全 §1、§2、§4

---

## 9. 术语表

### 9.1 翻译工程通用术语（供对齐用词，非项目结论）

| 术语 | 含义 |
|---|---|
| TM（Translation Memory） | 翻译记忆库，按句段复用历史译文 |
| TB / Termbase | 术语库，强制或建议译法 |
| Segment | 句段，翻译与复用的基本单位 |
| TMX / XLIFF | 记忆交换格式 / 本地化交换格式（含状态、注释、标记） |
| CAT | 计算机辅助翻译工具 |
| MT | 机器翻译；PEMT 指机器翻译后编辑 |
| CAT tool leverage | 匹配率（100% / fuzzy / 新建） |
| MQM / LQA | 翻译质量评估体系 / 语言质量保证 |
| QA check | 自动检查：术语一致、标记配对、数字与占位符、重复句段 |
| Placeholder / Tag | 占位符与内联标记，必须原样保留 |
| i18n / l10n / g11n | 国际化 / 本地化 / 全球化 |
| ICU MessageFormat | 带复数、性别、选择分支的消息格式 |
| Do-Not-Translate (DNT) | 禁止翻译的字符串（品牌名、代码等） |
| Back-translation | 回译，用于质量核验 |

### 9.2 项目专有术语 / 用户指定译法（待填）

| 原文 | 用户指定译法 | 备注 |
|---|---|---|
| （待填） | | |

---

## 10. 变更日志

| 日期 | 变更 | 执行者 |
|---|---|---|
| 2026-09-17 | 建立本文件；写入 D-001~D-004、环境清单、任务板、风险、术语表 | agent |
| 2026-09-17 | C 盘清理（D-005）：4 个缓存迁至 `Z:\_cache` 并留 Junction，C: 0.62 → 5.38 GB | agent |
| 2026-09-17 | 用户口述项目概要 → 填入 §1.1；新增技术方案提案 `docs/plan-v0.1.md`（D-006，待批准） | agent |
| 2026-09-17 | §2 技术栈改为"提案中"摘要；§6 任务板改为 E0/E1/E2 + M0~M6 阶段 | agent |
| 2026-09-17 | 用户答复 6 项关键决策；计划书升为 v0.2（`docs/plan.md`，旧 v0.1 已删）；新增 D-007~D-012；确定三方目录约定；识别本地模型 AWQ/显存两处硬冲突 | agent |
| 2026-09-17 | 答复 Q14~Q22；**实测样书**（结构/字数/竖排/ruby/成本）；计划书升 **v1.0** 并新增 §14 实施·调试·回滚策略；新增 D-013~D-016 与风险 #9~#11 | agent |
| 2026-09-17 | **M0 开工**：uv 0.12.17 + venv + git + CLI 落地（`tp --version`/`doctor` 通过）；NAV 结构算法实测 100% 命中（D-017）；PDF 引擎选定 Typst（D-018）；网络结论（D-019）。产出 `docs/m0-report.md` 与 6 个调研/基准工具 | agent |
| 2026-09-17 | M0 实测 #6 完成：**pypdfium2 定为 PDF 单引擎**（等同 PyMuPDF、快 3 倍、可读书签），pdfminer 因 Type3 字体淘汰（D-020）；模型与引擎下载改用 ModelScope/gh-proxy 并加断点续传重试 | agent |
| 2026-09-17 | **M1 前三步完成**（D-022）：IR 模型 + EPUB 抽取器（三层兜底结构策略 D-021）+ Markdown 预览闸门 + 15 个测试；真实样书结构还原 13/13 条目录有标题。M0 剩余：#3/#4 本地吞吐（模型下载中）、#7 API 成本（等用户 `.env`） | agent |
