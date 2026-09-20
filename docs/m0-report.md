# M0 实测报告（进行中）

**日期** 2026-09-17　**状态** ✅ **7/7 全部完成**（M0 收尾）
**对应计划书** `docs/plan.md` §8（M0）与 §12（七项实测）

---

## 1. 环境落地（✅ 完成）

| 项 | 结果 |
|---|---|
| uv | **0.12.17**，装于 `Z:\Python\Python_3_12_1\Scripts\uv.exe`（走 PyPI；该 Python 无 pip，已用 `ensurepip` 自举） |
| 项目 venv | `Z:\AgentProjectHub\Translation_Engineering\.venv`（**55 MB，1550 文件**，在 Z 盘） |
| 依赖锁定 | `uv.lock` ✅（pydantic 2.13.5 / typer 0.27.2 / httpx / lxml / bs4 / rich 15.0.0；dev: pytest 9.1.1 / ruff 0.16.8） |
| CLI | `tp --version` / `tp version` / `tp -V` / `tp doctor` 均可用 ✅ |
| git | 仓库已初始化（`git init`），身份取自全局配置 |
| 缓存落盘 | `UV_CACHE_DIR=Z:\_cache\uv`，**C 盘无 uv 缓存** ✅ |
| 目录 | `Z:\Tools`、`Z:\AgentHub\{models,engines,serve}` 已建立 |

环境变量（用户级，已固化）：`UV_CACHE_DIR`、`HF_HOME`、`TORCH_HOME`、`OLLAMA_MODELS`、`HF_ENDPOINT=https://hf-mirror.com`；PATH 追加 Python Scripts。

### ⚠️ 开发摩擦（须知）
沙箱是 `workspace-write`，因此**我执行 `uv sync` / 写 `Z:\_cache` 必须申请提权**。
你本人直接在终端跑则无此限制。若想让我零摩擦开发，可把 uv 缓存改为工作区内路径——但会偏离"缓存统一放 `Z:\_cache`"的约定，暂不改。

### 网络实测（重要）
| 目标 | 结果 |
|---|---|
| HuggingFace 官方 `huggingface.co` | ❌ **不可达** |
| **`hf-mirror.com`** | ✅ 可用（已列出国 `Qwen3-8B-GGUF` 的 5 个量化：Q4_K_M / Q5_0 / Q5_K_M / Q6_K / Q8_0） |
| GitHub API / releases | ✅ 可用（llama.cpp `b11053` 有 13 个 Windows 资产） |
| DeepSeek API 端点 | ✅ 可达（harness 在用） |
| `ollama.com` | ✅ 可达（安装包 1.5 GB） |

→ **结论**：模型下载走 **hf-mirror**；本地运行时选 **llama.cpp**（比 Ollama 小得多，且 `-ngl`/`--n-cpu-moe` 正是测 14B 部分卸载所需）。

---

## 2. 实测 #2：NAV 结构识别原型（✅ 完成，最高优先级）

背景：样书 `<h1>`~`<h6>` 计数为 **0**，原以为要靠字体/类名启发式。

### 实测结论（两本样书共 22 条目录）

1. **章节与文件一一对应**：每个 `p-00N.xhtml` 就是一章；NAV 给出 `(标题, 文件#锚点)`。
2. **章节标题 = `p.bold.mfont.font-110per`，其 `id` 恰是 NAV 的锚点**（如 `#toc-002`）。
3. 逐条验证结果：

| 指标 | 43 卷 | 44 卷 | 合计 |
|---|---|---|---|
| 目录条目 | 12 | 10 | 22 |
| 锚点缺失 | **0** | **0** | **0** |
| 文件缺失 | **0** | **0** | **0** |
| 带锚点条目定位成功 | 9/9 | 7/7 | **16/16 = 100%** |
| 标题与 NAV 完全一致 | 9/9 | 7/7 | **16/16 = 100%** |
| 无锚点条目（表紙 / CONTENTS / 奥付） | 3 | 3 | 6（前后附页，非章节） |

→ **结构还原可以做到"零启发式"**：NAV 锚点定位，不需要字号统计，也不需要 ML 版面模型（对 EPUB 而言）。

