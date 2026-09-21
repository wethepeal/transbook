# DSH 插件安装与磁盘迁移记录

日期：2026-09-16 ｜ 目标：给当前 harness 增加能力，且不占用 C 盘

---

## 1. 结论速览

已装并**实测验证**的插件：

| 插件 | 来源 | 状态 | 验证方式 |
|---|---|---|---|
| `dsh-tool-cordis` | 官方内置（零下载） | 已生效 | 本会话已能调用 `cordis_inspect_*` / `cordis_define` / `cordis_run` |
| 会话全文检索（`session-query-sqlite` 覆盖） | 官方内置 | 配置已生效 | 合成配置校验通过；重启后用侧边栏搜索验证 |
| Playwright MCP | GitHub `microsoft/playwright-mcp` | 已生效 | 真实打开 `https://example.com` 成功，产物落在 Z 盘 |
| Context7 MCP | GitHub `upstash/context7`（托管端点） | 已生效 | `mcp__context7__*` 工具已注册，端点实测 HTTP 200 |
| GitHub MCP | GitHub `github/github-mcp-server`（托管端点） | 令牌已验证（45 个工具），重启后生效 | 直连端点 `initialize` / `tools/list` 均 HTTP 200 |

额外完成：pnpm 12 已装在 Z 盘（`dsh plugin` 需要它），npm 缓存与 DSH 数据根已迁到 Z 盘。

---

## 2. 现在请做这一步：用 Z 盘启动器重启

当前正在运行的 harness 仍在用 C 盘数据根，且它的安装目录已被删除（见第 4 节），
**必须重启一次**才会切到 Z 盘：

```
Z:\AgentPlugins\start-dsh.cmd
```

这个启动器会：
1. 设置 `DSH_HOME` / `npm_config_cache` / `PLAYWRIGHT_BROWSERS_PATH` 指向 Z 盘；
2. 把 C 盘旧的会话日志同步过来（避免这段对话的结尾丢失）；
3. 用 `npx @deepseek-ai/dsh web` 启动。

> 也可以直接用普通终端 `npx @deepseek-ai/dsh web`：用户级环境变量已经设好，
> 但**必须是重启后新开的终端**才继承得到。

重启后确认清单：
- [ ] 会话历史完整（本对话还在）
- [ ] 我这边能看到 `mcp__playwright__*` 和 `mcp__context7__*` 工具
- [ ] 侧边栏搜索能搜到历史会话**正文**内容（全文检索已开）
- [ ] `Z:\AgentPlugins\dsh-home\session-search.db` 在首次搜索后出现

---

## 3. 配置文件在哪

```
Z:\AgentPlugins\dsh-home\profiles\web\cordis.patch.yml
```

这是**用户补丁层**，最后应用，`patchReload: live` → 改完保存即热重载，不必重启。

### patch 语法速查

```yaml
# 新增一个插件实例
- insert:
    - id: my-row
      name: '@deepseek-ai/dsh-某包'
      config:
        key: value

# 覆盖已存在行的配置（注意：整体替换 config，不是合并！）
- id: session-query-sqlite
  config:
    path: !!js dshHomePath('session-search.db')
    openAt: first-search

# 启用/禁用某行
- id: ui-schedule
  disabled: false
```

要点：
- 文件必须是**顶层 YAML 数组**；清空该层要写 `[]`，不能留空文件。
- `!!js` 表达式可用（`process.env.X`、`dshHomePath('...')`）。
- 补丁是**整段替换 config**，覆盖时必须把想保留的字段全部重写。
- 改错不会搞坏运行中的进程：加载失败的编辑会被拒绝，保留上一份可用配置。

---

## 4. 磁盘发生了什么（含一次事故，如实说明）

**成果：**
- npm 缓存 3.16 GB → `Z:\AgentPlugins\npm-cache`
- DSH 数据根 198 MB → `Z:\AgentPlugins\dsh-home`
- pnpm 12 → `Z:\AgentPlugins\npm-global`（已加入用户 PATH）
- 用户级环境变量已设：`DSH_HOME`、`npm_config_cache`、`PLAYWRIGHT_BROWSERS_PATH`

**事故：**清理 C 盘 npm 缓存时，删掉的不只是缓存数据——`profiles\node_modules\*` 是
**链接（ReparsePoint）**，指向 `npm-cache\_npx` 里的真实文件。删缓存连带删掉了这些链接的
目标，于是 **C 盘数据根变成残缺状态**（只剩 14 个文件 / 1.29 MB 的空壳）。

影响与处置：
- 当前 harness 进程仍能跑，是因为它需要的模块早已加载进内存；但**任何懒加载都可能失败**，
  所以请尽快用第 2 节的启动器重启。
