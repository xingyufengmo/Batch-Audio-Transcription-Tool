@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist config.json (
  echo 未找到 config.json，请先填写配置文件。
  pause
  exit /b 1
)
start "" "%~dp0AliyunASRTranslator.exe"