### 附带发现：`<rt>` 必须剥离（否则标题都对不上）
日文电子书**逐字注音**很常见：`<ruby>幕<rt>まく</rt>間<rt>あい</rt></ruby>`。
剥离前 NAV `幕間` vs 正文 `幕まく間あい`（不一致 5 处）；剥离后**不一致 0**。
→ 已实现 `text_no_rt()`，写入 `tools/nav_anchor_check.py`，M1 直接复用。

### 正文标记画像（`p-003.xhtml` 实例）
```
body.vrtl.p-text                    ← vrtl = 竖排标记；p-text = 正文
  div.main
    div.start-1em
      p.bold.mfont.font-110per#toc-002   ← 章节标题（粗体 + 110% 字号）
    p（空, 含 br）×3                  ← 仅用于行距，需丢弃
    div.h-indent-5em > p ▸ '１'       ← 场景编号（非标题）
    p（裸标签）…                      ← 正文段落
    p ▸ '…' with <ruby>…<rt>…</rt></ruby>
```
要点：
- 正文段落是**裸 `<p>`**（无类名）；空段落需剔除以免污染翻译
- 语义类名极简（全书画像：`p` 3650、`rt` 1977、`ruby` 1459、`br` 370、`span.tcy` 125、`div.h-indent-5em` 75、`div.start-4em` 39、`div.main` 25）
- `span.tcy` = 縦中横（竖排中的横排数字）；`line-break-loose` / `word-break-break-all` = 竖排换行控制
- **竖排仅体现为 CSS 与类名**，文字层正常 → 印证 D-015（竖排 EPUB 不是难题）

**产出工具**：`tools/epub_probe.py`、`tools/nav_probe.py`、`tools/dump_xhtml.py`、`tools/nav_anchor_check.py`

---

## 3. 实测 #3/#4：本地模型（✅ 完成）

**部署（全部落 Z 盘）**
- 运行时：**llama.cpp Vulkan build `b11053`** → `Z:\AgentHub\engines\llama.cpp`（86 MB，Vulkan 包仅 30 MB，
  对比 CUDA 包 615 MB **省 20 倍带宽**）
- 模型：**Qwen3-8B-Q5_K_M.gguf**（5.45 GB，GGUF 头校验通过）→ `Z:\AgentHub\models\gguf`
- 下载源：**ModelScope**（HuggingFace 官方不可达、hf-mirror 会停滞）

**吞吐实测（llama-bench，4C/20T + RTX 4060 Laptop）**

| 配置 | 提示处理 (pp128) | **生成 (tg64)** | 说明 |
|---|---|---|---|
| **全 GPU 卸载 `-ngl 99`** | **1049.7 t/s** | **32.20 tok/s** | ✅ 5.44 GiB 权重装得下 7.2 GB 可用显存 |
| 部分卸载 `-ngl 16` | 149.0 t/s | 10.18 tok/s | **14B 的降级路线**（机制已验证） |
| 纯 CPU `-ngl 0` | 156.8 t/s | 5.31 tok/s | GPU **快 6.1 倍** |

**OpenAI 兼容端点**（项目 Provider 直接对接）：`夢の城に帰り着いた。` → **`回到了梦想中的城市。`** ✅
（首次请求含模型加载，约 10 s）

**⚠️ 关键发现：必须关闭思考模式**
Qwen3 是思考型模型，默认会把 token 全花在推理上——实测 64 个 token 全用于思考，
`message.content` **返回空字符串**。翻译场景必须显式关闭：
`chat_template_kwargs: {"enable_thinking": false}`。
已固化到 `tp translate --engine local` 与 `tools/local_bench.py`。

**对项目的影响**：整本（约 99k 输出 token）本地翻译约 **1~1.5 小时**（原估 6~20 小时）。
本地模型作为"隐私/离线备选"完全可用。

**未做**：14B 模型的真机实测（需再下 8~9 GB）；当前用 8B + 部分卸载验证了同一机制。

## 4. 实测 #5：中文 PDF 排版选型（✅ 完成 → **选定 Typst**）

| 引擎 | 装/跑结果 | 证据 |
|---|---|---|
| WeasyPrint（BSD） | ❌ **失败** | `OSError: cannot load library 'libgobject-2.0-0'` —— Windows 缺 GTK/Pango 运行库 |
| **Typst**（Apache-2.0） | ✅ **通过** | `pip install typst` 自带编译器（28 MB，**零外部依赖**）；A5 文档 **1.23 s** 出 36 KB PDF |

