# 批量音频转写工具 / Batch Audio Transcription Tool

一个本地 Web 工具：支持电脑麦克风录音、上传单个音频、选择多个文件、选择目录批量上传；可调用阿里云 Fun-ASR 或 Cloudflare Whisper Large V3 Turbo 转写音频，并用机器翻译输出中文或英文结果。

A local web tool that supports recording, single-file upload, multi-file upload, and batch directory upload. It transcribes audio with Aliyun Fun-ASR or Cloudflare Whisper Large V3 Turbo, then uses machine translation to output Chinese or English results.


## 启动

Windows 直接双击：

```text
start.bat
```

或在 PowerShell 里运行：

```powershell
.\start.ps1
```

或手动启动：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000
```

## 使用

1. 用电脑麦克风录音，或选择单个音频、多个音频、目录。
2. 选择识别语言。越南语音频请选择“识别：越南语”。
3. 点击“开始识别”。
4. 表格会逐行显示文件名和输出文本。
5. 识别完成后选择“翻译成中文”或“翻译成英文”，点击“翻译”。

批量 200 个文件时，默认并发是 `2`。确认接口限额和费用后，可以用环境变量 `ASR_CONCURRENCY` 调高。

