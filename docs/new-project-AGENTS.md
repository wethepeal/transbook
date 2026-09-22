# 本机环境与版本控制须知

> **用法**：把本文件复制到新工作区根目录，**重命名为 `AGENTS.md`**。它会在该工作区
> 的首个请求自动注入，新项目一开工就知道这台机器的约束与交付流程。
>
> **与全局文件的关系**：`$DSH_HOME/AGENTS.md`（`Z:\AgentPlugins\dsh-home\AGENTS.md`）
> 是**每个会话、每个项目都自动注入**的机器级须知。下面「一、本机环境」与它**有意重叠**——
> 重叠部分**以那份为准**，这里摘录只为让本文件自成一体、可独立复制。
> 真正新增、那边没有的是「二、版本控制与交付」。
>
> **维护**：机器环境变了改全局那份；交付流程变了改这份。两边都改的时候优先改全局。
>
> 记录时间：2026-09-21。带「易变」标记的条目会随环境变化，别当常数。

---

## 一、本机环境

### 1.1 磁盘：C 盘紧张，下载/缓存/依赖一律放 Z 盘

C 盘长期只有几 GB 可用。**任何安装、下载、模型、缓存都不要落到 C 盘**，
用环境变量或工具的配置项指到 Z 盘：

| 用途 | 位置 | 由谁指定 |
|---|---|---|
| 本地 AI 部署（引擎 / 模型 / 本地推理服务） | `Z:\AgentHub` | `HF_HOME`、`OLLAMA_MODELS` 等 |
| 工具二进制（uv / Typst / Tesseract / ffmpeg…） | `Z:\Tools` | 加入 PATH |
| 通用缓存（pip / NuGet / uv / HF / torch / CLIP） | `Z:\_cache` | 各工具的 `*_CACHE_DIR` |
| DSH 数据根（会话 / 凭据 / 插件） | `Z:\AgentPlugins\dsh-home` | `DSH_HOME` |
| npm / npx 缓存 | `Z:\AgentPlugins\npm-cache` | `npm_config_cache` |
| Playwright 产物 | `Z:\AgentPlugins\playwright-output` | MCP 行 `--output-dir` |

**做法**：项目开工第一条命令就把缓存变量定下来（写进 `.env` 或启动脚本），
别等 C 盘满了再回头迁。迁移时用 Junction 保留原路径，但注意——
**`Get-ChildItem -Recurse` 和 `robocopy /XJ` 不穿越 Junction**，统计文件数会严重少算；
判断完整性要去掉 `/XJ`（跟随链接）。

### 1.2 沙箱与权限

- 默认策略 **`workspace-write`**：只有会话工作区可写，工作区外一律拒绝
  （含 `Z:\AgentPlugins`、注册表、系统环境变量）。
- 确实需要工作区外操作时**直接带 `sandbox_permissions: danger-full-access` 申请**，
  不要绕路、不要因此降级实现。
- **已知会被沙箱挡掉、必须放宽才能做的事**（都实测过）：
  - **前端构建（Vite / npm build）**：Vite 会 `exec('net use')` 探测网络盘 → 子进程管道
    stdio 被禁 → `spawn EPERM`。
  - **`curl.exe` 的 TLS**：报 `schannel: SEC_E_NO_CREDENTIALS`。**这不是网络故障**，
    测连通性改用 Node 的 `fetch` 或 Python `urllib`。
  - **需要 git 起 shell 的命令**（`git ls-remote`、部分凭据交互）：报
    `couldn't create signal pipe, Win32 error 5`。用 GitHub API 或直接 `git push` 代替。

### 1.3 这台机器特有的坑（都是实测踩出来的）

**① 端口可能落在 Windows 保留区段里**（易变）

Windows 把一些 TCP 区段保留给 Hyper-V / WSL / Docker。落在里面的端口连 `bind` 都不允许，
报错是「以一种访问权限不允许的方式做了一个访问套接字的尝试」——**看着像权限问题，
其实是端口被系统占着了**。查：

```powershell
netsh int ipv4 show excludedportrange protocol=tcp
```

实测本机保留过 `8163-8262` 与 `8263-8362` 两段（**会变**，重启或开虚拟机会重新分配）。
所以：**写任何本地服务都要"先探测再启动、并打印实际地址"**，不要在文档里写死一个端口号。

**② 输出被重定向时按 ANSI 代码页编码**

