# Translation Engineering — 项目记忆入口

> 本文件由 `dsh-agent-instructions` 自动注入（项目级，首个请求加载）。
> **详细内容在 `PROJECT-MEMORY.md`**——按需读取，不要把细节复制到这里（本文件每次请求都占上下文）。
> 机器级环境须知见用户全局 `$DSH_HOME/AGENTS.md`（同样自动注入）。

## 项目定位

- 工作区：`Z:\AgentProjectHub\Translation_Engineering`
- 方向：**翻译工程（Translation Engineering）**，属于大型项目开发
- 状态：**项目尚未开始编码**，目标/范围/技术栈待与用户确认

## 开工前必读（按顺序）

1. `PROJECT-MEMORY.md` — 项目详细记忆：概览、架构、目录约定、决策记录、任务板、术语表、遗留 TODO
2. `dsh-plugins-setup.md` — 本机 harness 的插件与磁盘迁移记录：装了什么、怎么加插件、怎么回滚

## 协作约定

- 与用户用**中文**交流；技术标识符、代码、命令保留英文原样。
- **先验证再断言**：不凭记忆判断工具/接口/文件是否存在；给出可核对的证据。
- 大改动（架构、依赖、破坏性变更）**先给方案和取舍**，确认后再动手。
- 安装、下载、缓存一律放 **Z 盘**（C 盘紧张，剩余约 10 GB）。
- 需要工作区外权限时**直接申请** `danger-full-access`，不要绕路或做降级实现。
- 长任务用后台任务 + 任务清单跟踪；不要长时间空转等待。

## 目录约定（待项目确定后更新）

```
Translation_Engineering/
├── AGENTS.md              # 本文件：项目记忆入口（自动注入，保持精简）
├── PROJECT-MEMORY.md      # 项目详细记忆（单一事实来源）
├── dsh-plugins-setup.md   # 环境 / 插件 / 磁盘迁移记录
└── (src/  docs/  tests/  data/  ... 待规划)
```

## 当前状态（2026-09-17）

- 环境已就绪：DSH 数据根在 Z 盘；4 个插件组共 **80 个工具**可用（GitHub 45 / Playwright 26 / Cordis 7 / Context7 2）。
- 代码尚未开始；技术栈、架构、目录结构均待确认。
- 遗留事项：会话全文检索待首次搜索验证；C 盘空间约 5.7 GB 可回收；GitHub 令牌当前仅授权 1 个仓库。

## 记忆维护协议

- **随时更新 `PROJECT-MEMORY.md`**：新决策、新约束、架构变化、任务状态、踩过的坑。
- 本文件只放"每次都要知道"的内容；任何细节都进 `PROJECT-MEMORY.md`。
- 决策按日期**追加**到 `PROJECT-MEMORY.md` 第 5 节，不删除旧记录（作废的标注"已废弃"）。
- 每次更新在 `PROJECT-MEMORY.md` 第 10 节留一行变更日志。