**核验（不只"能出 PDF"）**：
1. `pypdfium2` 取回页面文字 → **5/5 通过**（中文标题 / 正文 / 日文假名 / 英文数字 / 标点）
2. 页面渲染成图片**人眼复核**：中文字形正常、标题黑体与正文宋体自动区分、首行缩进 2 字符、
   **标点禁则生效**（行首未出现 `，。、」`）、中英混排间距合理、A5 + 页码正确

**字体**：本机已装 `NotoSerifSC-VF.ttf` / `NotoSansSC-VF.ttf`，真实 family 名为
**`Noto Serif SC`** / **`Noto Sans SC`**（不是 "Noto Serif CJK SC"）——**无需下载字体**。

**架构影响**：内容真源由 "XHTML" 上提为 **IR**；EPUB 走 IR→XHTML，PDF 走 IR→Typst。
两个适配器同源，仍满足"双语版与终版内容强一致"。

**产出工具**：`tools/render_bench.py`、`tools/typst_check.py`

## 5. 实测 #6：PDF 抽取对比（✅ 完成 → **选定 pypdfium2 单引擎**）

**素材**：用户提供的文字版日文 PDF（373 页 / 20 MB / 与 EPUB 同书）

**引擎对比（全文 156,881 字符）**

| 引擎 | 字符数 | 耗时 | 相对 | 结论 |
|---|---|---|---|---|
| PyMuPDF（AGPL，仅对照） | 156,881 | 16.9 s | 100% | 基准 |
| **pypdfium2**（Apache/BSD） | **156,881** | **5.2 s**（热缓存后 1.6 s） | **100%** | ✅ **质量等同、快 3 倍、许可宽松** |
| pdfminer.six（MIT） | **4,838** | 6.4 s | **3%** | ❌ **不可用** |

**pdfminer 失败原因（重要）**：该 PDF 使用 **Type3 字体**（日文电子书常见转换产物），
pdfminer 无法映射其编码，只抽出 3% 的文字，并刷屏 `Could not get FontBBox`。
→ **Type3 字体书必须用 PDFium / MuPDF 系引擎**（pdfminer 直接淘汰出主链路）。

**正文质量**：pypdfium2 抽取的第 19 页（プロローグ）连贯完整，且**注音内联保留**
（`筆舌に尽くし難がたい`、`温ぬくもり`）——与 EPUB 的行为一致，可直接进入清洗阶段。

**结构来源（PDF 路径）**：pypdfium2 可直读 **12 条书签**，与 EPUB 的 NAV 一一对应：
```
L0 表紙 → p.2 ｜ CONTENTS → p.18 ｜ プロローグ『夢の終わり』 → p.19
L0 第一章『氷上決戦』 → p.25 ｜ 第二章『ヤエ・テンゼン』 → p.49 ｜ 第三章『アルデバラン』 → p.84 …
```
→ **PDF 与 EPUB 共用同一套「目录→章节」结构模型**，IR 设计进一步被验证。

**竖排**：⚠️ **本节结论已勘误（M2 阶段修正），见下方。**

> ### 🔴 勘误（2026-09-20）
>
> **原文写"本书横排（竖排行仅 1%）"——结论是错的。这是一本竖排（縦書き）PDF。**
>
> **错在哪**：我用 PyMuPDF 的**行方向向量**（`dir`）判断横竖排。在竖排文档里，PyMuPDF 会把
> "同一水平高度的各列切片"归并成一条"行"，于是 `dir` 恒为 `(1,0)`，被误判成横排。
> 把页面**渲染成图片**后一眼就能看出：文字自上而下成列、列序从右到左、带振假名。
>
> **更正后的事实（反而更好）**：
> 1. **pypdfium2 对竖排的阅读顺序本身就是正确的**——用同一本书的 EPUB 孪生文本逐段比对，
>    **200/200 完全命中**（含内联振假名）。所以**不需要任何列序重排**。
> 2. 竖排的正确检测法：看**字符推进方向**（相邻字符 \|dy\| ≫ \|dx\|），
>    已实现为 `transbook.ingest.pdf.detect_vertical`（含单元测试）。
> 3. PDF 里的 `\r\n` 是**列边界**不是段落边界；段落靠**列首字下げ**识别
>    （dy≈0 续行 / dy≈1 字新段落 / dy≫1 字振假名）。M2 已实现并用真实数据验证：
>    **段落数 3317 vs EPUB 3388（差 2.1%）、段落文本精确命中 92.3%、前缀命中 97.1%**。

