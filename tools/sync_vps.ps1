# 把 git status 中的变更文件 scp 到 VPS 对应路径。
param(
    [string]$RemoteHost = 'admin@47.96.93.237',
    [string]$RemoteBase = '/home/admin/AgentA'
)

$ScriptDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Test-SyncExcluded([string]$RelPath) {
    $norm = $RelPath.Replace('\', '/').TrimStart('./')
    $exclude = @('.env', '.venv/', 'node_modules/', '.pytest_cache/', '__pycache__/', 'logs/')
    foreach ($pat in $exclude) {
        if ($pat.EndsWith('/')) {
            if ($norm -like ($pat + '*') -or $norm -like ('*/' + $pat + '*')) { return $true }
        } elseif ($norm -eq $pat -or $norm -like ('*/' + $pat)) {
            return $true
        }
    }
    return $false
}

Push-Location $ScriptDir
$raw = & git status --porcelain -z 2>&1
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Error "git status failed: $raw"
    exit 1
}

$paths = New-Object System.Collections.Generic.List[string]
$parts = $raw -split [char]0 | Where-Object { $_ }
for ($i = 0; $i -lt $parts.Count; ) {
    $entry = $parts[$i]
    $i++
    if ($entry.Length -lt 4) { continue }

    $status = $entry.Substring(0, 2)
    $path = $entry.Substring(3)
    if ($status[0] -eq 'R' -or $status[0] -eq 'C' -or $status[1] -eq 'R' -or $status[1] -eq 'C') {
        if ($i -ge $parts.Count) { break }
        $path = $parts[$i]
        $i++
    }
    if ($status -match 'D') { continue }
    if (Test-SyncExcluded $path) {
        Write-Host "Skipped: $path"
        continue
    }
    if (-not $paths.Contains($path)) { [void]$paths.Add($path) }
}

if ($paths.Count -eq 0) {
    Pop-Location
    Write-Host 'No files to sync.'
    exit 0
}

$dest = $RemoteHost + ':' + $RemoteBase + '/'
Write-Host ('Syncing ' + $paths.Count + ' file(s) to ' + $dest)
$sshOpts = @('-o', 'BatchMode=yes')
$failed = 0
foreach ($rel in $paths) {
    $local = Join-Path $ScriptDir $rel
    if (-not (Test-Path -LiteralPath $local)) {
        Write-Warning "Local file missing, skipped: $rel"
        continue
    }

    $unixRel = $rel.Replace('\', '/')
    $parent = [System.IO.Path]::GetDirectoryName($unixRel)
    if ($parent -and $parent -ne '.') {
        $mkdirCmd = 'mkdir -p ' + "'" + $RemoteBase + '/' + $parent + "'"
        ssh @sshOpts $RemoteHost $mkdirCmd
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Failed to create remote directory: $parent"
            $failed++
            continue
        }
    }

    $remoteDest = $RemoteHost + ':' + $RemoteBase + '/' + $unixRel
    Write-Host ('  -> ' + $unixRel)
    scp -q @sshOpts $local $remoteDest
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Sync failed: $rel"
        $failed++
    }
}
Pop-Location

if ($failed -gt 0) {
    Write-Host ('Done with ' + $failed + ' failed file(s).')
    exit 1
}
Write-Host 'Sync complete.'
exit 0