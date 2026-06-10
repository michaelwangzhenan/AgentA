<#
.SYNOPSIS
  确保虚拟环境已激活，然后把参数透传给 tools/ui.ps1。

.DESCRIPTION
  参数与 tools/ui.ps1 保持一致，按 Tab 可补全命令（ValidateSet）。

.EXAMPLE
  .\start.ps1 start            -> .\tools\ui.ps1 start
  .\start.ps1 restart vite     -> .\tools\ui.ps1 restart vite
  .\start.ps1 status           -> .\tools\ui.ps1 status
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

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir   = Join-Path $ScriptDir '.venv'
$Activate  = Join-Path $VenvDir 'Scripts\Activate.ps1'
$UiScript  = Join-Path $ScriptDir 'tools\ui.ps1'

# $env:VIRTUAL_ENV 由 Activate.ps1 设置；指向本项目 venv 才算已激活，否则才激活。
$activated = $env:VIRTUAL_ENV -and (Test-Path $env:VIRTUAL_ENV) -and `
    ((Resolve-Path $env:VIRTUAL_ENV).Path -eq (Resolve-Path $VenvDir).Path)
if (-not $activated) {
    . $Activate
}

# 只转发用户实际给出的参数，避免把默认值硬塞给 ui.ps1
$forward = @($Action)
if ($Target) { $forward += $Target }
& $UiScript @forward
