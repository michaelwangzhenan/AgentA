<#
.SYNOPSIS
  确保虚拟环境已激活，然后把参数透传给 tools/dev_server.ps1。

.DESCRIPTION
  参数与 tools/dev_server.ps1 保持一致；一级命令用 ValidateSet，二级目标（uvicorn/vite）用 ArgumentCompleter。
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
    [Alias('h')]
    [switch]$Help,

    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'logs', 'status', 'getlog', 'sync', 'scpto', 'scpfrom', 'help', '--help')]
    [string]$Action = 'help',

    [Parameter(Position = 1)]
    [ArgumentCompleter({
        param($CommandName, $ParameterName, $WordToComplete, $CommandAst, $FakeBoundParameters)
        $action = $FakeBoundParameters['Action']
        if ($action -in 'start', 'stop', 'restart', 'logs') {
            'uvicorn', 'vite' |
                Where-Object { $_ -like "$WordToComplete*" } |
                ForEach-Object {
                    [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
                }
        }
    })]
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

function Show-IHelp {
    Write-Host ""
    Write-Host "i.ps1 - AgentA dev helper (activates .venv, forwards to dev_server.ps1)"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  i start                  Start uvicorn (:8000) + vite (:5173) in background"
    Write-Host "  i stop     [uvicorn|vite] Stop one or both dev servers"
    Write-Host "  i restart  [uvicorn|vite] Restart one or both dev servers"
    Write-Host "  i logs     uvicorn|vite   Tail server log (Ctrl+C to stop viewing)"
    Write-Host "  i status                 Show PID / port / URL"
    Write-Host "  i getlog                 Fetch remote uvicorn.log to logs\vps\"
    Write-Host "  i sync                   Push git-changed files to VPS via scp"
    Write-Host "  i scpto  <local> <remote> Upload a repo file to VPS"
    Write-Host "  i scpfrom <remote> <local> Download a VPS file into the repo"
    Write-Host "  i help | -h | --help     Show this help"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  i scpto .env .env"
    Write-Host "  i scpfrom history/admin123_chat.md docs/chat-admin123.md"
    Write-Host ""
}

if ($Help -or $Action -in 'help', '--help') {
    Show-IHelp
    exit 0
}

function ConvertTo-UnixRel([string]$RelPath) {
    $normalized = $RelPath.Replace('\', '/')
    if ($normalized.StartsWith('./')) {
        return $normalized.Substring(2)
    }
    return $normalized
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
        Write-Error 'Usage: i scpto <local-path> <remote-path>'
        exit 1
    }
    $local = Resolve-LocalRepoPath $LocalRel
    if (-not $local) {
        Write-Error "Local file not found or invalid path: $LocalRel"
        exit 1
    }
    $remoteRel = ConvertTo-UnixRel $RemoteRel
    if (-not (Ensure-RemoteParent $remoteRel)) {
        Write-Error "Failed to create remote directory: $remoteRel"
        exit 1
    }
    $remoteDest = "${RemoteHost}:${RemoteBase}/${remoteRel}"
    Write-Host "Upload $LocalRel -> $remoteDest"
    scp @SshOpts $local $remoteDest
    exit $LASTEXITCODE
}

function Invoke-ScpFrom {
    param([string]$RemoteRel, [string]$LocalRel)
    if (-not $RemoteRel -or -not $LocalRel) {
        Write-Error 'Usage: i scpfrom <remote-path> <local-path>'
        exit 1
    }
    $remoteRel = ConvertTo-UnixRel $RemoteRel
    $local = Join-Path $ScriptDir (ConvertTo-UnixRel $LocalRel)
    $root = (Resolve-Path $ScriptDir).Path
    try {
        $localResolved = [System.IO.Path]::GetFullPath($local)
    } catch {
        Write-Error "Invalid local path: $LocalRel"
        exit 1
    }
    if (-not $localResolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Error "Local path must be inside the repo: $LocalRel"
        exit 1
    }
    Ensure-LocalParent $localResolved
    $remoteSrc = "${RemoteHost}:${RemoteBase}/${remoteRel}"
    Write-Host "Download $remoteSrc -> $LocalRel"
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
