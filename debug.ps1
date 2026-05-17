# debug.ps1 - Start Chainlit + cloudflared tunnel
param(
    [int]$Port = 8000,
    [string]$App = "chainlit_app.py"
)

$ErrorActionPreference = "Stop"
$VenvActivate = ".\.venv\Scripts\Activate.ps1"

# Activate venv if not already active
if (-not $env:VIRTUAL_ENV) {
    if (Test-Path $VenvActivate) {
        & $VenvActivate
    }
}

# Start Chainlit in a new window
Write-Host "[1/3] Starting Chainlit on port $Port ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "chainlit run $App --port $Port"

# Wait for port to be ready
Write-Host "[2/3] Waiting for localhost:$Port ..." -ForegroundColor Gray
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $result = Test-NetConnection -ComputerName "127.0.0.1" -Port $Port -WarningAction SilentlyContinue
    if ($result.TcpTestSucceeded) {
        $ready = $true
        break
    }
}

if (-not $ready) {
    Write-Host "ERROR: Chainlit not ready after 30s. Check the new window for errors." -ForegroundColor Red
    exit 1
}
Write-Host "OK: Chainlit ready at http://localhost:$Port" -ForegroundColor Green

# Find cloudflared
Write-Host "[3/3] Starting cloudflared tunnel ..." -ForegroundColor Cyan
$cfPath = $null
if (Get-Command "cloudflared" -ErrorAction SilentlyContinue) {
    $cfPath = "cloudflared"
} elseif (Test-Path ".\cloudflared.exe") {
    $cfPath = ".\cloudflared.exe"
} else {
    Write-Host "ERROR: cloudflared not found." -ForegroundColor Red
    Write-Host "  Download from: https://github.com/cloudflare/cloudflared/releases/latest" -ForegroundColor Yellow
    Write-Host "  Place cloudflared.exe in this folder, then re-run." -ForegroundColor Yellow
    exit 1
}

# Capture tunnel URL from stderr
$logFile = "$env:TEMP\cf-tunnel-$PID.log"
$proc = Start-Process -FilePath $cfPath `
    -ArgumentList "tunnel", "--url", "http://localhost:$Port" `
    -RedirectStandardError $logFile `
    -PassThru -NoNewWindow

# Wait for URL
$tunnelUrl = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Path $logFile) {
        $text = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
        if ($text -match 'https://[a-z0-9\-]+\.trycloudflare\.com') {
            $tunnelUrl = $matches[0]
            break
        }
    }
}

if ($tunnelUrl) {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Magenta
    Write-Host "  Tunnel URL (send this to AI):" -ForegroundColor White
    Write-Host "  $tunnelUrl" -ForegroundColor Yellow
    Write-Host "==========================================" -ForegroundColor Magenta
    $tunnelUrl | Set-Clipboard
    Write-Host "  Copied to clipboard." -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "WARNING: Could not capture tunnel URL. Log: $logFile" -ForegroundColor Yellow
}

Write-Host "Press Ctrl+C to stop the tunnel. (Close Chainlit window separately.)" -ForegroundColor Gray

# Keep running until Ctrl+C
$proc.WaitForExit()
if (Test-Path $logFile) { Remove-Item $logFile -Force }