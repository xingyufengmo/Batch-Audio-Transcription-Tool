# 批量音频转写工具 / Batch Audio Transcription Tool

一个本地 Web 工具：支持电脑麦克风录音、上传单个音频、选择多个文件、选择目录批量上传；可调用阿里云 Fun-ASR 或 Cloudflare Whisper Large V3 Turbo 转写音频，并用机器翻译输出中文或英文结果。

## 配置

复制模板文件：

```powershell
Copy-Item config.example.json config.json
```

然后把 `config.json` 里的占位值改成你的真实配置。`config.json` 已加入 `.gitignore`，不会被提交。

```json
{
  "dashscope": {
    "api_key": "sk-xxx",
    "workspace_id": "your-workspace-id"
  },
  "oss": {
    "bucket": "your-oss-bucket",
    "endpoint": "https://oss-cn-beijing.aliyuncs.com",
    "access_key_id": "your-oss-access-key-id",
    "access_key_secret": "your-oss-access-key-secret"
  },
  "translation": {
    "access_key_id": "your-translation-access-key-id",
    "access_key_secret": "your-translation-access-key-secret"
  }
}
```

`dashscope.workspace_id` 可以填纯业务空间 ID，例如：

```text
ws-f9y7auqaa5wrxq73
```

也可以填百炼控制台给出的完整 API Host，例如：

```text
ws-f9y7auqaa5wrxq73.cn-beijing.maas.aliyuncs.com
```

如果 OSS 和机器翻译使用同一组 AccessKey，可以把 `oss.access_key_id` / `oss.access_key_secret` 留空，程序会尝试复用 `translation.access_key_id` / `translation.access_key_secret`。

运行参数不放在 `config.json` 里，先使用代码默认值。需要临时调整时再用环境变量覆盖，例如：

```powershell
$env:APP_CONFIG_FILE="C:\path\to\config.json"
$env:DASHSCOPE_REGION="cn-beijing"
$env:DASHSCOPE_ASR_MODEL="fun-asr"
$env:DASHSCOPE_LANGUAGE_HINTS="zh,en"
$env:ASR_CONCURRENCY="2"
$env:OSS_PREFIX="asr-uploads"
$env:OSS_SIGNED_URL_EXPIRES="86400"
$env:ALIMT_ENDPOINT="mt.cn-hangzhou.aliyuncs.com"
```

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

## 打包分发

开发电脑上运行：

```powershell
.\distribute\build.ps1
```

生成的压缩包在：

```text
release\AliyunASRTranslator.zip
```

对方解压后填写 `config.json`，双击 `start.bat` 即可，不需要安装 Python。

## Cloudflare Whisper

页面里可以选择 `引擎：Cloudflare Whisper`。需要在 `config.json` 里增加：

```json
"cloudflare": {
  "account_id": "your-cloudflare-account-id",
  "api_token": "your-cloudflare-workers-ai-api-token"
}
```

Cloudflare 请求器调用 Workers AI 模型 `@cf/openai/whisper-large-v3-turbo`，音频会直接以 base64 发给 Cloudflare，不经过 OSS。
