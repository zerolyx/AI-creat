"use strict";

const elements = {
  shell: document.getElementById("engineerShell"),
  healthRail: document.getElementById("healthRail"),
  healthTitle: document.getElementById("healthTitle"),
  healthDescription: document.getElementById("healthDescription"),
  lastRefresh: document.getElementById("lastRefresh"),
  uptime: document.getElementById("uptimeValue"),
  version: document.getElementById("versionValue"),
  serviceSummary: document.getElementById("serviceSummary"),
  serviceDetail: document.getElementById("serviceDetail"),
  robotSummary: document.getElementById("robotSummary"),
  robotDetail: document.getElementById("robotDetail"),
  speakerSummary: document.getElementById("speakerSummary"),
  speakerDetail: document.getElementById("speakerDetail"),
  asrSummary: document.getElementById("asrSummary"),
  asrDetail: document.getElementById("asrDetail"),
  logTabs: [...document.querySelectorAll("[data-log-view]")],
  levelFilter: document.getElementById("levelFilter"),
  search: document.getElementById("logSearch"),
  pause: document.getElementById("pauseButton"),
  refresh: document.getElementById("refreshButton"),
  clearLogs: document.getElementById("clearLogsButton"),
  clearLogsDialog: document.getElementById("clearLogsDialog"),
  streamState: document.getElementById("streamState"),
  list: document.getElementById("diagnosticList"),
  logCount: document.getElementById("logCount"),
  logSource: document.getElementById("logSource"),
  issueList: document.getElementById("issueList"),
  issueCount: document.getElementById("issueCount"),
  deviceList: document.getElementById("deviceList"),
  mode: document.getElementById("modeValue"),
  voiceState: document.getElementById("voiceStateValue"),
  gate: document.getElementById("gateValue"),
  writeOutcome: document.getElementById("writeOutcomeValue"),
  python: document.getElementById("pythonValue"),
  pid: document.getElementById("pidValue"),
  logMeta: document.getElementById("logMeta"),
  toast: document.getElementById("toast"),
};

const levelNames = { info: "信息", success: "正常", warning: "警告", error: "错误" };
const sourceNames = {
  system: "核心服务",
  robot: "机器人",
  microphone: "麦克风",
  speaker: "语音输出",
  recognition: "语音识别",
  ollama: "本地模型",
  local_modbus: "工艺参考",
  interlock: "安全联锁",
  robot_action: "机器人动作",
  conversation: "会话",
  operator: "操作员",
  voice: "语音状态",
  file: "文件日志",
};

let diagnostics = null;
let logView = "events";
let paused = false;
let pollTimer = null;
let toastTimer = null;

async function postAction(payload) {
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || "操作失败");
  return data;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("is-visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("is-visible"), 2600);
}

function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function timeLabel(timestamp) {
  if (!timestamp) return "--:--:--";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date(timestamp * 1000));
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = text;
  return element;
}

function renderHealth(data) {
  const statuses = data.statuses || {};
  const errors = Object.values(statuses).filter((status) => status.level === "error");
  const coreFault = data.safety.tts_fault || !data.service.ready;
  const level = coreFault ? "error" : errors.length ? "warning" : "ok";
  elements.shell.dataset.health = level === "error" ? "error" : "ok";
  elements.healthRail.dataset.level = level;
  if (coreFault) {
    elements.healthTitle.textContent = "核心服务存在故障";
    elements.healthDescription.textContent = data.service.last_error || "语音或本地状态服务已锁止，请检查事件日志。";
  } else if (errors.length) {
    elements.healthTitle.textContent = "系统受限运行";
    elements.healthDescription.textContent = `${errors.length} 项设备异常；当前安全联锁仍然有效。`;
  } else {
    elements.healthTitle.textContent = "系统运行正常";
    elements.healthDescription.textContent = "未发现活动故障，安全联锁处于监控状态。";
  }
  elements.lastRefresh.textContent = timeLabel(data.generated_at);
}

function renderSummary(data) {
  const robot = data.statuses.robot || {};
  const speaker = data.statuses.speaker || {};
  elements.uptime.textContent = formatUptime(data.uptime_seconds);
  elements.version.textContent = data.version;
  elements.serviceSummary.textContent = data.service.ready ? "运行中" : "未就绪";
  elements.serviceDetail.textContent = data.service.voice_text || "--";
  elements.robotSummary.textContent = robot.level === "ok" ? "通信正常" : robot.level === "error" ? "通信锁止" : "检查中";
  elements.robotDetail.textContent = robot.value || "--";
  elements.speakerSummary.textContent = speaker.level === "ok" ? "输出正常" : speaker.level === "error" ? "输出故障" : "检查中";
  elements.speakerDetail.textContent = speaker.value || "--";
  elements.asrSummary.textContent = data.audio.confidence == null ? "暂无数据" : `${Math.round(data.audio.confidence * 100)}%`;
  elements.asrDetail.textContent = data.audio.snr_db == null ? "尚未完成识别" : `信噪比 ${Number(data.audio.snr_db).toFixed(1)} dB`;
}

