const state = {
  files: [],
  mediaRecorder: null,
  recordedChunks: [],
  jobId: null,
  pollTimer: null,
};

const els = {
  recordButton: document.querySelector("#recordButton"),
  stopButton: document.querySelector("#stopButton"),
  recordState: document.querySelector("#recordState"),
  singleInput: document.querySelector("#singleInput"),
  multiInput: document.querySelector("#multiInput"),
  directoryInput: document.querySelector("#directoryInput"),
  clearButton: document.querySelector("#clearButton"),
  transcribeButton: document.querySelector("#transcribeButton"),
  asrProvider: document.querySelector("#asrProvider"),
  recognitionLanguage: document.querySelector("#recognitionLanguage"),
  translateButton: document.querySelector("#translateButton"),
  targetLanguage: document.querySelector("#targetLanguage"),
  fileCount: document.querySelector("#fileCount"),
  jobState: document.querySelector("#jobState"),
  progressText: document.querySelector("#progressText"),
  resultBody: document.querySelector("#resultBody"),
};

els.singleInput.addEventListener("change", (event) => addFiles(event.target.files));
els.multiInput.addEventListener("change", (event) => addFiles(event.target.files));
els.directoryInput.addEventListener("change", (event) => addFiles(event.target.files));
els.clearButton.addEventListener("click", resetAll);
els.transcribeButton.addEventListener("click", startTranscription);
els.translateButton.addEventListener("click", translateJob);
els.recordButton.addEventListener("click", startRecording);
els.stopButton.addEventListener("click", stopRecording);

function addFiles(fileList) {
  const incoming = Array.from(fileList || []);
  if (!incoming.length) return;

  const known = new Set(state.files.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
  for (const file of incoming) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (!known.has(key)) {
      state.files.push(file);
      known.add(key);
    }
  }
  renderSelectedFiles();
}

function renderSelectedFiles() {
  els.fileCount.textContent = String(state.files.length);
  els.transcribeButton.disabled = state.files.length === 0;
  els.jobState.textContent = state.files.length ? "已选择" : "待选择";
  els.progressText.textContent = state.files.length ? "等待开始识别" : "";

  if (!state.files.length) {
    renderEmpty("请选择音频文件或直接录音。");
    return;
  }

  els.resultBody.innerHTML = "";
  for (const file of state.files) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><div class="file-name"></div><div class="row-status">待识别</div></td>
      <td></td>
      <td></td>
    `;
    row.querySelector(".file-name").textContent = file.webkitRelativePath || file.name;
    els.resultBody.appendChild(row);
  }
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.recordedChunks = [];
    state.mediaRecorder = new MediaRecorder(stream, { mimeType: pickMimeType() });
    state.mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) state.recordedChunks.push(event.data);
    };
    state.mediaRecorder.onstop = () => {
      const blob = new Blob(state.recordedChunks, { type: state.mediaRecorder.mimeType });
      const extension = state.mediaRecorder.mimeType.includes("mp4") ? "m4a" : "webm";
      const file = new File([blob], `microphone-${timestamp()}.${extension}`, {
        type: blob.type,
        lastModified: Date.now(),
      });
      addFiles([file]);
      stream.getTracks().forEach((track) => track.stop());
      els.recordState.textContent = "录音已加入";
    };
    state.mediaRecorder.start();
    els.recordButton.disabled = true;
    els.stopButton.disabled = false;
    els.recordState.textContent = "录音中";
  } catch (error) {
    els.recordState.textContent = "无法录音";
    alert(`无法访问麦克风：${error.message}`);
  }
}

function stopRecording() {
  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop();
  }
  els.recordButton.disabled = false;
  els.stopButton.disabled = true;
}

function pickMimeType() {
  const candidates = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

async function startTranscription() {
  if (!state.files.length) return;
  els.transcribeButton.disabled = true;
  els.translateButton.disabled = true;
  els.jobState.textContent = "上传中";
  els.progressText.textContent = "";

  const form = new FormData();
  form.append("asr_provider", els.asrProvider.value);
  form.append("language_hints", els.recognitionLanguage.value);
  state.files.forEach((file) => form.append("files", file, file.webkitRelativePath || file.name));

  try {
    const response = await fetch("/api/jobs", { method: "POST", body: form });
    const data = await readJson(response);
    state.jobId = data.id;
    renderJob(data);
    pollJob();
  } catch (error) {
    els.jobState.textContent = "失败";
    els.progressText.textContent = error.message;
    els.transcribeButton.disabled = false;
  }
}

function pollJob() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    try {
      const response = await fetch(`/api/jobs/${state.jobId}`);
      const data = await readJson(response);
      renderJob(data);
      if (["completed", "failed"].includes(data.status)) {
        clearInterval(state.pollTimer);
        els.transcribeButton.disabled = false;
        els.translateButton.disabled = data.rows.every((row) => !row.text);
      }
    } catch (error) {
      clearInterval(state.pollTimer);
      els.jobState.textContent = "轮询失败";
      els.progressText.textContent = error.message;
      els.transcribeButton.disabled = false;
    }
  }, 1500);
}

async function translateJob() {
  if (!state.jobId) return;
  els.translateButton.disabled = true;
  els.jobState.textContent = "翻译中";
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_language: els.targetLanguage.value }),
    });
    const data = await readJson(response);
    renderJob(data);
  } catch (error) {
    els.progressText.textContent = error.message;
  } finally {
    els.translateButton.disabled = false;
  }
}

function renderJob(job) {
  els.jobState.textContent = labelJobStatus(job.status);
  els.progressText.textContent = `${job.completed}/${job.total} · 引擎：${labelAsrProvider(job.asr_provider)} · 识别语言：${labelRecognitionLanguage(job.language_hints)} (${labelRecognitionCode(job.language_hints)})`;
  els.fileCount.textContent = String(job.total);
  els.resultBody.innerHTML = "";

  for (const item of job.rows) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>
        <div class="file-name"></div>
        <div class="row-status"></div>
      </td>
      <td class="text-cell"></td>
      <td class="translated-cell"></td>
    `;
    row.querySelector(".file-name").textContent = item.file_name;
    const status = row.querySelector(".row-status");
    status.textContent = item.error ? `${labelRowStatus(item.status)}：${item.error}` : labelRowStatus(item.status);
    if (item.error) status.classList.add("error");
    row.querySelector(".text-cell").textContent = item.text || "";
    row.querySelector(".translated-cell").textContent = item.translated_text || "";
    els.resultBody.appendChild(row);
  }
}