**产出工具**：`tools/pdf_probe.py`

## 6. 实测 #7：DeepSeek API 真实成本（✅ 完成）

**密钥接入**：写入项目 `.env`（已 gitignore）；`tp doctor` 确认读取成功。

**小样验证**（6 段）：480 入 / 181 出 token → ¥0.0024；译文质量良好
（`本電子書籍は縦書きでレイアウトされています。` → `本电子书采用竖排布局。`）

**整本实测（Re:Zero 第 43 卷，3401 段全量）**：

| 指标 | 实测值 |
|---|---|
| 批次 / 段落 | **114 批 / 3395 段**（+6 段前次已译 = 3401） |
| 失败 | **0** |
| Token | 输入 **228,837** ／ 输出 **169,132** |
| **真实费用** | **¥0.9054**（状态统计含前次小样：**¥0.9078**） |
| 耗时 | **约 16 分钟**（00:39:52 → 00:56:12） |
| 定价档位 | 空闲时段（周末），高峰会翻倍 → 约 ¥1.81 |

**与事前估算的对比**：dry-run 预估 ¥0.5361（空闲）→ 实际 **¥0.9054**，为预估的 **1.69 倍**。
差异来源：每批的系统提示词开销（114 批 × 约 350 token ≈ 4 万 token）与真实文本 token 数
高于"字符数/1.5"的粗估。→ **计划书 §9 已按实测值更新。**

**预算对比**：你的上限 ¥20~30 → 实际 **¥0.91**，**仅为预算的 3~4%**（约 25 倍余量）。

**⚠️ 关键发现：思考模式默认开启**
官方文档确认 `thinking` 默认 enabled 且 effort=high；不关闭会让思维链白花 token。
已固化：`tp translate --engine deepseek` 自动传 `extra_body={"thinking":{"type":"disabled"}}`。

**质量验证**（`tp qa` 对 3401 段真实译文）：
- 初次报告 115 个 `source_leak` → 人工核查发现**全是误报**（`第七章 『Reweave』`、`「────」` 这类本就无需翻译）
- 修正规则（**只有含假名的原文才可能漏译**）后：**错误 1 ｜ 警告 1**（1 个单假名拟声 `「べ」` 值得人工看一眼）

---

## 7. 阶段结论

- ✅ **M0 环境与骨架完成**：`tp --version`/`doctor` 可跑、venv 与缓存在 Z 盘、C 盘零新增
- ✅ **七项实测完成 6 项**：
  | # | 实测 | 结论 |
  |---|---|---|
  | 1 | uv/缓存落盘 | ✅ uv 0.12.17，缓存与 venv 全在 Z 盘，C 盘无 uv 缓存 |
  | 2 | NAV 结构识别原型 | ✅ 带锚点条目 **16/16 = 100%** 命中（`<h*>` 计数为 0 也不影响） |
  | 3/4 | llama.cpp + Qwen3-8B 吞吐 | ✅ **32.2 tok/s**（GPU 全卸载）；`-ngl 16` 10.18 tok/s；CPU 5.31 tok/s |
  | 5 | 中文 PDF 排版选型 | ✅ **Typst**（WeasyPrint 因缺 GTK 失败）；禁则/缩进/页码已肉眼核验 |
  | 6 | pypdfium2 / pdfminer 抽取对比 | ✅ **pypdfium2 单引擎**（等同 PyMuPDF、快 3 倍）；pdfminer 因 Type3 字体淘汰 |
  | 7 | DeepSeek 真实成本 | ✅ **整本 ¥0.9054 / 16 分钟 / 0 失败**（预算的 3~4%） |
- ✅ **M1 全链路已打通并产出真实成品**：
  - EPUB：纯中文 13.83 MB + 双语 14.01 MB（13 章，含 22 图）
  - PDF：纯中文 **250 页 / 8.15 MB**（A5、自动目录、思源宋体、禁则正确）
  - 审核回流：`export-review` → 修改 → `apply-review` → 重渲染，两个格式均正确反映
  - 测试：**80 个用例全绿**

**M0 与 M1 目标已全部达成。** 下一步可进入 M2（抽取质量）或 M5（服务化）。
