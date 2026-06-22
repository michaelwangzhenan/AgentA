<#
.SYNOPSIS
  管理 AgentA UI 的两个 dev server：uvicorn（后端 :8000）+ vite（前端 :5173）。

.DESCRIPTION
  命令：
    dev_server.ps1 start                   两个 server 一起后台启动
    dev_server.ps1 stop                    两个一起停
    dev_server.ps1 stop uvicorn|vite       只停其中一个
    dev_server.ps1 restart                 两个一起重启（先停再起）
    dev_server.ps1 restart uvicorn|vite    只重启其中一个
    dev_server.ps1 logs uvicorn|vite       tail -f 对应日志（Ctrl+C 只退出"看日志"，服务继续跑）
    dev_server.ps1 status                  显示两个服务的 PID / 端口 / URL
    dev_server.ps1 help                    显示帮助（不带参数时也显示）

  约定：
    .run/<name>.pid                后台进程 PID（cmd.exe 包装器）
    logs/<name>.log                合并的 stdout + stderr
    已在跑（PID 文件 或 端口已占）时 start 跳过 + 警告。
    stop 用 taskkill /T /F 杀进程树，避免 npm/node、uvicorn reloader/worker 留孤儿。
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'logs', 'status', 'help')]
    [string]$Action = 'help',

    [Parameter(Position = 1)]
    [ValidateSet('uvicorn', 'vite')]
    [string]$Target = ''
)

$ErrorActionPreference = 'Stop'

# PowerShell 5.1 默认按系统代码页输出，中文会乱码。强制 UTF-8 仅影响当前进程。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$RunDir      = Join-Path $ProjectRoot '.run'
$LogDir      = Join-Path $ProjectRoot 'logs'
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$VenvPython  = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

foreach ($d in @($RunDir, $LogDir)) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: venv python not found at $VenvPython" -ForegroundColor Red
    exit 1
}