function resetAll() {
  clearInterval(state.pollTimer);
  state.files = [];
  state.jobId = null;
  els.singleInput.value = "";
  els.multiInput.value = "";
  els.directoryInput.value = "";
  els.translateButton.disabled = true;
  renderSelectedFiles();
}

function renderEmpty(message) {
  els.resultBody.innerHTML = `<tr class="empty"><td colspan="3"></td></tr>`;
  els.resultBody.querySelector("td").textContent = message;
}

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
}

function labelJobStatus(status) {
  return {
    queued: "排队中",
    running: "识别中",
    completed: "已完成",
    failed: "失败",
  }[status] || status;
}

function labelRowStatus(status) {
  return {
    queued: "排队中",
    uploading: "上传 OSS",
    recognizing: "识别中",
    completed: "完成",
    failed: "失败",
  }[status] || status;
}

function labelAsrProvider(provider) {
  return provider === "cloudflare" ? "Cloudflare Whisper" : "阿里 Fun-ASR";
}

function labelRecognitionLanguage(languageHints) {
  const value = Array.isArray(languageHints) ? languageHints.join(",") : String(languageHints || "");
  return {
    "zh,en": "中文/英语",
    zh: "中文",
    en: "英语",
    ja: "日语",
    ko: "韩语",
    vi: "越南语",
    th: "泰语",
    id: "印尼语",
    ms: "马来语",
    tl: "菲律宾语",
    hi: "印地语",
    ar: "阿拉伯语",
    fr: "法语",
    de: "德语",
    es: "西班牙语",
    pt: "葡萄牙语",
    ru: "俄语",
    it: "意大利语",
    nl: "荷兰语",
    sv: "瑞典语",
    da: "丹麦语",
    fi: "芬兰语",
    no: "挪威语",
    el: "希腊语",
    pl: "波兰语",
    cs: "捷克语",
    hu: "匈牙利语",
    ro: "罗马尼亚语",
    bg: "保加利亚语",
    hr: "克罗地亚语",
    sk: "斯洛伐克语",
  }[value] || value || "默认";
}

function labelRecognitionCode(languageHints) {
  return Array.isArray(languageHints) ? languageHints.join(",") : String(languageHints || "default");
}

function timestamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}
