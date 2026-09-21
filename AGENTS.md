# Translation Engineering — 项目记忆入口

> 本文件由 `dsh-agent-instructions` 自动注入（项目级，首个请求加载）。
> **详细内容在 `PROJECT-MEMORY.md`**——按需读取，不要把细节复制到这里（本文件每次请求都占上下文）。
> 机器级环境须知见用户全局 `$DSH_HOME/AGENTS.md`（同样自动注入）。

## 项目定位

- 工作区：`Z:\AgentProjectHub\Translation_Engineering`
- 产品：**transbook** —— 电子书翻译流水线。英/日文 EPUB、PDF → 中文，输出双语版与纯中文版的 EPUB / PDF
- 仓库：<https://github.com/wethepeal/transbook>（当前私有，用户计划最终转为**公开**）
- 状态：**M0–M7 全部完成**。335 个测试全绿、ruff 零告警、CI 通过。
  交付说明在 `docs/DELIVERY.md`，决策记录在 `PROJECT-MEMORY.md` §5（D-001…D-066）

## 开工前必读（按顺序）

1. `PROJECT-MEMORY.md` — 项目详细记忆：概览、架构、目录约定、决策记录、任务板、术语表、遗留 TODO
2. `docs/DELIVERY.md` — 交付说明：操作手册、验收数据、故障排查、分发与打包
3. `dsh-plugins-setup.md` — 本机 harness 的插件与磁盘迁移记录

## 怎么跑

```powershell
uv sync                     # 装依赖（venv 在项目内 .venv）
tp doctor                   # 环境自检，先看这一屏
tp serve --root data\work   # Web 界面（端口被系统保留时会自动让开，见下）
uv run pytest -q            # 335 项测试
uv run ruff check .         # 必须为零告警（CI 会拦）
```

## 协作约定

- 与用户用**中文**交流；技术标识符、代码、命令保留英文原样。
- **先验证再断言**：不凭记忆判断工具/接口/文件是否存在；给出可核对的证据。
- 大改动（架构、依赖、破坏性变更）**先给方案和取舍**，确认后再动手。
- 安装、下载、缓存一律放 **Z 盘**（C 盘紧张）。
- 需要工作区外权限时**直接申请** `danger-full-access`，不要绕路或做降级实现。
- 长任务用后台任务 + 任务清单跟踪；不要长时间空转等待。

## 目录结构

```
src/transbook/     ingest(抽取) ir(契约) store(TM) translate quality review render(渲染) service(API/作业)
web/               React + TS + Vite 前端（dist 不入库，发布时嵌进 wheel）
tests/             335 个测试
tools/             build_release.py / verify_install.ps1 / check_secrets.py / acceptance_run.py + 探针
packaging/         start.cmd（纯 ASCII 启动器）+ README.txt
data/work/<书>/    每本书一个工作目录（全部不入库）
```

## 本项目的坑（每次都要记得）

- **`data/` 与 `web/dist` 都不进 git**，但发布时 `web/dist` 会被 `hatch_build.py` 嵌进 wheel。
- **`.env` 在 `.gitignore` 里**，绝不要把真实密钥写进任何被跟踪的文件——已经踩过一次，
  只能重写历史清除。提交前跑 `python tools/check_secrets.py`（CI 也会跑）。
- **不要为了消除 lint 告警给代码加"更强的断言"**（比如 `zip(strict=True)`）而不先验证
  那个不变量——也踩过一次，CI 直接红了。
- **Windows 上输出被重定向时按 ANSI 代码页编码**，中文会 `UnicodeEncodeError`。
  新脚本要在顶部调 `transbook.console.tolerate_unencodable_output()`。
- **默认端口 8321 可能落在 Windows 保留区段里**（Hyper-V/WSL/Docker 占 8xxx 段），
  `tp serve` 会自动往后找；查保留段：`netsh int ipv4 show excludedportrange protocol=tcp`。

## 记忆维护协议

- **随时更新 `PROJECT-MEMORY.md`**：新决策、新约束、架构变化、任务状态、踩过的坑。
- 本文件只放"每次都要知道"的内容；任何细节都进 `PROJECT-MEMORY.md`。
- 决策按日期**追加**到 `PROJECT-MEMORY.md` 第 5 节，不删除旧记录（作废的标注"已废弃"）。
- 每次更新在 `PROJECT-MEMORY.md` 第 10 节留一行变更日志。