function buildIssues(data) {
  const issues = [];
  const statuses = data.statuses || {};
  if (statuses.robot?.level === "error") {
    issues.push({ level: "error", title: "机器人通信未确认", detail: "检查 robot_config.local.env 中的私有地址、端口、寄存器和控制启用标志。运动指令继续锁止。" });
  }
  if (data.safety.tts_fault || statuses.speaker?.level === "error") {
    issues.push({ level: "error", title: "中文语音输出故障", detail: "检查 Windows 中文语音、默认扬声器和 Realtek 输出路由；故障解除前不要恢复语音控制。" });
  }
  if (data.safety.local_state_fault || statuses.local_modbus?.level === "error") {
    issues.push({ level: "warning", title: "工艺参考状态异常", detail: "本地工艺步骤记录不可用，但不影响固定指令的单次下发。请结合现场实际装配状态操作。" });
  }
  if (statuses.microphone?.level === "error") {
    issues.push({ level: "error", title: "麦克风输入不可用", detail: "检查 Windows 麦克风权限、默认输入设备和采样率配置。" });
  }
  if (statuses.ollama?.level === "error") {
    issues.push({ level: "warning", title: "本地大模型不可用", detail: "检查 Ollama 服务与 qwen2.5:7b；机器人固定指令仍必须经过本地安全解析。" });
  }
  for (const item of data.deployment?.results || []) {
    if (item.status === "PASS") continue;
    if (item.name === "机器人 Modbus" && statuses.robot?.level === "error") continue;
    issues.push({
      level: item.status === "FAIL" ? "error" : "warning",
      title: `部署基线：${item.name}`,
      detail: item.detail,
    });
  }
  if (!issues.length) {
    issues.push({ level: "ok", title: "未发现活动故障", detail: "继续观察实时事件和识别质量；现场投产仍需完成机器人联调验收。" });
  }
  return issues;
}

function renderIssues(data) {
  const issues = buildIssues(data);
  elements.issueList.replaceChildren();
  for (const issue of issues) {
    const item = createElement("article", "issue-item");
    item.dataset.level = issue.level;
    item.append(createElement("strong", "", issue.title), createElement("p", "", issue.detail));
    elements.issueList.append(item);
  }
  elements.issueCount.textContent = String(issues.filter((issue) => issue.level !== "ok").length);
}

function renderDevices(statuses) {
  const labels = {
    robot: "工业机器人",
    microphone: "麦克风输入",
    speaker: "中文语音输出",
    recognition: "语音识别",
    ollama: "本地大模型",
    local_modbus: "工艺顺序参考",
  };
  elements.deviceList.replaceChildren();
  for (const [key, label] of Object.entries(labels)) {
    const status = statuses[key] || { level: "pending", value: "未知" };
    const row = createElement("div", "device-row");
    row.dataset.level = status.level;
    const lamp = document.createElement("i");
    const text = document.createElement("div");
    text.append(createElement("span", "", label), createElement("strong", "", status.value || "未知"));
    row.append(lamp, text);
    elements.deviceList.append(row);
  }
}

function renderRuntime(data) {
  elements.mode.textContent = data.service.mode === "robot" ? "机器人控制" : "AI 对话";
  elements.voiceState.textContent = data.service.voice_state;
  elements.gate.textContent = data.safety.command_gate;
  elements.writeOutcome.textContent = data.safety.robot_write_outcome;
  elements.python.textContent = data.process.python;
  elements.pid.textContent = String(data.process.pid);
  if (!data.log.available) {
    elements.logMeta.textContent = "日志文件尚未生成";
    return;
  }
  const visibleKb = Math.ceil((data.log.visible_bytes || 0) / 1024);
  const historyKb = Math.ceil((data.log.history_bytes || 0) / 1024);
  elements.logMeta.textContent = `${data.log.name} · 本次 ${visibleKb} KB · 历史保留 ${historyKb} KB`;
}

function filteredEvents() {
  if (!diagnostics) return [];
  const level = elements.levelFilter.value;
  const query = elements.search.value.trim().toLocaleLowerCase("zh-CN");
  return diagnostics.events.filter((event) => {
    if (level !== "all" && event.level !== level) return false;
    if (!query) return true;
    return `${event.source} ${event.message} ${event.detail}`.toLocaleLowerCase("zh-CN").includes(query);
  });
}

