<#
.SYNOPSIS
  确保虚拟环境已激活，然后把参数透传给 tools/dev_server.ps1。

.DESCRIPTION
  参数与 tools/dev_server.ps1 保持一致，按 Tab 可补全命令（ValidateSet）。
  getlog 是例外：直接在本脚本里处理（scp 拉远程日志），不转发、不需要虚拟环境。

.EXAMPLE
  .\i.ps1 start            -> .\tools\dev_server.ps1 start
  .\i.ps1 restart vite     -> .\tools\dev_server.ps1 restart vite
  .\i.ps1 status           -> .\tools\dev_server.ps1 status
  .\i.ps1 getlog           -> scp 拉取远程服务器的 uvicorn.log 到 logs\vps\
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'logs', 'status', 'getlog', 'help')]
    [string]$Action = 'help',

    [Parameter(Position = 1)]
    [ValidateSet('uvicorn', 'vite')]
    [string]$Target = ''
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir   = Join-Path $ScriptDir '.venv'
$Activate  = Join-Path $VenvDir 'Scripts\Activate.ps1'
$DevScript = Join-Path $ScriptDir 'tools\dev_server.ps1'

# getlog：拉远程日志，跟本地 dev server 管理无关，不需要虚拟环境，直接处理并退出。
if ($Action -eq 'getlog') {
    $RemoteHost = 'root@114.55.66.20'
    $RemotePath = '/root/AgentA/logs/uvicorn.log'
    $LocalDir = Join-Path $ScriptDir 'logs\vps'
    if (-not (Test-Path $LocalDir)) { New-Item -ItemType Directory -Path $LocalDir | Out-Null }
    scp "${RemoteHost}:${RemotePath}" "$LocalDir\"
    exit $LASTEXITCODE
}

# $env:VIRTUAL_ENV 由 Activate.ps1 设置；指向本项目 venv 才算已激活，否则才激活。
$activated = $env:VIRTUAL_ENV -and (Test-Path $env:VIRTUAL_ENV) -and `
    ((Resolve-Path $env:VIRTUAL_ENV).Path -eq (Resolve-Path $VenvDir).Path)
if (-not $activated) {
    . $Activate
}

# 只转发用户实际给出的参数，避免把默认值硬塞给 dev_server.ps1
$forward = @($Action)
if ($Target) { $forward += $Target }
& $DevScript @forward
