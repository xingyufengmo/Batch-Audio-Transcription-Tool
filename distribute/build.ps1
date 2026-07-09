$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$VenvDir = Join-Path $ProjectRoot ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$AppName = "AliyunASRTranslator"
$AppDir = Join-Path $ReleaseRoot $AppName

Set-Location $ProjectRoot

if (-not (Test-Path $Python)) {
    python -m venv $VenvDir
}

& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt") -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com
& $Python -m pip install pyinstaller==6.11.1 -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com

if (Test-Path $ReleaseRoot) {
    Remove-Item $ReleaseRoot -Recurse -Force
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name $AppName `
    --add-data "static;static" `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.loops.auto" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.protocols.websockets.auto" `
    --hidden-import "multipart" `
    --hidden-import "app" `
    (Join-Path $ProjectRoot "distribute\packaged_server.py")

New-Item -ItemType Directory -Force $ReleaseRoot | Out-Null
Copy-Item (Join-Path $ProjectRoot "dist\$AppName") $AppDir -Recurse -Force
Copy-Item (Join-Path $ProjectRoot "config.example.json") (Join-Path $AppDir "config.json")
Copy-Item (Join-Path $ProjectRoot "distribute\start.bat") (Join-Path $AppDir "start.bat")
Copy-Item (Join-Path $ProjectRoot "distribute\README-DISTRIBUTION.md") (Join-Path $AppDir "README.md")

Write-Host "Build complete:" -ForegroundColor Green
Write-Host $AppDir
