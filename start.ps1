$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectRoot ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$Config = Join-Path $ProjectRoot "config.json"
$Url = "http://127.0.0.1:8000"

Set-Location $ProjectRoot

Write-Host "== Aliyun ASR Translator ==" -ForegroundColor Cyan

if (-not (Test-Path $Config)) {
    Write-Host "config.json was not found. Creating it from config.example.json." -ForegroundColor Yellow
    Copy-Item (Join-Path $ProjectRoot "config.example.json") $Config
    Write-Host "Please fill config.json, then run this script again." -ForegroundColor Yellow
    Start-Process notepad.exe $Config
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not (Test-Path $Python)) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Cyan
    python -m venv $VenvDir
}

Write-Host "Installing/checking dependencies..." -ForegroundColor Cyan
& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt") -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com

Write-Host "Starting server: $Url" -ForegroundColor Green
Start-Process $Url

& $Python -m uvicorn app:app --host 127.0.0.1 --port 8000
