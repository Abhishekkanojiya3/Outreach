$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Join-Path $backend "venv\Scripts\python.exe"
$nodeModules = Join-Path $frontend "node_modules"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$cloudflaredLog = Join-Path $backend "cloudflared.log"

if (-not (Test-Path $python)) {
    Write-Host "Backend venv not found. Creating it now..." -ForegroundColor Cyan
    Push-Location $backend
    python -m venv venv
    & $python -m pip install -r requirements.txt
    Pop-Location
}

Write-Host "Starting backend on http://localhost:5000" -ForegroundColor Green
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$backend'; `$env:FLASK_DEBUG='1'; `$env:FLASK_RELOAD='1'; & '$python' app.py"
)

Write-Host "Waiting for backend..." -ForegroundColor Cyan
$backendReady = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    try {
        Invoke-RestMethod -Method Get -Uri "http://localhost:5000/api/settings" | Out-Null
        $backendReady = $true
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

if ($backendReady -and (Test-Path $cloudflared)) {
    Write-Host "Starting Cloudflare tunnel for open tracking..." -ForegroundColor Green
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
    Remove-Item $cloudflaredLog -Force -ErrorAction SilentlyContinue

    Start-Process -FilePath $cloudflared `
        -ArgumentList "tunnel", "--url", "http://localhost:5000", "--protocol", "http2" `
        -WindowStyle Hidden -RedirectStandardError $cloudflaredLog

    $trackingUrl = $null
    foreach ($i in 1..30) {
        Start-Sleep -Seconds 2
        $log = Get-Content $cloudflaredLog -Raw -ErrorAction SilentlyContinue
        if ($log -match "https://[a-z0-9\-]+\.trycloudflare\.com") {
            $trackingUrl = $Matches[0]
            break
        }
    }

    if ($trackingUrl) {
        Invoke-RestMethod -Method Post -Uri "http://localhost:5000/api/settings" `
            -ContentType "application/json" `
            -Body (@{ tracking_base_url = $trackingUrl } | ConvertTo-Json) | Out-Null
        Write-Host "Open tracking URL: $trackingUrl" -ForegroundColor Cyan
    } else {
        Write-Host "Cloudflare tunnel did not return a URL. Open tracking will be disabled until the tunnel is fixed." -ForegroundColor Yellow
    }
} elseif (-not $backendReady) {
    Write-Host "Backend did not become ready. Start tracking was skipped." -ForegroundColor Yellow
} else {
    Write-Host "cloudflared not found at $cloudflared. Open tracking was skipped." -ForegroundColor Yellow
}

Write-Host "Starting frontend on http://localhost:5173" -ForegroundColor Green
$frontendCommand = "Set-Location '$frontend'; "
if (-not (Test-Path $nodeModules)) {
    $frontendCommand += "npm install; "
}
$frontendCommand += "npm run dev"

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    $frontendCommand
)

Write-Host ""
Write-Host "Local app: http://localhost:5173" -ForegroundColor Cyan
Write-Host "Backend reloads on Python changes. Frontend hot-reloads on React/CSS changes." -ForegroundColor Cyan
Write-Host "Keep the backend window open while sending emails so open tracking can receive pixel requests." -ForegroundColor Cyan
