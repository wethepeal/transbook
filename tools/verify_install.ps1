<#
.SYNOPSIS
    验证"发布包开箱即用"：在干净环境里装 wheel，并从项目目录之外启动，确认界面可用。

.NOTES
    **本文件必须保存为 UTF-8 with BOM。**
    Windows PowerShell 5.1 读取无 BOM 的 .ps1 时会按 ANSI 代码页（简中机器上是 GBK）
    解码，中文会被解坏、连语法都过不去。用编辑器改完请确认 BOM 还在；
    丢了就执行下面的命令补回来（把路径换成实际路径）：

        $p = 'tools\verify_install.ps1'
        $t = [IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
        [IO.File]::WriteAllText($p, $t, (New-Object Text.UTF8Encoding($true)))

.DESCRIPTION
    为什么必须这样验证：
      * 只在项目目录里跑 `tp serve` 是**测不出问题**的——那里本来就有 web/dist，
        即使前端没打进 wheel 也会"看起来正常"。所以必须换到项目之外的目录，
        让 find_web_dist() 只能靠包内嵌的那一份。
      * 只用退出码判断也不够：界面缺失时服务照样能起来，只是首页 404。
        所以要真的请求 HTTP 并把 index.html 的内容取回来核对。

    用法：
        powershell -File tools\verify_install.ps1
        powershell -File tools\verify_install.ps1 -Port 8399 -Keep
#>
[CmdletBinding()]
param(
    [int]$Port = 8399,
    [switch]$Keep
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Whl = Get-ChildItem (Join-Path $Root 'dist\*.whl') -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $Whl) {
    throw "dist\ 下没有 wheel。先跑：python tools\build_release.py"
}

# 刻意放在项目之外：项目里的 web/dist 会让这个测试失去意义
$Sandbox = Join-Path $env:TEMP ("transbook_verify_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$Venv = Join-Path $Sandbox 'venv'
$Work = Join-Path $Sandbox 'work'
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$passed = [System.Collections.Generic.List[string]]::new()
$failed = [System.Collections.Generic.List[string]]::new()
function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { $passed.Add("$Name $Detail") } else { $failed.Add("$Name $Detail") }
    $mark = if ($Ok) { 'PASS' } else { 'FAIL' }
    Write-Host ("  [{0}] {1} {2}" -f $mark, $Name, $Detail)
}

$proc = $null
try {
    Write-Host "`n=== 1. 准备干净环境 ==="
    Write-Host "  沙箱目录: $Sandbox"
    Write-Host "  wheel   : $($Whl.Name)  ($([math]::Round($Whl.Length/1MB,1)) MB)"
    Check '沙箱在项目之外' (-not $Sandbox.StartsWith($Root))

    uv venv $Venv --python 3.12 | Out-Null
    uv pip install --python (Join-Path $Venv 'Scripts\python.exe') $Whl.FullName | Out-Null
    $Tp = Join-Path $Venv 'Scripts\tp.exe'
    Check 'wheel 安装成功' (Test-Path $Tp)

    # 确认装进去的确实是 wheel，不是被 editable 的源码顶掉
    $pkgPath = & (Join-Path $Venv 'Scripts\python.exe') -c "import transbook; print(transbook.__file__)"
    Check '导入的是 site-packages 而非源码' ($pkgPath -like "$Venv*") $pkgPath

    Write-Host "`n=== 2. 确认工作目录及其所有上级都没有 web/dist（否则测试无意义）==="
    $ancestors = @($Work)
    $d = Get-Item $Work
    while ($d.Parent) { $d = $d.Parent; $ancestors += $d.FullName }
    $leaked = @($ancestors | Where-Object { Test-Path (Join-Path $_ 'web\dist\index.html') })
    Check '工作目录及全部上级都无 web/dist' ($leaked.Count -eq 0) ($leaked -join ', ')

    # 关键：以下所有调用都必须把工作目录切到沙箱。否则会命中项目源码树的 web/dist，
    # 那样即便 wheel 里根本没打进前端，测试照样"通过"——这正是第一版脚本的漏洞。
    Write-Host "`n=== 3. 从包内嵌位置定位前端（cwd 已切到沙箱）==="
    Push-Location $Work
    try {
        $found = & (Join-Path $Venv 'Scripts\python.exe') -c @'
from transbook.service import find_web_dist
p = find_web_dist()
print(p if p else "NONE")
'@ 2>&1
    } finally { Pop-Location }
    Check 'find_web_dist 找到界面' ("$found" -notmatch 'NONE') $found
    Check '找到的是包内嵌路径' ("$found" -like "$Venv*")

    Write-Host "`n=== 4. .env 能从工作目录读到（装成 wheel 后的关键路径）==="
    Push-Location $Work
    try {
        & $Tp setup --key 'sk-verify-placeholder-0000' | Out-Null
        Check '.env 写到了当前目录' (Test-Path (Join-Path $Work '.env'))
        $doctor = & $Tp doctor 2>&1 | Out-String
        Check 'doctor 认到了密钥' ($doctor -match 'DEEPSEEK_API_KEY' -and $doctor -notmatch '未设置')
    } finally { Pop-Location }

    Write-Host "`n=== 5. 真的起服务并请求首页 ==="
    $proc = Start-Process -FilePath $Tp `
        -ArgumentList 'serve', '--root', $Work, '--port', $Port `
        -WorkingDirectory $Work `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Sandbox 'serve.out') `
        -RedirectStandardError (Join-Path $Sandbox 'serve.err')

    $ok = $false
    foreach ($i in 1..20) {
        Start-Sleep -Milliseconds 700
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch { }
    }
    Check '首页返回 200' $ok
    if ($ok) {
        Check '首页是 HTML（不是"前端未构建"）' ($r.Content -match '<div id="root">')
        Check '首页引用了打包的 JS' ($r.Content -match '/assets/index-.*\.js')

        $js = [regex]::Match($r.Content, '/assets/index-[^"]+\.js').Value
        $a = Invoke-WebRequest "http://127.0.0.1:$Port$js" -UseBasicParsing -TimeoutSec 5
        Check '静态资源可取' ($a.StatusCode -eq 200 -and $a.RawContentLength -gt 10000) "$($a.RawContentLength) bytes"

        try {
            Invoke-WebRequest "http://127.0.0.1:$Port/api/projects" -UseBasicParsing -TimeoutSec 5 | Out-Null
            Check 'API 可用' $true
        } catch { Check 'API 可用' $false $_.Exception.Message }
    }

    Write-Host "`n=== 6. 未安装 Node 也能工作 ==="
    $envLines = Get-Content (Join-Path $Sandbox 'serve.out') -ErrorAction SilentlyContinue
    Check '启动日志里没有"前端未构建"' (-not ($envLines -match '界面未构建'))
} finally {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    if ($Keep) {
        Write-Host "`n（保留沙箱：$Sandbox）"
    } else {
        Remove-Item $Sandbox -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "`n============================================"
Write-Host ("通过 {0} 项，失败 {1} 项" -f $passed.Count, $failed.Count)
if ($failed.Count) {
    Write-Host "失败项："
    $failed | ForEach-Object { Write-Host "  - $_" }
    exit 1
}
Write-Host "发布包开箱即用验证通过。"
exit 0
