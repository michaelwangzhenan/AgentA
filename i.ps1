<#
.SYNOPSIS
  确保虚拟环境已激活，然后把参数透传给 tools/dev_server.ps1。

.DESCRIPTION
  参数与 tools/dev_server.ps1 保持一致，按 Tab 可补全命令（ValidateSet）。
  getlog / sync / scpto / scpfrom 是例外：直接在本脚本里处理（scp 与 VPS 互传），不转发、不需要虚拟环境。

.EXAMPLE
  .\i.ps1 start            -> .\tools\dev_server.ps1 start
  .\i.ps1 restart vite     -> .\tools\dev_server.ps1 restart vite
  .\i.ps1 status           -> .\tools\dev_server.ps1 status
  .\i.ps1 getlog           -> scp 拉取远程服务器的 uvicorn.log 到 logs\vps\
  .\i.ps1 sync             -> scp 推送 git status 中的变更文件到 VPS
  .\i.ps1 scpto tools/cli/getchat.py tools/cli/getchat.py
  .\i.ps1 scpfrom history/admin123_chat.md docs/chat-admin123.md
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'logs', 'status', 'getlog', 'sync', 'scpto', 'scpfrom', 'help')]
    [string]$Action = 'help',

    [Parameter(Position = 1)]
    [string]$Arg1 = '',

    [Parameter(Position = 2)]
    [string]$Arg2 = ''
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir   = Join-Path $ScriptDir '.venv'
$Activate  = Join-Path $VenvDir 'Scripts\Activate.ps1'
$DevScript = Join-Path $ScriptDir 'tools\dev_server.ps1'

$RemoteHost = 'admin@47.96.93.237'
$RemoteBase = '/home/admin/AgentA'
$SshOpts    = @('-o', 'BatchMode=yes')

function ConvertTo-UnixRel([string]$RelPath) {
    return $RelPath.Replace('\', '/').TrimStart('./')
}

function Resolve-LocalRepoPath([string]$RelPath) {
    if (-not $RelPath) { return $null }
    $candidate = Join-Path $ScriptDir (ConvertTo-UnixRel $RelPath)
    if (-not (Test-Path -LiteralPath $candidate)) { return $null }
    $full = (Resolve-Path -LiteralPath $candidate).Path
    $root = (Resolve-Path $ScriptDir).Path
    if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        return $null
    }
    return $full
}

function Ensure-LocalParent([string]$LocalFile) {
    $parent = Split-Path -Parent $LocalFile
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Ensure-RemoteParent([string]$RemoteRel) {
    $unixRel = ConvertTo-UnixRel $RemoteRel
    $parent = [System.IO.Path]::GetDirectoryName($unixRel)
    if (-not $parent -or $parent -eq '.') { return $true }
    $mkdirCmd = 'mkdir -p ' + "'" + $RemoteBase + '/' + $parent.Replace("'", "'\\''") + "'"
    ssh @SshOpts $RemoteHost $mkdirCmd
    return $LASTEXITCODE -eq 0
}

function Invoke-ScpTo {
    param([string]$LocalRel, [string]$RemoteRel)
    if (-not $LocalRel -or -not $RemoteRel) {
        Write-Error '用法: .\i.ps1 scpto <本地相对路径> <远程相对路径>'
        exit 1
    }
    $local = Resolve-LocalRepoPath $LocalRel
    if (-not $local) {
        Write-Error "本地文件不存在或路径非法: $LocalRel"
        exit 1
    }
    $remoteRel = ConvertTo-UnixRel $RemoteRel
    if (-not (Ensure-RemoteParent $remoteRel)) {
        Write-Error "远程目录创建失败: $remoteRel"
        exit 1
    }
    $remoteDest = "${RemoteHost}:${RemoteBase}/${remoteRel}"
    Write-Host "上传 $LocalRel -> $remoteDest"
    scp @SshOpts $local $remoteDest
    exit $LASTEXITCODE
}

function Invoke-ScpFrom {
    param([string]$RemoteRel, [string]$LocalRel)
    if (-not $RemoteRel -or -not $LocalRel) {
        Write-Error '用法: .\i.ps1 scpfrom <远程相对路径> <本地相对路径>'
        exit 1
    }
    $remoteRel = ConvertTo-UnixRel $RemoteRel
    $local = Join-Path $ScriptDir (ConvertTo-UnixRel $LocalRel)
    $root = (Resolve-Path $ScriptDir).Path
    try {
        $localResolved = [System.IO.Path]::GetFullPath($local)
    } catch {
        Write-Error "本地路径非法: $LocalRel"
        exit 1
    }
    if (-not $localResolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Error "本地路径必须位于仓库内: $LocalRel"
        exit 1
    }
    Ensure-LocalParent $localResolved
    $remoteSrc = "${RemoteHost}:${RemoteBase}/${remoteRel}"
    Write-Host "下载 $remoteSrc -> $LocalRel"
    scp @SshOpts $remoteSrc $localResolved
    exit $LASTEXITCODE
}

# getlog：拉远程日志，跟本地 dev server 管理无关，不需要虚拟环境，直接处理并退出。
if ($Action -eq 'getlog') {
    $RemotePath = "$RemoteBase/logs/uvicorn.log"
    $LocalDir = Join-Path $ScriptDir 'logs\vps'
    if (-not (Test-Path $LocalDir)) { New-Item -ItemType Directory -Path $LocalDir | Out-Null }
    scp "${RemoteHost}:${RemotePath}" "$LocalDir\"
    exit $LASTEXITCODE
}

# sync：推送 git status 变更到 VPS，不需要虚拟环境。
if ($Action -eq 'sync') {
    $SyncScript = Join-Path $ScriptDir 'tools\sync_vps.ps1'
    & $SyncScript -RemoteHost $RemoteHost -RemoteBase $RemoteBase
    exit $LASTEXITCODE
}

if ($Action -eq 'scpto') {
    Invoke-ScpTo -LocalRel $Arg1 -RemoteRel $Arg2
}

if ($Action -eq 'scpfrom') {
    Invoke-ScpFrom -RemoteRel $Arg1 -LocalRel $Arg2
}

# $env:VIRTUAL_ENV 由 Activate.ps1 设置；指向本项目 venv 才算已激活，否则才激活。
$activated = $env:VIRTUAL_ENV -and (Test-Path $env:VIRTUAL_ENV) -and `
    ((Resolve-Path $env:VIRTUAL_ENV).Path -eq (Resolve-Path $VenvDir).Path)
if (-not $activated) {
    . $Activate
}

# 只转发用户实际给出的参数，避免把默认值硬塞给 dev_server.ps1
$forward = @($Action)
if ($Arg1) { $forward += $Arg1 }
& $DevScript @forward