Windows 上 stdout 被管道 / 文件 / CI 捕获时，Python 按 ANSI 代码页编码：
简体中文机器是 **GBK**、英文 runner 是 **cp1252**。
GBK 装不下 `✓`(U+2713)、`✗`、`⑪`(U+246A)，cp1252 连中文都装不下 →
`UnicodeEncodeError`，**整条命令以非零码退出**。直接输出到真实控制台没有这个问题，
所以**只在管道 / 重定向 / CI 里踩得到**。

对策：脚本顶部统一调一次

```python
for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
```

**这个坑一旦抽成共享函数，就别再在每个新脚本里重写一遍**——我在一个项目里连踩三次。

**③ 编码与文本文件的三种约定**

| 文件类型 | 存法 | 原因 |
|---|---|---|
| 含中文的 `.ps1` | **UTF-8 with BOM** | PowerShell 5.1 读 BOM-less 会按 GBK 解，中文全乱 |
| `.cmd` / `.bat` | **纯 ASCII** | cmd.exe 在非中文代码页下会乱码 |
| 其他源码 / 文档 | UTF-8 无 BOM | 通用做法 |

**④ `pnpm` 在本机是坏的**：`pnpm.exe` 被错误硬链接成了 POSIX 二进制，
报 `not a valid application`。**前端一律用 npm**。

**⑤ 长脚本写成文件再跑，别用内联 heredoc**

内联 Python + 中文 + 引号在这个 harness 里会反复出问题（引号被 PowerShell 吃掉、
f-string 里的中文触发语法错、`Out-String` 改变编码导致正则匹配失败）。
**超过十行的脚本一律写进文件再执行**。

**⑥ 中文输出在终端里的乱码不代表内容错了**

`pwsh` 捕获子进程输出时经常显示成 `������`。要判断真实内容，**用 Python 读文件 / 调 API
并打印 `repr()` 或字符码点**，不要靠肉眼看终端。

**⑦ 用"字符类"做检查时，注意同形字和转义**

- 中日文有大量同形字（`卷` U+5377 / `巻` U+5DFB），写测试断言时肉眼分不出，
  构造数据尽量用 ASCII。
- 自定义的 Markdown 结构检查器会把表格里的转义 `\|` 当成多一列 —— 是检查器的错，不是文档的错。

---

## 二、版本控制与交付（GitHub）

### 2.1 令牌

- 细粒度 PAT（`github_pat_` 开头），**不放仓库、不写进 URL、不 echo**。
  本机放在工作区外的固定文件里，用完即删临时副本。
- **实测能力**（逐个接口验证过，不是猜的——GitHub 不通过 API 暴露细粒度 PAT 的勾选项，
  所以只能按"哪些操作真的能跑"来判断）：

  | 能做 | 怎么确认的 |
  |---|---|
  | 推送提交与标签 | 推 `main` 与 `v0.1.0` 都成功 |
  | 建 / 改 Release（含附件） | Release 发布成功，说明文字也用 API 改过 |
  | 读 Actions 运行与 job 日志 | 拉取 CI 失败日志定位过两次问题 |
  | 读 / 写 Issue、PR、提交、分支 | 接口均返回 200 |

  | 做不到 | 现象 |
  |---|---|
  | 读仓库 Webhook | HTTP 403 |
  | 读 Actions secrets | HTTP 403 |
  | 读安全告警（secret / Dependabot / code scanning） | HTTP 403 |

- **建议令牌勾这些**（按"通常会有部署需求"来配）：

  | 权限 | 为什么 |
  |---|---|
  | `Contents: Read and write` | 推送、打标签、建 Release |
  | `Workflows: Read and write` | **推送 `.github/workflows/*` 必须**，否则被拒 |
  | `Actions: Read` | 读 CI 运行结果与日志（排查失败要用） |
  | `Metadata: Read` | 必选 |
  | `Secrets: Read` | 想用 API 核对 / 管理 Actions secrets 就得有 |
  | `Security: Read` | 想核对密钥扫描、Dependabot 告警就得有 |
  | `Webhooks: Read` | 要做部署回调才需要 |

- **注意区分**：CI **正常读取** secrets 不需要令牌有 `Secrets` 权限——
  工作流里的 `${{ secrets.X }}` 走的是仓库自己的机制。缺的只是"通过 API 管理 secrets"。
- 令牌**只授权勾选的仓库**。要扩大范围去 GitHub 上给令牌加仓库，不用换令牌。

### 2.2 网络：git **不读** Windows 注册表的代理

- 这台机器靠系统代理上网（实测 `127.0.0.1:7897`，**易变**）。
- **Python 会自动读注册表里的代理设置，git / libcurl 不会**。症状是
  "Python 能连 GitHub，git 却连不上"。