- **数据没有丢**：Z 盘副本是在删除之前拷的，并已通过完整复检（见第 10 节）。
- C 盘旧根 `C:\Users\wethe\.dsh` 现在只剩 1.3 MB 空壳，240 个插件链接**全部断链**，
  已确认无用，可以安全删除（我尚未删除，等你确认）。

---

## 5. Z:\AgentPlugins 目录布局

| 目录 | 用途 |
|---|---|
| `dsh-home\` | DSH 数据根：会话、凭据、storages、profiles/插件 |
| `npm-cache\` | npm/npx 下载缓存（含 harness 本体安装 `_npx\1e7f6d9597241db0`） |
| `npm-global\` | pnpm 12（`pnpm.cmd`） |
| `playwright-output\` | 浏览器快照/截图等产物 |
| `playwright-browsers\` | 预留；当前为空 = 复用系统 Chrome，零浏览器下载 |
| `start-dsh.cmd` | 启动器 |
| `_verify\` | 校验用的临时文件，可删 |

---

## 6. 启用 GitHub MCP（还差一步）

官方 `github/github-mcp-server` 的托管端点已连通，令牌也已配置并**现场验证通过**：

1. 令牌存放在**用户级环境变量**里（2026-09-17 设置）：
   ```
   setx GITHUB_PERSONAL_ACCESS_TOKEN "<你的 fine-grained PAT>"
   ```
   令牌**不写进本文件、也不写进补丁文件**，避免密钥进入可分享的文本。
   只读权限足够：Contents、Issues、Pull requests、Metadata。
2. 直连端点验证结果：`initialize` → HTTP 200（serverInfo = `github-mcp-server`），
   `tools/list` → HTTP 200，返回 **45 个工具**（create_pull_request、get_commit、list_issues…）。
   令牌归属账号 `wethepeal`（`GET /user` 200）；`GET /user/repos` 显示当前
   **仅授权 `wethepeal/pixel-ai`**。要让 agent 操作其它仓库，去 GitHub 该令牌的
   Repository access 里把它们加进去即可——细粒度令牌按仓库授权，**加仓库不需要换令牌**。
3. 重启 harness 后 `mcp__github__*` 才会出现在工具列表里——**必须是新进程**才能继承新变量。
   若重启后仍然没有，说明你的启动终端是 `setx` 之前开的：改用 `Z:\AgentPlugins\start-dsh.cmd`
   或新开一个终端。

> 轮换令牌：重新执行一次 `setx` 并重启即可。建议**由你自己在终端里敲**这条命令，
> 这样新令牌就不会进入会话记录。

未配置令牌时不会有副作用：该服务器返回 401，harness 照常启动，只是不注册它的工具。

> 为什么不放 `.env`：`!!js process.env.X` 读的是真实进程环境，而 harness 的
> `dsh-launch-environment` 只把 `.env` 收进内部快照，不保证扁平化进 `process.env`。
> 启动器里的 `set` 是确定的。

---

## 7. 回滚

- **停用某个插件**：在 `cordis.patch.yml` 里删掉对应条目（或给该行加 `disabled: true`）。
- **整体停用补丁层**：把文件内容改成 `[]`。
- **回到 C 盘数据根**：删除用户级环境变量 `DSH_HOME`（或改回 `C:\Users\wethe\.dsh`）。
  注意 C 盘那份已被破坏，实际应先把 `Z:\AgentPlugins\dsh-home` 拷回去。

---

## 8. 可选的后续插件（本次已核实可用，按需再加）

| 插件 | 说明 | 代价 |
|---|---|---|
| `dsh-schedule` + `dsh-time-context`，并把 `ui-schedule` 行 `disabled: false` | 定时任务，含界面页 | 零下载 |
| `chrome-devtools-mcp` | 性能 trace、网络、控制台（用本机 Chrome） | npx 安装 |
| `@modelcontextprotocol/server-sequential-thinking` / `-memory` / `-filesystem` | 官方参考服务器：长链推理 / 记忆 / 文件 | npx 安装 |
| `dsh-hooks-claude-code` / `dsh-hooks-codex` | 接入它们的 hooks.json 命令钩子 | 零下载，需 configPath |
| `dsh-webhook-github` | 接收 GitHub webhook（需同时插 `dsh-webhook` 行） | 零下载，需密钥 |
| `serena` / `mcp-server-fetch` | 语义代码检索 / 抓取 | 需先装 uv（本机没有） |

安装新插件的命令（pnpm 已在 Z 盘）：
```
Z:\AgentPlugins\npm-global\pnpm.cmd --dir Z:\AgentPlugins\dsh-home\profiles\web add <包名>
```
装完检查 `profiles\web\package.json` 的 `dsh.profile.bundles` 是否自动加了该包。

---

## 9. 已知坑

- **Windows 上 MCP 用 npx 启动**：本 harness 的 MCP SDK 走 `cross-spawn`，能识别 `.cmd`；
  但为稳妥，补丁里统一写成 `command: cmd` + `args: ['/c','npx',...]`（实测有效）。
- **沙箱里的 curl 无法做 TLS**（schannel 拿不到凭证，报 `SEC_E_NO_CREDENTIALS`），
  这不是网络故障；用 Node 的 `fetch` 或 `web_fetch` 工具测连通性。
- **拷贝 npm 缓存会导致 `ECOMPROMISED / Lock compromised`**：复制后要删掉
  `npm-cache\_locks` 和 `_cacache\tmp` 再使用。
- **跨盘符时 pnpm 硬链接失效**，会退化为复制；数据根与 store 都在 Z 盘后恢复正常。

---

## 10. 复检记录（重启后实测）

迁移**已生效且完整**：

| 检查项 | 结果 |
|---|---|
| 运行中 harness 的数据根 | `DSH_HOME=Z:\AgentPlugins\dsh-home`  |
| npm 缓存 | `Z:\AgentPlugins\npm-cache`  |
| 插件链接 | 240 / 240 有效，**0 断链**|
| 可达文件数 | 3297 目录 / 25399 文件 / 198.9 MB（与迁移前完全一致） |
| 链接目标 | `Z:\AgentPlugins\npm-cache\_npx\1e7f6d9597241db0\...`（同盘、目标完好） |
| 关键包可读 | tool-cordis / mcp-client / session-query-sqlite / cordis-host-runner  |
| 插件已注册 | `mcp__playwright__*` 26 个 + `mcp__context7__*` 2 个 + `cordis_*` 7 个  |
| C 盘旧根 | 240 / 240 链接断链 → **已删除**（删前最后同步 exit=0，确认无新写入） |
| 空间 | C: 剩 10 GB / 200 GB；Z: 剩 138 GB / 1000 GB |

**为什么"文件数"会看起来变少**：`profiles\node_modules\*` 是**目录联接（Junction）**，指向
npm 缓存里的真实包。`Get-ChildItem -Recurse` 和 robocopy `/XJ` **默认不穿越链接**，所以只会
数到 14 个实体文件；去掉 `/XJ`（跟随链接）才得到完整的 25399 个文件。判断完整性必须用后者。

**全文检索尚未验证到底**：`session-search.db` 还没生成，因为 `openAt: first-search` 要等
**第一次搜索**才建库——在 Web 侧边栏搜一个词，然后看
`Z:\AgentPlugins\dsh-home\session-search.db` 是否出现即可。

**版本提示**：`@deepseek-ai/dsh` 本体是 0.1.5-rc.1，但依赖 `dsh-mcp-client` 已被解析到
0.1.5-rc.2（npx 按 `^0.1.5-rc.1` 取了较新的补丁版）。若要版本可复现，把启动器里的
`@deepseek-ai/dsh` 写成带版本的形式，如 `@deepseek-ai/dsh@0.1.5-rc.1`。

**C 盘仍有压力**（仅剩 10 GB），实测可回收项：`~\.cache` 2.2 GB、`~\.nuget\packages` 1.5 GB、
`AppData\Local\Temp` 1.0 GB、`pip\cache` 0.75 GB、Edge 缓存 0.2 GB —— 合计约 5.7 GB，
都可以迁到 Z 盘或直接清理。

**令牌复检（同日）**：GitHub PAT 已写入用户级环境变量并直连验证——`initialize` 与
`tools/list` 均 HTTP 200，服务器返回 **45 个工具**。

**第二次重启后实测（全部完成）**：`mcp__github__get_me` 真实调用返回 `login=wethepeal`
（bailend）；`mcp__github__list_issues`（`wethepeal/pixel-ai`）正常返回 —— 仓库级读权限通过。
当前注册的工具总数：`cordis_*` 7 + `mcp__github__*` 45 + `mcp__playwright__*` 26 +
`mcp__context7__*` 2 = **80 个**。

**全文检索的最后一步（待你触发一次）**：`session-search.db` 仍未生成，因为它是
**首次搜索时才建库**。其运行前提已逐项验证：`node:sqlite` 可用、内置 SQLite **3.50.4 支持
FTS5**（实测检索命中）、补丁行合成正确。在 Web 侧边栏搜任意一个词，库文件就会出现。

**本地 GUI 有认证**：用隔离浏览器打开 `http://127.0.0.1:3080` 返回 401（根路径即需鉴权），
所以无法用 Playwright 替你在界面上操作；访问外部站点不受影响。

**密钥暴露面（须知）**：该 PAT 目前存在于 (1) 用户环境变量 `HKCU\Environment`、
(2) **本会话的记录**——因为令牌是在对话里粘贴的，会话日志与
`storages\session_projcache\...json` 里都会留有它。启动器与本文档中均无令牌副本。
若这份会话记录会被分享或备份到别处，请在 GitHub 上轮换该令牌。