# 服务配置
#   LogFile        ：`logs` 命令 tail 的目标（干净的业务/访问日志）
#   Redirect       ：包装脚本 stdout+stderr 重定向到的文件
#   ArchiveOnStart ：启动时把旧 Redirect 文件归档（保留最近 3 份）而非清空
#   Env            ：写进包装脚本的环境变量行
# uvicorn 走 `python -m src.api.run`：日志由后端 RotatingFileHandler 直接写 uvicorn.log
#   （带 [APP]/[ACCESS] 前缀、按大小滚动、跨启动追加），不靠 shell 重定向。包装脚本的
#   stdout+stderr 另存 uvicorn.boot.log，只兜底捕获早期崩溃 / MCP 子进程裸输出。
$Services = [ordered]@{
    uvicorn = @{
        Port           = 8000
        Url            = 'http://localhost:8000/docs'
        PidFile        = Join-Path $RunDir 'uvicorn.pid'
        LogFile        = Join-Path $LogDir 'uvicorn.log'
        Redirect       = Join-Path $LogDir 'uvicorn.boot.log'
        ArchiveOnStart = $false
        Env            = @('set PYTHONIOENCODING=utf-8')
        CmdLine        = "`"$VenvPython`" -m src.api.run"
        Cwd            = $ProjectRoot
    }
    vite = @{
        Port           = 5173
        Url            = 'http://localhost:5173/'
        PidFile        = Join-Path $RunDir 'vite.pid'
        LogFile        = Join-Path $LogDir 'vite.log'
        Redirect       = Join-Path $LogDir 'vite.log'
        ArchiveOnStart = $true
        # FORCE_COLOR=0 / NO_COLOR=1：让 vite 不输出 ANSI 颜色码，避免日志文件里全是转义序列（F4）
        Env            = @('set FORCE_COLOR=0', 'set NO_COLOR=1')
        CmdLine        = 'npm.cmd run dev'
        Cwd            = $FrontendDir
    }
}

function Rotate-LogOnStart {
    param([string]$Path, [int]$Keep = 3)
    if (-not (Test-Path $Path)) { return }
    if ((Get-Item $Path).Length -eq 0) { return }  # 空文件不归档
    $oldest = "$Path.$Keep"
    if (Test-Path $oldest) { Remove-Item $oldest -Force -ErrorAction SilentlyContinue }
    for ($i = $Keep - 1; $i -ge 1; $i--) {
        $src = "$Path.$i"
        if (Test-Path $src) { Move-Item $src "$Path.$($i + 1)" -Force -ErrorAction SilentlyContinue }
    }
    Move-Item $Path "$Path.1" -Force -ErrorAction SilentlyContinue
}

function Get-State {
    param($Svc)
    $state = @{ Running = $false; ProcId = $null; Source = 'none' }

    if (Test-Path $Svc.PidFile) {
        $raw = (Get-Content $Svc.PidFile -ErrorAction SilentlyContinue) -as [int]
        if ($raw -and (Get-Process -Id $raw -ErrorAction SilentlyContinue)) {
            $state.Running = $true
            $state.ProcId  = $raw
            $state.Source  = 'pidfile'
            return $state
        }
    }

    try {
        $conn = Get-NetTCPConnection -LocalPort $Svc.Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -First 1
        if ($conn) {
            $state.Running = $true
            $state.ProcId  = [int]$conn.OwningProcess
            $state.Source  = 'port'
        }
    } catch {
        # Get-NetTCPConnection 不可用时静默
    }

    return $state
}

function Start-One {
    param([string]$Name, $Svc)

    $state = Get-State $Svc
    if ($state.Running) {
        Write-Host "[$Name] already running (PID $($state.ProcId), via $($state.Source)). Skipped." -ForegroundColor Yellow
        Write-Host "  URL : $($Svc.Url)"
        Write-Host "  Hint: run ``dev_server.ps1 stop $Name`` first if you want to restart."
        return
    }

    # 用 .cmd 包装：避开 PowerShell -> cmd.exe 的引号转义陷阱（路径含空格也安全）
    $wrapper  = Join-Path $RunDir "$Name.cmd"
    $redirect = if ($Svc.Redirect) { $Svc.Redirect } else { $Svc.LogFile }
    $envLines = ($Svc.Env -join "`r`n")
    $wrapperContent = @"
@echo off
cd /d "$($Svc.Cwd)"
$envLines
$($Svc.CmdLine) > "$redirect" 2>&1
"@
    Set-Content -Path $wrapper -Value $wrapperContent -Encoding ascii

    # F3：启动时归档旧日志（保留最近 3 份）而非清空。
    # uvicorn.log 由后端 RotatingFileHandler 自管（跨启动追加 + 按大小滚动），不在此处理。
    if ($Svc.ArchiveOnStart) {
        Rotate-LogOnStart $redirect 3
    }

    $proc = Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c', $wrapper `
        -WorkingDirectory $Svc.Cwd `
        -WindowStyle Hidden `
        -PassThru

    Set-Content -Path $Svc.PidFile -Value $proc.Id -Encoding ascii

    Start-Sleep -Milliseconds 800
    if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
        Write-Host "[$Name] started (PID $($proc.Id))" -ForegroundColor Green
        Write-Host "  URL : $($Svc.Url)"
        Write-Host "  Log : $($Svc.LogFile)"
    }
    else {
        Write-Host "[$Name] failed to start. See log: $redirect" -ForegroundColor Red
        Remove-Item $Svc.PidFile -ErrorAction SilentlyContinue
    }
}

function Stop-One {
    param([string]$Name, $Svc)

    $state = Get-State $Svc
    if (-not $state.Running) {
        Write-Host "[$Name] not running." -ForegroundColor DarkGray
        Remove-Item $Svc.PidFile -ErrorAction SilentlyContinue
        return
    }

    Write-Host "[$Name] stopping PID $($state.ProcId) (via $($state.Source))..." -ForegroundColor Cyan
    # /T 杀整个进程树：cmd.exe -> python/uvicorn(reloader+worker) 或 cmd.exe -> npm -> node(vite)
    & taskkill /PID $state.ProcId /T /F 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[$Name] stopped." -ForegroundColor Green
    }
    else {
        Write-Host "[$Name] taskkill exit $LASTEXITCODE — process may already be gone." -ForegroundColor Yellow
    }
    Remove-Item $Svc.PidFile -ErrorAction SilentlyContinue
}

