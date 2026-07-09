@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist config.json (
  echo config.json not found. Please fill config.json first.
  pause
  exit /b 1
)
start "" "%~dp0AliyunASRTranslator.exe"