- 必须给仓库配一次：

  ```powershell
  git config http.https://github.com.proxy http://127.0.0.1:7897
  ```

- **代理会停**。停了之后先别下结论说推不了——**实测 GitHub 在本机可以直连**，
  用命令行临时覆盖即可：

  ```powershell
  git -c http.https://github.com.proxy= push origin main
  ```

  所以「代理挂了」≠「推不了」，先测直连。

### 2.3 推送

- **凭据文件必须和用它推送写在同一条命令里**：本 harness 的 `%TEMP%` **每次调用都不同**，
  上一轮写的文件下一轮就找不到了。
- 写法（`git credential-store` 的**文件直接写好**，不用 `git credential approve`——
  那条会静默失败）：

  ```powershell
  $credFile = Join-Path $env:TEMP 'gh_creds'
  [IO.File]::WriteAllText($credFile, "https://USER:TOKEN@github.com`n",
                          (New-Object Text.UTF8Encoding($false)))
  git -c credential.helper="store --file=$credFile" push origin main
  Remove-Item $credFile -Force
  ```

- 会看到一条 `fatal: unable to get credential storage lock in 1000 ms: Permission denied`，
  **但它不影响推送**（实测 exit 0、远端确实更新了）。别被这行吓到去改方案。
- **推送失败先重试**（网络抖动很常见），再排查。写个 3 次的退避循环，别一次失败就放弃。
- **推完立刻核对本地与远端是否一致**：
  `git rev-parse --short main` 对比 `git rev-parse --short origin/main`。

### 2.4 密钥：最容易犯的、代价最高的错

**我在一个项目里把真实的 API Key 写进了测试文件并推送了。** 来源是写"接口不能回传
密钥明文"那条测试时，从一次**失败的断言输出**里把真值复制成了测试字面量。教训按代价排序：

1. **测试数据一律现编假值**，绝不从任何运行输出里复制真实凭据。
   （命名上也要一眼可辨：`sk-fake000...`）
2. **`.gitignore` 第一条就写 `.env`**，项目开工时先做这件事，不是想起来才做。
3. **只在新提交里删掉是不够的**——旧提交的 blob 里还在。要清全历史：
   用 **`git filter-repo`**（本机 `git filter-branch` 不可用，`sh` 不在 PATH）：
   ```powershell
   git filter-repo --replace-text replacements.txt --force
   ```
   动手前先备份整个 `.git`。
4. **验证方式是重新克隆远端再扫**，不是只看本地。只有这样才能证明远端真的没有了。
5. **密钥一旦推送过就视为泄漏，必须作废重发**——重写历史**不能替代**这一步。

**把密钥扫描做成 CI 的固定一步**（工作树每次都扫，另留一个 `--history` 模式供转公开前全量扫）。
这类脚本值得做成**可复用的**，不要每个项目重写一遍。

### 2.5 仓库转公开：一道不可逆的风险分界线

**转公开之后，提交历史、Issue、Actions 日志对匿名用户全部可读。**
动作一旦做了就收不回来，所以按顺序来：

**转公开之前必须做**：

1. **扫全历史**，不是只看工作树：
   ```powershell
   python tools/check_secrets.py --history
   ```
2. **确认 `.gitignore` 覆盖 `.env`、数据目录、构建产物**。
3. **曾经泄漏过的凭据一律作废重发** —— 重写历史**不能替代**这一步（见 2.4）。
4. **确认 Actions 日志里不会打印出密钥**：CI 里不要 `echo` 密钥、
   不要 `set -x` 打印环境变量、不要在失败输出里 dump 配置。

**转公开之后应该打开**（公开仓库免费）：

- **Secret scanning** 与 **Push protection**（Settings → Code security）——
  前者事后告警，后者**在推送那一刻就拦下来**，对防 2.4 那种错最有效。
- **Dependabot alerts**（依赖漏洞告警）。

**验证公开状态不要凭印象**——不带任何凭据访问一次：

```python
urllib.request.urlopen("https://api.github.com/repos/OWNER/REPO")   # 能拿到就是公开的
```

实测本项目：`visibility: public`、匿名可读 README 与 Release 附件。
另外注意：**令牌没勾 `Security` 权限时，读安全告警接口会返回 403**，
那时你无法从 API 确认这些开关是否打开，只能去网页 Settings 里看。

### 2.6 CI 与发布

- **CI 从第一天就要有**，别等出问题才建。最小三步：依赖同步 → lint → 测试。
  再加一步**密钥扫描**（见 2.4）。
- **CI 失败要去拿真实日志**，不能只看告警邮件的摘要。用 GitHub API 拉 job 日志：
  注意 `/logs` 会 302 到另一个域名，**跟随时要把 `Authorization` 头去掉**，否则 401。
- **Action 版本按各 Action 自己 `action.yml` 里的 `runs.using` 选**，
  取首个 node24 的版本——**别按版本号猜**，否则会一直收到 Node 弃用告警。
- **写进 README 的外链，当轮就要点开验证**。我在 README 里写了 Release 下载链接，
  却没验证那个页面存在，用户一眼就发现"No releases published"。
- **发布机制**：推 `v*` 标签触发工作流即可，**不需要额外权限**——
  工作流里写 `permissions: contents: write` 并用仓库自带的 `${{ github.token }}`。

  ```yaml
  on:
    push:
      tags: ["v*"]
  permissions:
    contents: write
  ```

- **部署类需求要先确认触发条件，而不是先怀疑授权**。上面那次我一开始怀疑"是不是令牌
  权限不够"，真实原因是**从来没推过 `v*` 标签**（本地只有一个不匹配的旧 tag）。
- **首个标签的 `--generate-notes` 只会写一行 "Full Changelog"**（前面没有可比版本），
  对外可见的说明要手工补。

### 2.7 提交前的三条自检

任何一次推送前都跑，三条都绿才算完：

```powershell
uv run pytest -q          # 或项目的测试命令
uv run ruff check .       # lint 必须零告警
python tools/check_secrets.py   # 密钥扫描
```

再补两条纪律：

- **不要为了消除 lint 告警给代码加"更强的断言"而不先验证那个不变量。**
  我曾把 `zip(a, b)` 改成 `zip(a, b, strict=True)`，理由是"入口已保证等长"——
  实际两个参数来自不同 API，长度本来就可能不等，CI 直接红了。
  **为过 lint 而"顺手加强"的约束，必须先真跑一遍验证。**
- **改完 bug 要补回归测试**，尤其是用户报出来的。测试数量是进度的可靠指标：
  这次项目从 0 走到 335 项全绿，中途每一次"以为没问题"都被测试抓出来过。

---

## 三、协作约定

- 与用户用**中文**交流；技术标识符、代码、命令保留英文原样。
- **先验证再断言**：不凭记忆判断工具 / 接口 / 文件是否存在，给可核对的证据。
  这条在本机尤其重要——工具版本、端口、代理状态都会变。
- **大改动（架构、依赖、破坏性变更）先给方案和取舍**，确认后再动手。
  破坏性操作（删除、重写历史、强推）**必须先问**，并把"删什么、留什么"写清楚。
- 安装、下载、缓存一律放 **Z 盘**。
- 需要工作区外权限时**直接申请** `danger-full-access`，不要绕路。
- 长任务用**后台任务 + 任务清单**跟踪，不要空转等待。
- **不要声称跑过没跑的门禁**。如果某个检查脚本不存在或跳过了，如实说"未验证"，
  不要用模糊措辞让它听起来通过了。

---

## 四、给新项目的起手清单

按这个顺序做，能省掉后面大量返工：

1. **`.gitignore` 先写**，第一条 `.env`。顺带 `data/`、`dist/`、构建产物、缓存。
2. **缓存目录指到 Z 盘**（1.1 的表），开工第一条命令就定下来。
3. **从第一天就有 CI**：依赖同步 → lint → 测试 → 密钥扫描。哪怕测试只有一条。
4. **`README.md` 和项目记忆文件同时建**。README 是对外的门面，记忆文件是对内的决策记录。
5. **决策随手记**（日期 / 决策 / 触发原因 / 取舍），尤其是**踩过的坑**——
   同一个坑踩第二次的代价远高于记录它的成本。
6. **破坏性功能先在方案里写清"删什么、留什么、能不能撤销"**，再写代码。
7. **提交前三条自检**（2.7）固化成习惯或脚本。
8. 装到一半失败时，**先看是不是沙箱 / 编码 / 端口这三类问题**——本机九成的怪错误出自这里。

---

## 五、这份文件本身怎么维护

- 它是**活文档**：新踩一个坑就加一条，别攒着。
- 加条目时**写"怎么判断"和"怎么办"**，不要只写结论。
  「端口可能被保留」没用，「用 `netsh` 查、并且让服务自动让开」才有用。
- **易变的条目要标出来**（代理端口、保留区段、工具版本），并写清怎么重新查。
- 机器级的事改全局 `$DSH_HOME/AGENTS.md`；只有交付流程的事才改这份。
