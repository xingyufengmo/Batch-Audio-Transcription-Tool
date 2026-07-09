# 架构与核心代码说明

这个项目是一个本地 Web 工具，用阿里云完成“音频转文字 + 文字翻译”。浏览器负责录音和选择文件，后端负责上传文件、调用阿里云 Fun-ASR、保存识别结果，再按需调用阿里云机器翻译。

## 整体架构

```text
浏览器页面
  ├─ 麦克风录音
  ├─ 单个音频上传
  ├─ 多文件上传
  └─ 目录批量上传
        │
        ▼
FastAPI 后端
  ├─ 保存上传文件到 data/uploads/
  ├─ 上传音频到阿里云 OSS
  ├─ 生成 OSS 临时访问 URL
  ├─ 调用 DashScope Fun-ASR 异步识别
  ├─ 下载识别 JSON 结果
  ├─ 提取 transcript text
  └─ 用户点击翻译后调用阿里机器翻译
        │
        ▼
页面表格展示
  ├─ 文件名
  ├─ 输出的文本
  └─ 翻译的文本
```

DashScope Fun-ASR 的录音文件识别接口需要可访问的 `file_url`，所以本地上传的音频不能直接传给 ASR。当前设计会先把音频上传到 OSS，再把签名 URL 提交给 Fun-ASR。

## 主要文件

- `app.py`：后端主程序，包含上传、任务管理、OSS、ASR、翻译逻辑。
- `static/index.html`：页面结构，包含录音、上传、识别、翻译按钮和结果表格。
- `static/app.js`：前端交互逻辑，负责录音、提交文件、轮询任务、触发翻译。
- `static/styles.css`：页面样式。
- `requirements.txt`：Python 依赖。
- `start.ps1`：Windows 启动脚本。

## 后端核心流程

### 1. 创建识别任务

接口：

```text
POST /api/jobs
```

核心函数：

```python
async def create_job(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...))
```

这个接口接收前端传来的一个或多个音频文件，然后：

1. 创建一个 `job_id`。
2. 把文件保存到 `data/uploads/{job_id}/`。
3. 为每个文件创建一行 `Row`，包含文件名、本地路径、状态等。
4. 把任务保存到内存字典 `jobs`。
5. 通过 `background_tasks.add_task(process_job, job_id)` 异步开始识别。
6. 立即返回任务状态给前端。

这样做的原因是：批量 200 个音频识别可能很久，不能让浏览器一直卡在上传请求里。

### 2. 批量处理任务

核心函数：

```python
def process_job(job_id: str) -> None
```

它会用线程池并发处理文件：

```python
with ThreadPoolExecutor(max_workers=ASR_CONCURRENCY) as executor:
```

并发数由环境变量控制：

```text
ASR_CONCURRENCY=2
```

建议一开始保持 `2`，确认阿里云接口限额、OSS 带宽和费用后再调高。

### 3. 单个文件识别

核心函数：

```python
def process_row(job_id: str, row_id: str) -> None
```

单个音频的处理顺序是：

```text
queued
  ▼
uploading
  ▼
recognizing
  ▼
completed / failed
```

内部主要做三件事：

```python
signed_url = upload_to_oss(row.local_path, row.file_name)
result = transcribe_url(signed_url)
text = extract_text(result)
```

含义：

- `upload_to_oss()`：把本地音频上传到 OSS，并生成临时签名 URL。
- `transcribe_url()`：调用 DashScope Fun-ASR 异步识别。
- `extract_text()`：从完整 JSON 结果里提取最终文本。

### 4. 上传到 OSS

核心函数：

```python
def upload_to_oss(local_path: str, file_name: str) -> str
```

使用这些环境变量：

```text
OSS_BUCKET
OSS_ENDPOINT
OSS_ACCESS_KEY_ID
OSS_ACCESS_KEY_SECRET
OSS_PREFIX
OSS_SIGNED_URL_EXPIRES
```

上传后返回：

```python
return bucket.sign_url("GET", object_key, OSS_EXPIRES_SECONDS, slash_safe=True)
```

这个签名 URL 会交给 Fun-ASR。默认有效期是 86400 秒，也就是 24 小时。

### 5. 调用 Fun-ASR

核心函数：

```python
def transcribe_url(file_url: str) -> dict[str, Any]
```

关键调用：

```python
task_response = Transcription.async_call(
    model=ASR_MODEL,
    file_urls=[file_url],
    language_hints=ASR_LANGUAGE_HINTS or None,
)
```

然后等待任务完成：

```python
transcription_response = Transcription.wait(task=task_response.output.task_id)
```

成功后，阿里云返回的不是完整识别内容，而是一个 `transcription_url`。后端会再请求这个 URL，拿到完整 JSON：

