# transbook

把**英/日文电子书**翻成中文的流水线：输入 PDF / EPUB，输出**双语对照版**与**纯中文版**的 EPUB / PDF。

[![ci](https://github.com/wethepeal/transbook/actions/workflows/ci.yml/badge.svg)](https://github.com/wethepeal/transbook/actions/workflows/ci.yml)

---

## 它解决什么问题

用通用翻译工具翻一整本书，会撞上几件具体的事：

- **长篇的一致性**——人名、称谓、伏笔跨章漂移。术语表只锁得住固定词，锁不住"上一章谁和谁结了盟"。
- **日文竖排 PDF**——文字层是按"列"排的，直接抽出来是乱的；还有内联振假名（`手て強ごわい`）污染正文。
- **页眉页脚混进正文**——每一页的书眉和页码都被当成段落翻进去。
- **翻译按批做，看不到前文**——成本与上下文长度是一对矛盾。
- **翻完不改**——机器译文总有个别地方要人看一眼。

transbook 把这条链路拆成五个阶段，每段都有独立的中间产物，可以中断、续跑、回滚。

## 特性

| | |
|---|---|
| **双输入** | EPUB 与 PDF（含**日文竖排**、英文横排）。PDF 用 pypdfium2，按字符推进方向判竖排 |
| **抽取保真** | 剥离内联振假名、压缩 CJK 间空格、过滤书眉/页码（两条独立判据）、保留脚注/表格/图片/目录 |
| **翻译记忆** | 重复段落不重复付费；`text_hash` 作键，重跑一本书时自动复用 |
| **术语表** | `tp terms` 从原文抽候选 → 人工填译法 → 注入提示词 → `tp qa` 回查 |
| **滚动摘要** | 按章生成短摘要，翻译时注入"最近 K 章前情"，解决跨章一致性 |
| **译文质检** | 未译、繁体、假名残留、术语不一致——逐条给出可判定的问题 |
| **人在环中** | 导出 TSV / 在网页里逐段改 → 回灌。**机器译文永不被覆盖**，定稿单独存一列 |
| **两种成品** | 双语对照（审核用）与纯中文（终版），每种都出 EPUB + PDF |
| **校验** | 内置结构检查 + epubcheck（装了就跑），成品 0 错误 0 警告 |
| **成本可控** | 跑之前先给估价，可设成本上限；实测一本 3400 段的书约 **¥0.8**、12 分钟 |

## 五分钟跑通

**环境**：Windows 10/11 + Python 3.12 + [uv](https://docs.astral.sh/uv/)。
Node ≥ 20 只有**从源码构建 Web 界面**时才需要。

```powershell
git clone https://github.com/wethepeal/transbook.git
cd transbook
uv sync                 # 装依赖（虚拟环境建在项目内 .venv）
tp setup                # 问一次 DeepSeek API Key，写进 .env
tp doctor               # 环境自检——先看这一屏
```

> `tp` 即 `.venv\Scripts\tp.exe`。嫌长可 `$env:Path="$PWD\.venv\Scripts;$env:Path"`。

**不用命令行的话**：下载 [Release](https://github.com/wethepeal/transbook/releases) 里的
`transbook-*-win64.zip`，解压双击 `start.cmd`——它会自动装 uv、装包、问密钥、开浏览器。
（发布包把前端嵌在 wheel 里，用户不需要 Node。）

**跑第一本书**（以 EPUB 为例）：

```powershell
$W = "data\work\mybook"
tp extract "D:\books\某本书.epub" -o $W   # ① 抽取 → 先看 preview.md！
tp import  $W                             # ② 入库
tp translate $W --engine fake             # ③ 先用 Fake 跑通，零成本
tp render  $W -m bilingual --to both      # ⑤ 出双语 EPUB+PDF
```

确认链路没问题后换成真引擎：

```powershell
tp translate $W --engine deepseek --model deepseek-flash --max-cost 3
```

## Web 界面

```powershell
cd web; npm install; npm run build; cd ..   # 源码跑要构建一次
tp serve --root data\work
```

浏览器打开**启动时打印的地址**（默认 `http://127.0.0.1:8321/`，端口被系统占用时会自动让开）。
在首页上传书 → 看进度 → 逐段校对 → 下载成品。

界面里还有一个**快速上手**页（`#/help`），把上面这条流程按步骤讲了一遍。

> 端口可能不是 8321：Windows 会把一些 TCP 区段保留给 Hyper-V / WSL / Docker，
> 落在里面的端口绑不上。`tp serve` 会先探测再启动，可以 `--port 9000` 固定住。

## 命令行

`tp --help` 可以列出全部 **18 条命令**。常用的：

```powershell
tp extract <书> -o data/work/X    # ① 抽取 → book.ir.json + preview.md（人工闸门）
tp import  data/work/X            # ② 入库（TM 复用旧译文）
tp summarize data/work/X          # ⑩ 按章生成短摘要
tp translate data/work/X --rolling-summary --max-cost 3
tp qa      data/work/X            # ⑧ 译文质检
tp render  data/work/X -m zh --to both    # ⑤ 纯中文终版
tp validate data/work/X           # ⑨ EPUB 校验（内置 + epubcheck）
tp serve   --root data/work       # ⑫ Web 界面
```

完整手册（每条命令的参数、验收数据、故障排查、分发与打包）见
**[docs/DELIVERY.md](docs/DELIVERY.md)**。

## 流水线

```
  PDF / EPUB
      │
      ▼
  ① 抽取  ──►  book.ir.json  ← 阶段之间唯一的契约（stable seg_id + text_hash）
      │            │
      │            └─► preview.md（人工检查闸门）
      ▼
  ② 入库  ──►  translations.db（段落表 + 翻译记忆 + 摘要）
      │
      ▼
  ③ 翻译  ──►  DeepSeek API ／ 本地 OpenAI 兼容端点 ／ Fake
      │            （术语表 + 前情提要 + 未译重试护栏）
      ▼
  ④ 质检/校对 ──►  QA 报告 ──► 导出 TSV ──► 人工改 ──► 回灌
      │
      ▼
  ⑤ 渲染  ──►  EPUB（手写 EPUB3）与 PDF（Typst）
      │
      ▼
  ⑨ 校验  ──►  内置结构检查 + epubcheck
```

关键约定：`translation`（机翻）**永不被覆盖**，人工定稿单独存在 `final_translation`，
所以「撤销定稿」随时能回到机器译文。

## 目录结构

```
src/transbook/      全部源码
  ingest/           ① 抽取（epub.py / pdf.py）
  ir/               DocumentIR —— 阶段之间唯一的契约
  store/            ② 段落表 / 翻译记忆 / 摘要
  translate/        ③ 引擎、提示词、编排、滚动摘要
  quality/          ⑧ 术语抽取、译文 QA、引擎对比
  render/           ⑤ XHTML → EPUB / Typst → PDF
  service/          FastAPI 服务 + 子进程作业 + SSE
  cli.py            18 条命令
web/                React + TS + Vite 前端
tests/              335 个测试
tools/              发布与验收脚本（build_release / verify_install / check_secrets …）
data/work/<书>/      每本书一个工作目录（不进版本库）
```

## 状态与验证

- **335 个测试全绿**，`ruff` 零告警，CI（Windows）通过
- 端到端实测：44 卷 EPUB（3435 段）与 43 卷竖排 PDF（3328 段）各跑通一次完整流程
- 抽取保真度（PDF vs EPUB 孪生版）：整书字符相似度 **0.9974**、去空白精确命中 **86.6%**
- 成本：DeepSeek flash ≈ **¥5.83 / 百万源字符**，单本约 ¥0.7~0.9

细节数字与复现方式见 [docs/DELIVERY.md](docs/DELIVERY.md) §6。

## 已知限制

- **PDF 表格不还原结构**（EPUB 的表格已支持）；PDF 脚注不做识别
- 只验证了**日→中 / 英→中**；中→外与其他语种未验证
- 全部实测在 Windows 上完成，Linux / macOS 未验证
- 本地小模型（实测 Qwen3-8B）**不适合作主译**：繁体、片假名残留、未译都明显多于 DeepSeek，且慢 7.7 倍

## 许可

[MIT](LICENSE)。翻译成品只用于个人阅读学习，请勿分发。