function Restart-One {
    param([string]$Name, $Svc)

    Stop-One $Name $Svc
    # 等端口/进程树彻底释放，避免紧接着 start 时端口仍被占而跳过
    Start-Sleep -Milliseconds 1200
    Start-One $Name $Svc
}

function Show-Status {
    Write-Host ""
    $fmt = "{0,-10} {1,-9} {2,-7} {3,-8} {4}"
    Write-Host ($fmt -f 'service', 'status', 'pid', 'source', 'url')
    Write-Host ("-" * 70)
    foreach ($n in $Services.Keys) {
        $s  = $Services[$n]
        $st = Get-State $s
        if ($st.Running) {
            $status = 'RUNNING'
            $color  = 'Green'
            $pidStr = "$($st.ProcId)"
        }
        else {
            $status = 'stopped'
            $color  = 'DarkGray'
            $pidStr = '-'
        }
        Write-Host ($fmt -f $n, $status, $pidStr, $st.Source, $s.Url) -ForegroundColor $color
    }
    Write-Host ""
}

function Show-Help {
    Write-Host ""
    Write-Host "dev_server.ps1 — 管理 AgentA UI 的两个 dev server"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  dev_server.ps1 start                  启动 uvicorn (:8000) + vite (:5173)，后台运行"
    Write-Host "  dev_server.ps1 stop    [uvicorn|vite]  停止（不带参数 = 都停）"
    Write-Host "  dev_server.ps1 restart [uvicorn|vite]  重启（先停再起，不带参数 = 都重启）"
    Write-Host "  dev_server.ps1 logs     uvicorn|vite   tail -f 日志（Ctrl+C 退出查看，服务继续跑）"
    Write-Host "  dev_server.ps1 status                 显示 PID / 端口 / URL"
    Write-Host "  dev_server.ps1 help                   显示本帮助（不带参数时默认显示）"
    Write-Host ""
    Write-Host "Files:"
    Write-Host "  .run/<name>.pid               后台进程 PID（脚本自管）"
    Write-Host "  logs/<name>.log               合并的 stdout + stderr"
    Write-Host ""
}

function Tail-Log {
    param([string]$Name, $Svc)

    if (-not (Test-Path $Svc.LogFile)) {
        Write-Host "[$Name] no log file yet: $($Svc.LogFile)" -ForegroundColor Yellow
        return
    }
    Write-Host "Tailing $($Svc.LogFile) — Ctrl+C 退出查看，服务继续跑。" -ForegroundColor Cyan
    Write-Host ("-" * 70)
    Get-Content -Path $Svc.LogFile -Wait -Tail 50
}

# ── 分发 ──────────────────────────────────────────────────────────────
switch ($Action) {
    'start' {
        if ($Target) {
            Start-One $Target $Services[$Target]
        }
        else {
            foreach ($n in $Services.Keys) { Start-One $n $Services[$n] }
        }
        Show-Status
    }
    'stop' {
        if ($Target) {
            Stop-One $Target $Services[$Target]
        }
        else {
            foreach ($n in $Services.Keys) { Stop-One $n $Services[$n] }
        }
    }
    'restart' {
        if ($Target) {
            Restart-One $Target $Services[$Target]
        }
        else {
            foreach ($n in $Services.Keys) { Restart-One $n $Services[$n] }
        }
        Show-Status
    }
    'logs' {
        if (-not $Target) {
            Write-Host "Usage: dev_server.ps1 logs uvicorn|vite" -ForegroundColor Red
            exit 1
        }
        Tail-Log $Target $Services[$Target]
    }
    'status' {
        Show-Status
    }
    'help' {
        Show-Help
    }
}
