# transbook 交付说明（M0–M6）

> 电子书翻译流水线：**PDF / EPUB（英 / 日）→ 中文 → EPUB + PDF**
> 版本：M0–M6 全部完成 ｜ 测试 **285 个全绿** ｜ 最后更新 2026-09-21
>
> 本文是**给使用者看的**：怎么装、怎么跑、每个数字怎么复现、出问题怎么查。
> 开发过程与决策记录在 `PROJECT-MEMORY.md`（§5 决策 D-001…D-055）；技术方案在 `docs/plan.md`。

---

## 目录

1. [这是什么](#1-这是什么)
2. [五分钟跑通](#2-五分钟跑通)
3. [操作手册 · 命令行](#3-操作手册--命令行)
4. [操作手册 · Web 界面](#4-操作手册--web-界面)
5. [专项功能](#5-专项功能)
6. [验收数据](#6-验收数据)
7. [目录结构与产物](#7-目录结构与产物)
8. [成本](#8-成本)
9. [故障排查](#9-故障排查)
10. [已知限制与未做项](#10-已知限制与未做项)
11. [分发与打包](#11-分发与打包)

---

## 1. 这是什么

一条把外文电子书翻成中文、并输出成品的流水线。六个阶段：

```
① 抽取          ② 入库            ③ 翻译              ④ 审核          ⑤ 渲染
PDF/EPUB   →   DocumentIR   →   SQLite 段落表   →   人工定稿    →   EPUB / PDF
             （JSON，人工闸门）   （TM 复用旧译文）   （TSV / 界面）   （双语+纯中文）
```

设计上的三条主线：

- **IR 是唯一真源**。EPUB 与 PDF 抽取都产出同一套 `DocumentIR`，渲染器（EPUB / PDF）各自是它的适配器——加一种输出格式不用碰抽取。
- **每个阶段可单独重跑**。段落有稳定 ID 与内容哈希：改了抽取逻辑，译文能凭内容哈希从 TM 迁移回来，不会白花钱。
- **钱花在明处**。翻译前可 `--dry-run` 看价，过程中有 `--max-cost` 硬护栏，结束后 `tp status` 可核账。

**已做到**：竖排日文 PDF 与 EPUB 的段落还原、振假名剥离、页眉页脚过滤、图片/封面保留、脚注与表格结构、术语一致、滚动摘要、段落级对照校对、EPUB3 官方校验。

**没做**：图形界面之外的协作功能、原位保版式替换、图片内文字翻译（见 [§10](#10-已知限制与未做项)）。

---

## 2. 五分钟跑通

### 2.1 环境要求

| 组件 | 版本 | 必需 | 说明 |
|---|---|---|---|
| Windows | 10/11 | ✅ | 全部实测在 Windows 上完成 |
| Python | 3.12 | ✅ | 用 `uv` 管理虚拟环境 |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.12 | ✅ | 装依赖（本机在 `Z:\Python\Python_3_12_1\Scripts\uv.exe`） |
| DeepSeek API Key | — | ✅ | 翻译用；没有也能跑 Fake 引擎验证流程 |
| Node.js + npm | ≥ 20 | ⭕ | 只在**从源码构建 Web 界面**时需要；用官方发布包（Release zip）则完全不需要 |
| Java | ≥ 11 | ⭕ | 只在要跑 **epubcheck** 官方校验时需要 |

### 2.2 首次安装

```powershell
cd Z:\AgentProjectHub\Translation_Engineering

# ① Python 依赖（会把虚拟环境建在项目内的 .venv）
uv sync

# ② 配置密钥
copy .env.example .env
notepad .env            # 填入 DEEPSEEK_API_KEY=sk-xxxx

# ③ 自检
.venv\Scripts\tp.exe doctor
```

`tp doctor` 会打印解释器、目录、缓存重定向、密钥、密钥是否就位、磁盘余量——**先看这一屏**。

```powershell
# ④（可选）Web 界面：必须构建一次
cd web
npm install
npm run build
cd ..
```

### 2.3 跑第一本书

**路线 A —— 命令行**（以 EPUB 为例）：

```powershell
$W = "data\work\mybook"
tp extract "D:\books\某本书.epub" -o $W     # ① 抽取
tp import  $W                                # ② 入库
tp translate $W --engine fake                # ③ 先用 Fake 跑通（零成本）
tp render  $W -m bilingual --to both         # ⑤ 出双语 EPUB+PDF
```

确认流程没问题后，把 `--engine fake` 换成真实引擎：

```powershell
tp translate $W --engine deepseek --model deepseek-flash --max-cost 3 --price-tier idle
```

> `tp` 即 `.venv\Scripts\tp.exe`。若嫌长，可 `$env:Path="$PWD\.venv\Scripts;$env:Path"` 后直接用 `tp`。

**路线 B —— Web 界面**（不用命令行）：

```powershell
tp serve --root data\work
```

浏览器打开 `http://127.0.0.1:8321/`，在首页上传 EPUB/PDF、选引擎、点「上传并开始翻译」，然后看着进度条跑完，点「校对」进去逐段改，回详情页下载成品。

---

## 3. 操作手册 · 命令行

一共 14 条命令。下面按"你会按的顺序"排。

### 3.1 `tp doctor` —— 环境自检

```powershell
tp doctor
```

无参数。先跑这个，能省掉一半的排查时间。

### 3.2 `tp extract` —— ① 抽取

```powershell
tp extract <书.epub|书.pdf> -o data/work/我的书 [--doc-id 名字]
```

| 选项 | 用途 | 什么时候用 |
|---|---|---|
| `-o, --out` | 输出目录 | 必给。默认 `data/work/book` |
| `--doc-id` | 文档短标识 | 进入段号（`{doc_id}:{block_id}`），**改动会让旧译文 ID 漂移**，一旦开始翻译就别改 |
| `--no-assets` | 不提取图片 | 只想看文字结构时 |
| `--keep-headers` | 不滤页眉页脚 | 结果里混进了正文时（默认会滤掉页码/书眉） |
| `--keep-ruby` | 不剥 PDF 内联注音 | 默认会剥离（**强烈建议保持默认**，见 §6.3） |
| `--limit N` | 预览只输出前 N 块 | 大书快速看结构 |

**产出**：`book.ir.json`（IR）、`preview.md`（**人工检查闸门**）、`assets/`（图片）。

> **先看 `preview.md` 再花钱**。这是全流程唯一的强制人工闸门：结构错了，翻译得再好也白费。
> 预览头部会列出块构成、可翻译段数、**附页归类**（封面/目次/奥付/广告各多少块）。

### 3.3 `tp import` —— ② 入库

```powershell
tp import data/work/我的书
```

把 IR 的可翻译块写进 SQLite。**会自动用内容哈希从 TM 复用旧译文**——同一句话在别的书里译过，这里直接命中、不再花钱。

```
✓ 入库完成：文档 xxx：新建 3401 ｜ 更新 0 ｜ 跳过 22 ｜ 迁移旧译文 0
```

### 3.4 `tp status` —— 看进度与账

```powershell
tp status data/work/我的书
```

### 3.5 `tp summarize` —— ⑩ 滚动摘要（可选，长篇建议开）

```powershell
tp summarize data/work/我的书 --engine deepseek --model deepseek-flash --price-tier idle
```

按章生成 100~200 字的短摘要。之后 `tp translate --rolling-summary` 会把"最近 4 章的前情"注入每一批的提示词，压住长篇的人名/称谓/伏笔漂移。

| 选项 | 默认 | 说明 |
|---|---|---|
| `--budget` | 800 | **注入**前情的总字数上限 |
| `--window` | 4 | 注入最近几章（每章预算 = budget ÷ window） |
| `--chapter-chars` | 6000 | 每章送入模型的原文上限 |
| `--force` | 关 | 默认沿用已有摘要（可断点续跑、不重复花钱） |

### 3.6 `tp translate` —— ③ 翻译

```powershell
# 先看价（不调用任何 API）
tp translate data/work/我的书 --engine deepseek --dry-run --price-tier idle

# 真跑
tp translate data/work/我的书 --engine deepseek --model deepseek-flash `
    --max-cost 3 --price-tier idle --rolling-summary
```

> `--dry-run` 只统计**待译**段落——书已译完时会显示 `批次 0`，这是正常的。

| 选项 | 默认 | 说明 |
|---|---|---|
| `--engine` | fake | `fake`（零成本，返回 `[译]原文`）｜`deepseek`｜`local`（任意 OpenAI 兼容端点） |
| `--model` | 引擎默认 | 如 `deepseek-flash` / `deepseek-v4-pro` |
| `--base-url` | — | `local` 引擎的端点，如 `http://127.0.0.1:8117/v1` |
| `--max-cost` | 0.5 | **成本硬护栏**，达到即停止并保存进度。正式跑记得调大 |
| `--batch-chars` | 2400 | 每批字符预算（长书调到 4000 可减少请求数） |
| `--batch-items` | 24 | 每批段落数上限 |
| `--price-tier` | peak | `peak`（保守，按高峰价）｜`idle`（空闲时段实际价，**约为一半**） |
| `--glossary` | — | 术语表（见 §5.1） |
| `--rolling-summary` | 关 | 按章注入前情（先跑 `tp summarize`） |
| `--limit` | — | 只译前 N 段，调试用 |

**可随时中断再跑**：只处理未完成的段落，不会重复计费。

**内置护栏**：模型若把整段原文原样返回，会自动用更严格的提示词重试；重试仍不行则保留译文并记账（`tp qa` 会报出来）。

### 3.7 `tp qa` —— ⑧b 译文质检

```powershell
tp qa data/work/我的书 [--strict]
```

七类可判定问题：

| 类型 | 含义 |
|---|---|
| `untranslated` | 无译文 / 状态异常 |
| `source_leak` | 译文与原文相同（含假名的原文才算） |
| `kana_left` | 译文残留平假名（≈漏译） |
| `katakana_left` | 残留片假名 |
| `traditional` | 疑似**繁体字或残留日文汉字**（本地小模型常犯） |
| `term_missing` | 术语表里的词没按约定译法出现 |
| `length_anomaly` | 译文长度比异常 |

`--strict` 时有错误即非零退出，可进 CI。

### 3.8 `tp render` —— ⑤ 渲染

```powershell
tp render data/work/我的书 -m bilingual --to both     # 双语对照，供审核
tp render data/work/我的书 -m zh       --to both      # 纯中文终版
```

| 选项 | 说明 |
|---|---|
| `-m bilingual` | 段落级对照（原文灰字在上、译文在下）——**给审核用** |
| `-m zh` | 只输出译文——**终版** |
| `--to epub\|pdf\|both` | 输出格式 |
| `-o` | 输出目录（默认工作目录） |

输出文件名：`{doc_id}.{mode}.epub` / `{doc_id}.{mode}.pdf`。
EPUB 会带封面页（书脊首位 + `cover-image` 声明 + EPUB2 兼容 meta）；PDF 是 A5 单栏、思源宋体正文 / 黑体标题、每章另起、自动目录、页脚页码。

### 3.9 `tp validate` —— ⑨ EPUB 校验

```powershell
tp validate data/work/我的书 [--strict]
```

两层：**内置结构检查**（零依赖，永远可跑）+ **epubcheck 官方**（装了就跑）。
内置检查覆盖 mimetype 位置与压缩方式、container→OPF、清单/书脊自洽、悬空引用、XHTML 良构、封面三类声明、目录穿越。

> **epubcheck 怎么装**：`python tools/fetch_epubcheck_lib.py`（从 Maven Central 递归装配 33 个 jar 到 `Z:\Tools\epubcheck-lib`，需要 Java）。
> 官方发行包在 GitHub 上下不动（实测 25 分钟 0 字节），所以走 Maven Central。

### 3.10 审核回流：`export-review` / `apply-review` / `clear-review`

```powershell
tp export-review data/work/我的书 -f tsv -o review.tsv   # 导出
#   …用 Excel / VS Code 改第 4 列（translation），保存…
tp apply-review data/work/我的书 review.tsv               # 回灌（自动备份数据库）
tp clear-review data/work/我的书 --yes                    # 回滚：清空全部人工定稿
```

- 导出**只让你改译文列**，未改的行原样保留；留空的行跳过。
- 回灌**只写被修改过的行**，写入 `final_translation`（**不覆盖机翻** `translation`）。
  回灌前会自动把数据库备份成 `translations.db.bak-<时间戳>`（可用 `--no-backup` 关掉）。
- 渲染时优先取定稿。所以"改错了"随时能回滚到机翻。
- `clear-review` 会清空**所有**人工定稿，所以要显式加 `--yes`。
- `-f md` 导出只读双语 Markdown，适合通读。

### 3.11 `tp terms` —— ⑧a 术语预扫描

```powershell
tp terms data/work/我的书 --min-count 3 --top 400     # 输出 terms.candidates.tsv
#   …填第 3 列（译法），删掉不要的行，另存为 configs/glossary.tsv…
tp translate data/work/我的书 --glossary configs/glossary.tsv
```

术语表支持两种格式：`.json`（对象）或每行 `原文=译文` 的文本 / TSV。

### 3.12 `tp compare` —— ⑪ 引擎对比

```powershell
# 先起本地端点（llama.cpp）
Z:\AgentHub\engines\llama.cpp\llama-server.exe -m Z:\AgentHub\models\gguf\Qwen3-8B-Q5_K_M.gguf `
    --host 127.0.0.1 --port 8117 -c 8192 -ngl 99 -fa on

tp compare data/work/我的书 --chapter 3 --limit 40 --sample 4
```

同一批段落跑两个引擎，给成本 / 速度 / 可判定质量 / 逐条对照。

### 3.13 `tp setup` —— 首次配置（密钥）

```powershell
tp setup                       # 已配置就跳过；没配置就交互式问一次
tp setup --key sk-xxxx         # 直接给出，不问
tp setup --force               # 已配置也重新问（换密钥时用）
tp setup --yes                 # 非交互：没配置也不问，直接跳过
```

把 `DEEPSEEK_API_KEY` 写进 `.env`（保留文件里其它内容）。发布包里的 `start.cmd` 会自动调用它，
所以从压缩包安装的用户不必手敲命令。它存在的另一个理由：让"问密钥"这件**要显示中文**的事
由 Python 来做——批处理里的中文在不同代码页下会乱码，Python 走 Windows 控制台 Unicode API 不会。

`.env` 的查找顺序（**先命中的胜出**）：

1. `$TRANSBOOK_ENV` 指定的路径
2. 项目根（源码树）的 `.env`
3. **当前工作目录**的 `.env`
4. `%APPDATA%\transbook\.env`

第 3 条是给发布包准备的：装成 wheel 后"项目根"会算成 `site-packages` 的上一级，那里不会有
`.env`，而用户是把密钥放在启动目录里的。**少了这一条，用户配了密钥也不会生效。**

---

## 4. 操作手册 · Web 界面

```powershell
tp serve --root data\work            # 界面 http://127.0.0.1:8321/ ｜ 接口文档 /docs
tp serve --root data\work --open     # 起好之后自动开浏览器（发布包的 start.cmd 用这个）
```

| 页面 | 能干什么 |
|---|---|
| **项目列表** `#/` | 上传 EPUB/PDF（选引擎、输出模式）；查看已有项目与产物 |
| **项目详情** `#/p/<doc>` | 统计看板；一键翻译/摘要/渲染；**实时进度条**；取消作业；下载产物；最近作业 |
| **段落校对** `#/p/<doc>/review` | 左右对照（左原文只读、右译文可编辑）；搜索/状态过滤/分页；逐段或整页保存；撤销定稿 |

作业在**独立子进程**里跑：关掉浏览器页面不影响执行，重启服务也不丢进度。

> **前端开发模式**（改界面时用，热更新）：
> ```powershell
> cd web
> npm run dev        # http://127.0.0.1:5173，/api 自动代理到 8321
> ```
> 改完必须 `npm run build` 才会反映到 `tp serve` 的界面上。

---

## 5. 专项功能

### 5.1 术语表

保证人名/专有名词全书统一。三步：

```powershell
tp terms data/work/我的书 --top 300          # 1. 抽候选
# 2. 填译法（第 3 列），删掉噪声
tp translate data/work/我的书 --glossary configs/glossary.tsv   # 3. 翻译时强制注入
tp qa data/work/我的书 --glossary configs/glossary.tsv          # 4. 事后核查一致性
```

### 5.2 使用本地模型

任何 OpenAI 兼容端点都能用（llama.cpp / Ollama / vLLM）：

```powershell
tp translate data/work/我的书 --engine local --base-url http://127.0.0.1:8117/v1 --model qwen3-8b
```

本地端点**不计费**（成本护栏不会误停）。

> ⚠️ **实测结论：本地 Qwen3-8B 不适合作主译**。见 [§6.5](#65-引擎对比m3)。

### 5.3 断点续跑与回滚

| 场景 | 做法 |
|---|---|
| 翻译中断 | 重跑同一条命令，只处理未完成的段落 |
| 换了抽取逻辑 | 重新 `extract` + `import`，译文凭内容哈希自动迁移 |
| 改了提示词 | 提示词版本随译文入库，可只重译受影响段落 |
| 审核改错 | `tp clear-review`（全清）或界面里单段「撤销定稿」 |

---

## 6. 验收数据

> 全部为**真实样书实测**，非估算。样书：`Re:ゼロから始める異世界生活` 43 / 44 卷
> （EPUB + 竖排 PDF 双版本，正好可互为真值）。

### 6.1 M0 环境与选型

| 项目 | 结论 |
|---|---|
| uv 缓存重定向 | ✅ 到 `Z:\_cache\uv` |
| 样书结构识别 | NAV 锚点 **16/16 命中**，标题文本 16/16 一致 |
| ruby 剥离 | 剥离 `<rt>` 前 5 处标题对不上，剥离后 **0 处** |
| PDF 引擎 | **pypdfium2**（与 PyMuPDF 结果一致、快 3 倍、可读书签）；pdfminer 因 Type3 字体淘汰（只能抽 3%） |
| PDF 排版引擎 | **Typst**（WeasyPrint 在本机 Windows 缺 GTK 直接失败） |
| 本地模型吞吐 | Qwen3-8B-Q5_K_M：**GPU 32.2 tok/s** / CPU 5.31 / `-ngl 16` 10.18 |
| 整本翻译成本 | 3401 段 → **¥0.9054**，16 分钟，0 失败（约预算的 3%） |

### 6.2 EPUB 抽取（43 卷）

| 指标 | 值 |
|---|---|
| 块 | 3413（标题 13 / 段落 3388 / 图片 22） |
| 可翻译 | 3401 |
| 目录 | 13 条 |
| 剥离注音 | 1977 处 `<rt>` |

### 6.3 PDF 抽取保真（与 EPUB 真值逐段比对）

竖排 PDF 的两大坑：**内联振假名**与**CJK 间空格**。逐项修掉的收益：

| 指标 | 初始 | 剥离振假名后 | 再压 CJK 空格后 |
|---|---|---|---|
| 整书字符相似度 | 0.9543 | 0.9955 | **0.9974** |
| 去空白精确命中 | 58.9% | 75.1% | **86.6%** |
| **含空格逐字符命中** | 50.5% | — | **84.6%** |
| 仅 PDF 有的段 | 1240 | 737 | **394** |
| CJK 间空格残留 | 42.0% | 42.0% | **0%** |

不剥注音时送进翻译的是这种被切碎的日文：

```
EPUB（真值）：手強い / 効果覿面
PDF（修复前）：手て強ごわい / 効こう果か覿てき面めん
```

页眉/页脚过滤实测：

| 书 | 结果 |
|---|---|
| 竖排日文 PDF | 段尾数字 **40 → 0**、纯数字段 **5 → 0**；正文 `「──── 」` **57 → 57 零误伤** |
| 英文 PDF | 段尾页码 **14 → 0**，其余段落零改动 |

### 6.4 PDF 输入路径端到端（43 卷竖排 PDF）

| 阶段 | 结果 |
|---|---|
| 抽取 | 3356 块 = 标题 11 / 段落 3317 / **图片 28** ｜ 目录 11 条 |
| 翻译 | **3328 段全译**，111 批，**0 失败**，**¥0.7567** |
| QA | 3349 段，错误 1（`「べ」` 拟声片段，可接受） |
| 输出 | EPUB(zh) 14.75 MB ｜ EPUB(双语) 14.94 MB ｜ PDF 268 页 15.22 MB |
| 图片 | **28/28 全部被 XHTML 引用** |
| 段落覆盖 | **100%** |

### 6.5 引擎对比（M3）

同一批 40 段，同参数：

| 引擎 | 耗时 | 速度 | 花费 | 可判定问题 |
|---|---|---|---|---|
| DeepSeek flash | 9.5 s | **152.8 字符/s** | ¥0.0085 | **0** |
| 本地 Qwen3-8B | 70.3 s | 19.9 字符/s | ¥0 | 3 繁体 + 8 片假名残留 + 1 假名 + 2 未译 |

**结论**：本地 8B **不适合作主译**——整段输出繁体（`一章『氷上決戰』`「異常事態」），且慢 7.7 倍。成本上也没必要（见 §8）。

### 6.6 滚动摘要（M3）

| 指标 | 值 |
|---|---|
| 样书 | 43 卷，10 章 |
| 每章摘要字数 | 118 ~ 158 字（预算 200 内） |
| 成本 | **¥0.041** |

### 6.7 输出与校验（M4）

| 指标 | 值 |
|---|---|
| epubcheck 官方校验 | 4 个成品全部 **0 错误 / 0 警告** |
| 封面 | 两本书都命中并写入（PDF 版 `p0001_1.jpg`、EPUB 版 `cover.jpg`） |
| 残余假名（纯中文 PDF） | 59 / 120,549 字 = **0.049%** |
| 术语一致性 | 蕾姆 98 次 / 阿尔 814 次 / 昴 175 次（全书统一） |

### 6.8 Web 界面（M6）

Playwright 真实浏览器实测（43 卷，3349 段）：

| 检查项 | 结果 |
|---|---|
| 项目列表 | 4 项，产物文件名真实 |
| 详情页统计 | 块 3244 / 可翻译 3216 / 已译 3349 / 封面 `p0001_1.jpg` / 花费 ¥0.8242 |
| 校对页 | 3349 段分 84 页，左右对照正常 |
| **界面改一段 → 保存** | SQLite 落库、**机翻未被覆盖**、新定稿可搜索 |
| 撤销定稿 | 回到机翻、标记消失 |
| 产物下载 | EPUB 200/`PK`、PDF 200/`%PDF-…%%EOF` |
| 目录穿越 | 404 |
| 浏览器 console | **0 条错误** |

### 6.9 测试

```
295 passed ｜ 0 failed
```

覆盖抽取（EPUB/PDF/表格/脚注/注音/页眉页脚）、存储与 TM、翻译编排与护栏、渲染（EPUB/PDF）、QA、审核回流、滚动摘要、引擎对比、服务与 Web、以及**发布形态**（打包相关回归见 §11）。**全部零成本**（用 Fake 引擎与合成夹具，不调用 API）。

### 6.10 交付前回归：一本全新的书（44 卷）

> 写这份文档时，为验证"手册里的命令在**没见过的新书**上同样成立"，用 44 卷 EPUB
> 从零跑了一遍完整流程。**它不是之前调参用过的书**，因此这组数据可作为独立回归证据。
>
> 复现命令（脚本会逐阶段计时、留日志、汇总）：
> ```powershell
> python tools\acceptance_run.py "D:\books\ReZero-44.epub" `
>     --work data/work/re0-v44 --doc-id re0-v44 --engine deepseek --rolling-summary
> ```

**八个阶段全部成功（退出码 0）**：

| 阶段 | 耗时 | 结果 |
|---|---|---|
| ① 抽取 | 1.0 s | 块 3458 = 标题 11 / 段落 3424 / 图片 23 ｜ 目录 11 条 ｜ 可翻译 **3435** ｜ 剥离注音 1910 |
| ② 入库 | 0.9 s | 3435 段 |
| ⑩ 摘要 | 16.4 s | **9 章**，每章 46~179 字，**¥0.0336** |
| ③ 翻译 | **704.8 s**（11.7 min） | 115 批 ｜ **3435 段全译** ｜ **0 失败** ｜ 重试补齐 36 ｜ token 入 256,515 / 出 128,325 ｜ **¥0.7774** |
| ⑧ QA | 0.8 s | **错误 0** ｜ 警告 1（一处残留片假名，1/3435） |
| ⑤ 渲染 zh | 2.0 s | PDF 269 页 / 23 图 ｜ EPUB 14.28 MB ｜ 段落覆盖 100% |
| ⑤ 渲染 双语 | 1.0 s | EPUB 14.47 MB ｜ 段落覆盖 100% |
| ⑨ 校验 | 13.0 s | **epubcheck 0 错误 / 0 警告** |

**产物复核**：

| 指标 | 值 |
|---|---|
| 图片 | **23/23 全部被 XHTML 引用**，封面已声明 `cover-image` |
| 纯中文 PDF | 269 页 / 126,584 字 / 残留假名 58（**0.046%**） |
| 术语一致性 | 蕾姆 109 次 / 爱蜜莉雅 56 次 / 昴 265 次 / 贝蒂 35 次（全书统一） |

**这次回归顺手挖出并修掉了三个真实缺陷**（都不是猜的，是跑出来的）：

| # | 缺陷 | 症状 | 修法 |
|---|---|---|---|
| ① | **滚动摘要进度回调签名不一致** | `generate_summaries` 用单参调用、调用方传双参 lambda → 第一次回调抛 `TypeError`，整轮只生成 1 章就中断。表现为「摘要 2.5 秒只出 1 条，且是『表紙』」 | 统一为 `(message, fraction)`，并加**签名一致性回归测试** |
| ② | **分章只看标题层级、不看标题自身的附页类别** | 「表紙」「CONTENTS」被当成章节写摘要；夹在两者之间的版权页文字还被算进「表紙」那一章 → 给版权声明写了一份"前情提要" | 非正文标题不开启新章 |
| ③ | **JSON 解析失败直接终止整批且不重试** | 一本书白丢 **30 段**（错误 `Expecting ',' delimiter`） | 把格式错误视为**可重试**（换强指令提示词）；网络/鉴权类错误仍立即失败不空等 |

> ① 是 M5 期间批量替换 lambda 时引入的**回归**——单测没覆盖"回调签名与调用方一致"，
> 只有真实跑一本书才会暴露。这三条都补了回归测试，测试数 285 → 287。

**这次回归的完整数据**留档在 `data/work/re0-v44/_acceptance.json` 与 `_log_*.txt`。

---

## 7. 目录结构与产物

```
Translation_Engineering/
├── src/transbook/           # 全部源码
│   ├── ingest/              # ① 抽取：epub.py / pdf.py / preview.py
│   ├── ir/models.py         #   DocumentIR —— 阶段之间唯一的契约
│   ├── store/db.py          # ② 段落表 / TM / 摘要 / 任务表
│   ├── translate/           # ③ 翻译：base / prompts / deepseek / fake / runner / summary
│   ├── quality/             # ⑧ 术语抽取、译文 QA、引擎对比
│   ├── review/              # ⑦ 审核回流（TSV/Markdown 导出与回灌）
│   ├── render/              # ⑤ 渲染：xhtml / epub / pdf
│   ├── service/             # M5/M6 服务层：api / jobs / pipeline / runner
│   ├── validate.py          # ⑨ EPUB 校验
│   ├── cli.py               # 全部命令
│   └── textutil.py          # 文本清洗原语（注音/连字/CJK 空格/附页归类）
├── web/                     # M6 前端（React + TS + Vite）
│   ├── src/components/      # 项目列表 / 详情 / 进度 / 上传 / 段落校对
│   └── dist/                # 构建产物（gitignore，由 npm run build 生成）
├── tests/                   # 287 个测试
├── tools/                   # 辅助脚本
│   ├── acceptance_run.py    #   端到端验收（逐阶段计时 + 汇总，见 §6.10）
│   ├── fetch_epubcheck_lib.py  # 从 Maven Central 装配 epubcheck
│   ├── golden_diff.py       #   PDF 抽取 vs EPUB 真值比对
│   └── …                    #   探针与基准（版式/注音/页脚）
├── docs/                    # plan.md（方案）/ m0-report.md / DELIVERY.md（本文）
├── data/work/<书>/          # ← 每本书一个工作目录
│   ├── source.epub          #   输入（Web 上传时保存）
│   ├── book.ir.json         #   ① 抽取产物（唯一真源）
│   ├── preview.md           #   ① 人工检查闸门
│   ├── assets/              #   图片
│   ├── translations.db      #   ②③④ 段落表 + TM + 摘要 + 任务
│   └── <doc>.<mode>.{epub,pdf}   # ⑤ 成品
└── PROJECT-MEMORY.md        # 开发记忆（决策 D-001…D-055）
```

**`data/` 全部不进版本库**（体积大 / 可再生 / 含第三方版权内容）。

---

## 8. 成本

| 项目 | 实测 |
|---|---|
| DeepSeek flash 单价（空闲时段） | 输入 ¥1/百万 token，输出 ¥4/百万；**高峰 ×2** |
| 整本 EPUB（3401 段） | **¥0.9054**（16 分钟） |
| 整本竖排 PDF（3328 段） | **¥0.7567** |
| 每百万源字符 | ≈ **¥5.83** |
| 滚动摘要（10 章） | ¥0.041 |
| 本地模型 | **¥0**（只有电费） |
| 你的预算 ¥20~30/本 | 实际占用 **3% ~ 4%** |

省钱要点（按收益排序）：

1. **`--price-tier idle`**：DeepSeek 空闲时段是高峰价的一半。
2. **TM 自动复用**：同一句话在别的书里译过就直接命中，不花钱。第二本书开始明显下降。
3. **`--dry-run` 先看价**：不调用任何 API。
4. **`--batch-chars 4000`**：提示词前缀（术语表、前情）按批摊销，批越大越省。
5. **先 Fake 后真实**：`--engine fake` 零成本验证整条链路。

---

## 9. 故障排查

### 9.1 环境类

| 症状 | 原因与解法 |
|---|---|
| `pnpm` 报 "not a valid application" | 本机 pnpm 的 exe 被错误硬链接成了 POSIX 二进制（C→Z 迁移后遗症）。**前端一律用 npm** |
| Vite 构建报 EPERM / `spawn` 失败 | 沙箱禁止子进程管道 stdio，而 Vite 会 `exec('net use')` 探测网络盘。构建需要放宽沙箱 |
| `curl` TLS 失败 `SEC_E_NO_CREDENTIALS` | 沙箱内的已知现象，**不是网络故障**。用 Node 的 `fetch` 或 Python `urllib` 测连通性 |
| GitHub 下载卡死 | 直连与多数代理都不通。模型走 **ModelScope**，其他走 `gh-proxy.com` / `ghfast.top`；epubcheck 走 **Maven Central** |
| 找不到 `epubcheck` | 跑 `python tools/fetch_epubcheck_lib.py`，并确认 `java -version` 可用 |

### 9.2 抽取类

| 症状 | 原因与解法 |
|---|---|
| 段落数远少于预期 | 检查是否把「无文字页」提前跳过了（纯插图页也会被判为空）。本项目已修 |
| 结果里混进页码/书眉 | 默认已过滤；若误伤正文，用 `--keep-headers` 关掉过滤 |
| 竖排书正文被切碎 | 注音没剥干净。确认没加 `--keep-ruby` |
| PDF 段落少得离谱 | 若你改过抽取逻辑，注意**逐页状态（字符高度）必须逐页保存**——用错页的会让段落判定整体偏移 |
| 图片没进成品 | 打包 ≠ 显示。用 `tp validate` 查「是否有图片只打包未被引用」 |

### 9.3 翻译类

| 症状 | 原因与解法 |
|---|---|
| `未配置 DEEPSEEK_API_KEY` | 复制 `.env.example` 为 `.env` 并填密钥 |
| 达到成本上限提前停止 | 正常护栏。调大 `--max-cost` 或加 `--price-tier idle` 重跑（已译段落不会重复计费） |
| 少量段落 `failed`，错误是 `无法解析模型输出` | 模型偶发吐出非法 JSON。**会自动重试**；重试仍失败则标记失败，**重跑一次同一条命令即可补齐**（只处理未完成段落） |
| `tp qa` 报大量 `kana_left` | 若用的是 `--engine fake`，这是**预期**的（Fake 返回原文加前缀）。换真引擎 |
| 译文残留繁体字 | 本地小模型的常见问题，用 `--engine deepseek`；或在术语表里锁住关键词 |
| 术语不一致 | 先 `tp terms` 建表，再 `--glossary` 注入，最后 `tp qa --glossary` 核查 |
| 摘要只生成了几章 | 某一章调用失败会跳过并继续。看输出里的「失败 N」；重跑 `tp summarize` 会自动补上缺的章 |

### 9.4 服务与界面类

| 症状 | 原因与解法 |
|---|---|
| `tp serve` 说「界面未构建」 | 到 `web/` 跑 `npm install && npm run build` |
| 页面白屏 | 看浏览器 console；多半是构建产物过期，重新 build 并强制刷新 |
| 作业一直 `running` | 进程被杀了。重启服务时会自动把僵尸作业收尸为 `failed` |
| 提交后毫无反应 | 看 `GET /api/jobs`；若为 `failed`，`GET /api/jobs/{id}` 的 `message` 里有完整堆栈 |

### 9.5 校验类

| 症状 | 原因与解法 |
|---|---|
| `nav 文档未列入书脊`（警告） | **本项目的有意设计**：书自带目次页，再加 nav 会重复。不影响 epubcheck 合规 |
| epubcheck 未运行 | 未安装。内置检查仍会跑，且会**明说**没跑 epubcheck，不会假装通过 |

---

## 10. 已知限制与未做项

**明确不做**（计划书 §最末已列）：图形界面之外的协作功能、原位保版式替换（输出是重排版，不是覆盖原文位置）、实时多人协作、图片内文字翻译/语音字幕。

**已知限制**：

1. **PDF 表格不还原结构**。EPUB 的表格已支持（保留行列与合并单元格、单元格级翻译）；从 PDF 版面推断表格是另一件工程，未做。你的样书里 PDF 与 EPUB 都没有表格。
2. **PDF 脚注不做识别**。EPUB 的脚注已支持（`aside[epub:type=footnote]` → 独立块 + 跳转上标）；PDF 脚注靠字号+位置推断，**两本样书都没有成体系的脚注**，所以没有做无法验证的检测器。
3. **英文 PDF 的跨行断词**用启发式（`trans-` + 小写字母则拼接）。`well-` 换行接 `known` 这类真连字符会被误合，属已知取舍。
4. **繁体字检测是启发式**（繁体专用字 + 日文专用汉字，阈值 2）。单个繁体字可能是刻意保留的人名用字，故不报。
5. **计数与实测口径**：`tp translate --dry-run` 的预估约为实际的 **1.7 倍**（系统提示词与真实 token 数开销），保守可用。
6. **输出被重定向时，GBK 表达不了的字符会降级成 `?`**。Windows 上 stdout 被管道或文件捕获时按 ANSI 代码页编码，而 GBK 里没有 `✓`(U+2713)、`⑪`(U+246A) 这些字符。已在 `cli.py` 加了全局兜底（`errors="replace"`），所以只会显示降级、不会中断命令；直接输出到真实控制台时不受影响。**加兜底之前，`tp ... | ...` 和 `tp ... > log.txt` 会直接以非零码退出**（实测确认）。
7. **`src/transbook/translate/summary.py` 有一个从未接线的函数**：`build_compress_messages` 引用了未定义的 `COMPRESS`，且全仓库没有任何地方调用它（所以测试全绿也发现不了）。它属于早期"累积式压缩"方案的遗留物，而下方 `build_summary_messages` 的注释明确说明后来放弃了那条路线。保留未删是因为它记录了当时的设计意图；需要时再决定实现还是移除。
8. **ruff 有 85 个存量告警**（`src` 59 / `tests` 30），其中 23 个是 B008——Typer/FastAPI 默认参数的惯用法，属工具误报。因此 CI 里的 ruff 步骤是**信息性、不阻断**的；清理是独立的一件事，不宜混在功能改动里。

**验证覆盖的边界**：

- 日文竖排 PDF、日文 EPUB、英文横排 PDF 都实测过；**中→外、其他语种未验证**。
- 长篇小说（3400 段）实测；超长篇（10 万段以上）未验证。
- 全部实测在 Windows 上完成；Linux/macOS 未验证。

---

## 11. 分发与打包

前面十节讲的是"在源码树里怎么用"。本节讲**怎么把成品送到另一台机器上**——
目标是那台机器没装过任何开发工具，用户也不需要碰命令行。

### 11.1 两种交付形态

| 形态 | 拿到什么 | 目标机器需要预装 | 适合谁 |
|---|---|---|---|
| **源码**（`git clone`） | 仓库 | Python 3.12 + uv + Node（自行构建界面）+ 会敲命令行 | 继续开发的人 |
| **发布包**（Release zip） | `start.cmd` + wheel | **什么都不用预装** | 只想用的人 |

关键差异：`web/dist`（前端产物）**不进 git**——带内容哈希的文件名会让每次构建产生一堆无意义
diff。所以 `git clone` 得到的源码**没有界面**，必须自己装 Node 构建一次。发布包则把前端
**嵌进了 wheel**，装完即有界面。这正是"装包即用"成立的前提。

### 11.2 前端是怎么进 wheel 的

`hatch_build.py`（hatchling 构建钩子）在打包时把 `web/dist` 映射成 wheel 内的
`transbook/web/dist`。该路径恰好落在 `service/api.py::find_web_dist()` 的向上查找链上
（`site-packages/transbook/` 之下），所以运行期不需要任何额外配置就能找到界面。

> **为什么不用 pyproject 里的 `force-include` 表**：那张表在前端没构建时直接抛
> `FileNotFoundError`，而全新 clone 恰恰没有 `web/dist`——结果是 `uv sync` 和 `uv build`
> 双双失败，开发流程直接断掉。已实测确认。钩子改成"有就嵌入、没有就跳过并告警"，
> 开发流程永远可用；发布包的完整性由构建脚本复核。

### 11.3 打一个发布包

```powershell
python tools\build_release.py                  # 前端 → wheel → Release zip
python tools\build_release.py --skip-frontend  # 前端没改时省时间
```

产出（都在 `dist/`，该目录不入库）：

| 文件 | 内容 | 体积 |
|---|---|---|
| `transbook-0.1.0-py3-none-any.whl` | 主产物，**前端已内嵌** | 204 KB |
| `transbook-0.1.0-win64.zip` | `start.cmd` + wheel + `README.txt` | 205 KB |

脚本最后会**拆开 wheel 复核里面真有界面**。只检查构建退出码是不够的：钩子在找不到
`web/dist` 时会跳过并告警，构建照样成功，产物却是个没界面的壳。

> 注意：wheel 只有 200 多 KB，但它依赖的组件（含 Typst 排版引擎，单它一个就 59 MB）
> 要在安装时下载，**装完约占用 150 MB 磁盘**。首次安装需要联网。

### 11.4 在另一台机器上装（用户视角）

解压 zip，双击 `start.cmd`。它会依次自动完成：找 uv（没有就用官方脚本装进用户目录，
不需要管理员权限）→ `uv tool install` 装本包 → `tp setup` **用中文**问一次 API Key →
`tp serve --open` 起服务并打开浏览器。

`start.cmd` **刻意只写 ASCII**：cmd.exe 按当前代码页解码批处理文件的字节，简中机器上是
GBK，中文会乱码；而 Python 走 Windows 控制台 Unicode API，不受代码页影响。所以面向用户的
中文全部在 `tp setup` / `tp serve` 里，中文文档在 `README.txt` 里。
（实测过 UTF-8 无 BOM、UTF-8 带 BOM、UTF-8+`chcp 65001` 三种写法，重定向后字节完全相同，
**无法靠选编码保证安全**，所以走了这条结构性方案。）

### 11.5 自动发布（GitHub Actions）

`.github/workflows/release.yml`：推 `v*` tag 时自动跑测试 → 构建 → 上传产物 → 建 Release，
用的是仓库自带的 `GITHUB_TOKEN`，**不需要配置任何 PAT 密钥**。
`.github/workflows/ci.yml` 在 push / PR 上跑测试与静态检查。

```powershell
git tag v0.1.0
git push origin v0.1.0        # 之后全自动
```

### 11.6 验证发布包是可用的（可复现）

```powershell
powershell -File tools\verify_install.ps1
```

14 项检查，全过才算数。它刻意把沙箱建在 `%TEMP%`：**在项目目录里测是测不出问题的**——
那里本来就有 `web/dist`，即使前端没打进 wheel 也会"看起来正常"。而且只看退出码也不够：
界面缺失时服务照样起得来，只是首页 404。所以脚本真的发 HTTP 请求并核对响应内容。

实测结果（本机，2026-09-17）：

| 检查项 | 结果 |
|---|---|
| 沙箱在项目之外 | PASS |
| wheel 装进干净 venv | PASS |
| 导入的是 `site-packages` 而非源码 | PASS |
| 工作目录及全部上级都无 `web/dist` | PASS |
| `find_web_dist()` 命中**包内嵌**路径 | PASS（`…\site-packages\transbook\web\dist`） |
| `.env` 写到工作目录、`doctor` 认到密钥 | PASS |
| 首页 HTTP 200 且是 HTML | PASS |
| 首页引用打包的 JS、静态资源可取 | PASS（237,600 字节） |
| `/api/projects` 可用 | PASS |
| 启动日志中无"界面未构建" | PASS |

**通过 14 项，失败 0 项。**

---

## 附：一页速查

```powershell
# 装
uv sync; tp setup                 # setup 会问一次 DEEPSEEK_API_KEY，写进 .env
cd web; npm install; npm run build; cd ..      # 从源码跑界面才需要；用发布包则跳过

# 跑（命令行）
tp extract "书.epub" -o data/work/X     # ① 抽取 → 先看 preview.md！
tp import  data/work/X                  # ② 入库
tp summarize data/work/X --price-tier idle            # ⑩ 摘要（长篇建议）
tp translate data/work/X --engine deepseek --price-tier idle --rolling-summary --max-cost 3
tp qa      data/work/X                  # ⑧ 质检
tp render  data/work/X -m bilingual --to both         # ⑤ 双语，审核
tp render  data/work/X -m zh --to both                # ⑤ 纯中文终版
tp validate data/work/X                 # ⑨ 校验

# 跑（界面）
tp serve --root data/work               # → http://127.0.0.1:8321/
tp serve --root data/work --open        # 顺便自动开浏览器

# 审核
tp export-review data/work/X -f tsv -o r.tsv   # 改译文列
tp apply-review  data/work/X r.tsv             # 回灌
tp clear-review  data/work/X                   # 回滚

# 打包发布
python tools\build_release.py           # → dist\*.whl + dist\*-win64.zip
powershell -File tools\verify_install.ps1   # 14 项开箱即用检查
git tag v0.1.0; git push origin v0.1.0      # CI 自动构建并发布 Release
```
