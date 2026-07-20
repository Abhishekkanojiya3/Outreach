# Starts the Cloudflare tunnel for email open tracking and saves the URL in app settings.
# Usage: right-click -> Run with PowerShell (backend must be running on port 5000 first)

$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$logFile = Join-Path $PSScriptRoot "backend\cloudflared.log"

# Stop any old tunnel
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Remove-Item $logFile -Force -ErrorAction SilentlyContinue

Write-Host "Starting Cloudflare tunnel..." -ForegroundColor Cyan
Start-Process -FilePath $cloudflared `
    -ArgumentList "tunnel", "--url", "http://localhost:5000", "--protocol", "http2" `
    -WindowStyle Hidden -RedirectStandardError $logFile

# Wait for the public URL to appear in the log
$url = $null
foreach ($i in 1..30) {
    Start-Sleep -Seconds 2
    $log = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
    if ($log -match "https://[a-z0-9\-]+\.trycloudflare\.com") {
        $url = $Matches[0]
        if ($log -match "Registered tunnel connection") { break }
    }
}

if (-not $url) {
    Write-Host "ERROR: Tunnel did not start. Check $logFile" -ForegroundColor Red
    exit 1
}

Write-Host "Tunnel URL: $url" -ForegroundColor Green

# Save into app settings
try {
    Invoke-RestMethod -Method Post -Uri "http://localhost:5000/api/settings" `
        -ContentType "application/json" `
        -Body (@{ tracking_base_url = $url } | ConvertTo-Json) | Out-Null
    Write-Host "Saved in app settings. Open tracking is ACTIVE." -ForegroundColor Green
    Write-Host "Keep this tunnel running while sending emails and waiting for opens." -ForegroundColor Yellow
} catch {
    Write-Host "Backend not running on port 5000 — start it first, then re-run this script." -ForegroundColor Red
}