```python
result = json.loads(request.urlopen(url, timeout=60).read().decode("utf8"))
```

### 6. 提取识别文本

核心函数：

```python
def extract_text(result: dict[str, Any]) -> str
```

阿里返回的完整 JSON 里，文本在：

```text
transcripts[].text
```

所以当前代码会遍历所有 `transcripts`，把每段 `text` 合并成最终输出文本。

## 翻译流程

翻译不是自动执行，而是识别完成后由用户点击按钮触发。

接口：

```text
POST /api/jobs/{job_id}/translate
```

请求体：

```json
{
  "target_language": "zh"
}
```

或者：

```json
{
  "target_language": "en"
}
```

核心函数：

```python
def translate_job(job_id: str, body: TranslateRequest)
```

它会找到当前任务里已经识别出文本的行，然后逐行调用：

```python
row.translated_text = translate_text(client, row.text, body.target_language)
```

机器翻译的核心请求：

```python
request_model = alimt_models.TranslateGeneralRequest(
    format_type="text",
    source_language="auto",
    target_language=target_language,
    source_text=chunk,
    scene="general",
)
```

因为阿里机器翻译单次文本长度有限，代码里用 `split_text(text, 4500)` 把长文本拆成多个片段再翻译，最后合并。

## 前端核心逻辑

### 文件输入

页面支持三种文件选择：

```html
<input id="singleInput" type="file" accept="audio/*,video/*" />
<input id="multiInput" type="file" accept="audio/*,video/*" multiple />
<input id="directoryInput" type="file" accept="audio/*,video/*" multiple webkitdirectory />
```

说明：

- `singleInput`：选择一个音频。
- `multiInput`：选择多个音频。
- `directoryInput`：选择目录，浏览器会把目录里的文件一起传上来。

### 麦克风录音

核心函数：

```javascript
async function startRecording()
function stopRecording()
```

浏览器用：

```javascript
navigator.mediaDevices.getUserMedia({ audio: true })
```

获取麦克风权限，再用：

```javascript
MediaRecorder
```

录成 `webm` 或 `m4a` 文件，然后加入待识别文件列表。

### 开始识别

核心函数：

```javascript
async function startTranscription()
```

它会把所有文件放进 `FormData`：

```javascript
state.files.forEach((file) => form.append("files", file, file.webkitRelativePath || file.name));
```

然后提交到：

```text
POST /api/jobs
```

### 轮询任务状态

核心函数：

```javascript
function pollJob()
```

每 1.5 秒请求一次：

```text
GET /api/jobs/{job_id}
```

后端返回每个文件的状态、识别文本、翻译文本，前端用 `renderJob()` 刷新表格。

### 点击翻译

核心函数：

```javascript
async function translateJob()
```

它会读取下拉框目标语言：

```javascript
els.targetLanguage.value
```

然后请求：

```text
POST /api/jobs/{job_id}/translate
```

## 当前状态存储

当前版本为了快速可用，任务状态保存在后端内存里：

```python
jobs: dict[str, "Job"] = {}
```

这意味着：

- 服务重启后，页面上的历史任务会丢失。
- 适合本地工具、个人使用、原型阶段。
- 如果要多人使用或长期保存结果，下一步应接入 SQLite、PostgreSQL 或 MySQL。

## 配置文件

项目优先从根目录的 `config.json` 读取 API 和密钥类配置。真实 `config.json` 已加入 `.gitignore`，仓库里只保留 `config.example.json` 模板。

`config.json` 只存储连接云服务必需的信息，不存储运行参数，例如 region、模型名、语言提示、并发数、OSS 前缀、签名有效期。

配置结构：

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

环境变量仍然可用，并且会覆盖 `config.json` 中同名密钥。运行参数只通过环境变量或代码默认值控制。可以用 `APP_CONFIG_FILE` 指定另一个 JSON 配置文件路径。

`dashscope.workspace_id` 支持两种写法：纯业务空间 ID，例如 `ws-xxx`；或完整 API Host，例如 `ws-xxx.cn-beijing.maas.aliyuncs.com`。后端会自动规范化成 DashScope SDK 需要的 `/api/v1` 地址。

## 后续可以增强的点

1. 增加数据库，保存历史任务和结果。
2. 增加导出 Excel / CSV。
3. 增加单行重新识别、单行重新翻译。
4. 增加批量删除 OSS 临时文件。
5. 增加登录和权限，避免多人混用同一套 Key。
6. 增加任务队列，比如 Celery / RQ，让 200 个以上文件更稳定。
7. 保存完整识别 JSON，后续可以展示时间戳、句子级文本、字幕文件。