function appendLogRow({ timestamp, level, source, message, detail }) {
  const row = createElement("article", "log-row");
  row.dataset.level = level;
  const time = createElement("time", "", timestamp ? timeLabel(timestamp) : "--");
  const levelTag = createElement("span", "level-tag", levelNames[level] || "信息");
  const sourceCell = createElement("span", "source", sourceNames[source] || source);
  const text = createElement("div", "event-text", message);
  if (detail) text.append(createElement("small", "", detail));
  row.append(time, levelTag, sourceCell, text);
  elements.list.append(row);
}

function renderEventLog() {
  const events = filteredEvents().slice(-300).reverse();
  elements.list.replaceChildren();
  for (const event of events) appendLogRow(event);
  if (!events.length) elements.list.append(createElement("div", "empty-log", "本次诊断会话暂无事件"));
  elements.logCount.textContent = `${events.length} 条记录`;
  elements.logSource.textContent = `诊断会话 #${diagnostics?.log?.session_id || 0} · 实时事件`;
}

async function renderFileLog() {
  const params = new URLSearchParams({ limit: "300", level: elements.levelFilter.value, query: elements.search.value.trim() });
  const response = await fetch(`/api/engineer/logs?${params}`, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || "文件日志读取失败");
  elements.list.replaceChildren();
  for (const line of [...payload.lines].reverse()) {
    appendLogRow({ timestamp: null, level: line.level, source: "file", message: line.text, detail: "" });
  }
  if (!payload.lines.length) elements.list.append(createElement("div", "empty-log", "本次诊断会话暂无文件日志"));
  elements.logCount.textContent = `${payload.lines.length} 条记录`;
  elements.logSource.textContent = `诊断会话 #${diagnostics?.log?.session_id || 0} · ${diagnostics?.log?.name || "voice_assistant.log"}`;
}

async function resetEngineerLogs(showConfirmation) {
  await postAction({ action: "reset_engineer_logs" });
  diagnostics = null;
  elements.list.replaceChildren(createElement("div", "empty-log", "本次诊断会话暂无事件"));
  elements.logCount.textContent = "0 条记录";
  elements.logSource.textContent = "正在建立新诊断会话";
  elements.search.value = "";
  elements.levelFilter.value = "all";
  if (showConfirmation) showToast("本次诊断日志已清空，历史日志已保留");
}

async function renderLog() {
  if (logView === "events") renderEventLog(); else await renderFileLog();
}

async function refreshDiagnostics(force = false) {
  if (paused && !force) return;
  try {
    const response = await fetch("/api/engineer", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "诊断后端无响应");
    diagnostics = payload.diagnostics;
    renderHealth(diagnostics);
    renderSummary(diagnostics);
    renderIssues(diagnostics);
    renderDevices(diagnostics.statuses || {});
    renderRuntime(diagnostics);
    await renderLog();
  } catch (error) {
    elements.shell.dataset.health = "error";
    elements.healthRail.dataset.level = "error";
    elements.healthTitle.textContent = "诊断连接中断";
    elements.healthDescription.textContent = error.message;
  } finally {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(refreshDiagnostics, 1000);
  }
}

elements.logTabs.forEach((button) => {
  button.addEventListener("click", async () => {
    logView = button.dataset.logView;
    elements.logTabs.forEach((tab) => {
      const active = tab === button;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    await renderLog();
  });
});

elements.levelFilter.addEventListener("change", () => {
  renderLog().catch((error) => showToast(error.message));
});
elements.search.addEventListener("input", () => {
  renderLog().catch((error) => showToast(error.message));
});
elements.pause.addEventListener("click", () => {
  paused = !paused;
  elements.pause.classList.toggle("is-active", paused);
  elements.pause.setAttribute("aria-label", paused ? "恢复日志刷新" : "暂停日志刷新");
  elements.streamState.textContent = paused ? "刷新已暂停" : "实时更新";
  showToast(paused ? "日志刷新已暂停" : "日志刷新已恢复");
  if (!paused) refreshDiagnostics(true);
});
elements.refresh.addEventListener("click", () => refreshDiagnostics(true));
elements.clearLogs.addEventListener("click", () => {
  elements.clearLogsDialog.returnValue = "cancel";
  elements.clearLogsDialog.showModal();
});
elements.clearLogsDialog.addEventListener("close", async () => {
  if (elements.clearLogsDialog.returnValue !== "confirm") return;
  try {
    await resetEngineerLogs(true);
    await refreshDiagnostics(true);
  } catch (error) {
    showToast(error.message);
  }
});

async function initializeEngineerConsole() {
  try {
    await resetEngineerLogs(false);
  } catch (error) {
    showToast(`诊断会话初始化失败：${error.message}`);
  }
  await refreshDiagnostics(true);
}

initializeEngineerConsole();
