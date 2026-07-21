$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Join-Path $backend "venv\Scripts\python.exe"
$nodeModules = Join-Path $frontend "node_modules"

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
