"use strict";

const elements = {
  shell: document.getElementById("appShell"),
  clock: document.getElementById("systemClock"),
  build: document.getElementById("buildVersion"),
  modeButtons: [...document.querySelectorAll("[data-mode]")],
  shutdown: document.getElementById("shutdownButton"),
  safety: document.getElementById("safetyState"),
  safetyTitle: document.getElementById("safetyTitle"),
  safetyDescription: document.getElementById("safetyDescription"),
  robotEndpoint: document.getElementById("robotEndpoint"),
  wakeWord: document.getElementById("wakeWord"),
  whisperModel: document.getElementById("whisperModel"),
  ollamaModel: document.getElementById("ollamaModel"),
  channel: document.getElementById("channelState"),
  processStages: [...document.querySelectorAll("#processTrack [data-stage]")],
  voiceCode: document.getElementById("voiceCode"),
  voiceText: document.getElementById("voiceStateText"),
  transcript: document.getElementById("liveTranscript"),
  talk: document.getElementById("talkButton"),
  talkIcon: document.getElementById("talkIcon"),
  talkLabel: document.getElementById("talkLabel"),
  confidence: document.getElementById("confidenceValue"),
  snr: document.getElementById("snrValue"),
  duration: document.getElementById("durationValue"),
  levelValue: document.getElementById("levelValue"),
  levelBar: document.getElementById("levelBar"),
  autoListen: document.getElementById("autoListenToggle"),
  soundLabel: document.getElementById("soundLabel"),
  sound: document.getElementById("soundButton"),
  reconnect: document.getElementById("reconnectButton"),
  operation: document.querySelector(".current-operation"),
  operationCode: document.getElementById("operationCode"),
  currentAction: document.getElementById("currentAction"),
  commandGate: document.getElementById("commandGate"),
  commandGateText: document.getElementById("commandGateText"),
  commandName: document.getElementById("commandName"),
  commandRegister: document.getElementById("commandRegister"),
  commandValue: document.getElementById("commandValue"),
  commandGateVerdict: document.getElementById("commandGateVerdict"),
  messageList: document.getElementById("messageList"),
  messageCount: document.getElementById("messageCount"),
  footerStatus: document.getElementById("footerStatus"),
  toast: document.getElementById("toast"),
  wave: document.getElementById("waveCanvas"),
  inspectorTabs: [...document.querySelectorAll("[data-panel-tab]")],
  inspectorPanels: [...document.querySelectorAll("[data-panel]")],
  systemAlertCount: document.getElementById("systemAlertCount"),
  clearMessages: document.getElementById("clearMessagesButton"),
  commandButtons: [...document.querySelectorAll("[data-command-value]")],
};

const stateLabels = {
  initializing: ["SYS / 00", "INITIALIZING"],
  paused: ["CH / 01", "PAUSED"],
  waiting: ["CH / 02", "STANDBY"],
  listening: ["CH / 03", "LISTENING"],
  hearing: ["CH / 04", "VOICE DETECTED"],
  recording: ["REC / 03", "RECORDING"],
  stopping: ["REC / 04", "CLOSING"],
  recognizing: ["ASR / 05", "RECOGNIZING"],
  thinking: ["LLM / 06", "PROCESSING"],
  speaking: ["TTS / 07", "SPEAKING"],
  error: ["ERR / 99", "FAULT"],
};

const activeVoiceStates = new Set([
  "listening",
  "hearing",
  "recording",
  "stopping",
  "processing",
  "speaking",
]);

let currentState = null;
let renderedMessageIds = new Set();
let toastTimer = null;
let pollTimer = null;
let displayLevel = 0;
let targetLevel = 0;
let wavePhase = 0;
let commandRequestPending = false;

function setClock() {
  elements.clock.textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("is-visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("is-visible"), 2800);
}

async function postAction(payload) {
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "操作失败");
  }
  render(data.state);
}

function levelFor(status) {
  if (!status) return "pending";
  return ["ok", "error", "active"].includes(status.level) ? status.level : "pending";
}

function renderStatuses(statuses) {
  let alertCount = 0;
  document.querySelectorAll("[data-status-key]").forEach((row) => {
    const status = statuses[row.dataset.statusKey];
    if (!status) return;
    row.dataset.level = levelFor(status);
    row.querySelector("strong").textContent = status.value || "未知";
    if (status.level === "error") alertCount += 1;
  });
  elements.systemAlertCount.textContent = alertCount ? String(alertCount) : "";

  const robot = statuses.robot || { level: "pending", value: "正在检查" };
  const robotLevel = levelFor(robot);
  elements.safety.dataset.level = robotLevel;
  if (robotLevel === "ok") {
    elements.safetyTitle.textContent = "控制链路已联锁";
    elements.safetyDescription.textContent = "机器人 Modbus 已响应，运动指令允许按单次写入策略下发。";
  } else if (robotLevel === "error") {
    elements.safetyTitle.textContent = "运动指令已锁止";
    elements.safetyDescription.textContent = "机器人 Modbus 未响应，程序不会下发运动指令。";
  } else {
    elements.safetyTitle.textContent = "联锁检查中";
    elements.safetyDescription.textContent = "机器人 Modbus 未确认前，运动指令不会下发。";
  }
}

function setInspectorPanel(name) {
  elements.inspectorTabs.forEach((tab) => {
    const active = tab.dataset.panelTab === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  elements.inspectorPanels.forEach((panel) => {
    const active = panel.dataset.panel === name;
    panel.classList.toggle("is-active", active);
    panel.hidden = !active;
  });
}

function renderMessages(messages) {
  const incomingIds = new Set(messages.map((message) => message.id));
  if ([...renderedMessageIds].some((id) => !incomingIds.has(id))) {
    elements.messageList.replaceChildren();
    renderedMessageIds = new Set();
  }

  for (const message of messages) {
    if (renderedMessageIds.has(message.id)) continue;
    const article = document.createElement("article");
    article.className = "message";
    article.dataset.role = message.role;

    const role = document.createElement("span");
    role.className = "message-role";
    role.textContent = message.role === "user" ? "YOU" : "SYS";

    const body = document.createElement("div");
    const text = document.createElement("p");
    text.textContent = message.text;
    const time = document.createElement("time");
    time.dateTime = new Date(message.timestamp * 1000).toISOString();
    time.textContent = new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(message.timestamp * 1000));
    body.append(text, time);
    article.append(role, body);
    elements.messageList.append(article);
    renderedMessageIds.add(message.id);
  }
  elements.messageCount.textContent = String(messages.length).padStart(2, "0");
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
}

function renderCommandButtons(state) {
  const voiceBusy = ["listening", "hearing", "recording", "recognizing", "thinking", "speaking", "stopping"].includes(state.voice_state);
  const robotReady = state.statuses?.robot?.level === "ok";
  const disabled = commandRequestPending
    || !state.ready
    || state.mode !== "robot"
    || voiceBusy
    || state.current_action_level === "active"
    || !robotReady
    || state.voice_state === "error";
  elements.commandButtons.forEach((button) => {
    button.disabled = disabled;
    button.dataset.sending = String(commandRequestPending && button.dataset.commandValue === commandRequestPending);
  });
}

function render(state) {
  currentState = state;
  elements.shell.dataset.voiceState = state.voice_state;
  elements.build.textContent = state.version;
  elements.robotEndpoint.textContent = state.robot_endpoint;
  elements.wakeWord.textContent = state.wake_word;
  elements.whisperModel.textContent = `WHISPER ${String(state.whisper_model).toUpperCase()}`;
  elements.ollamaModel.textContent = String(state.ollama_model).toUpperCase();

  const [code, channel] = stateLabels[state.voice_state] || stateLabels.waiting;
  elements.voiceCode.textContent = code;
  elements.channel.querySelector("strong").textContent = channel;
  elements.channel.dataset.level = state.voice_state === "error"
    ? "error"
    : !state.ready || state.voice_state === "paused"
      ? "pending"
      : activeVoiceStates.has(state.voice_state)
        ? "active"
        : "ok";
  elements.voiceText.textContent = state.voice_text || state.voice_state;
  elements.transcript.textContent = state.transcript || "等待语音输入";
  elements.soundLabel.textContent = state.sound_label || "中文语音待机";
  const manualRecording = Boolean(state.manual_recording);
  const voiceBusy = ["recognizing", "thinking", "speaking", "stopping"].includes(state.voice_state);
  const robotBusy = state.current_action_level === "active";
  const robotReconnecting = state.statuses?.robot?.level === "active";
  elements.talk.disabled = !state.ready || voiceBusy || robotBusy || state.voice_state === "error";
  elements.talk.dataset.recording = String(manualRecording);
  elements.talk.setAttribute(
    "aria-label",
    manualRecording ? "结束录音并开始识别" : "点击开始说话",
  );
  elements.talkIcon.src = manualRecording ? "/assets/icons/circle-stop.svg" : "/assets/icons/mic.svg";
  elements.talkLabel.textContent = state.voice_state === "stopping"
    ? "正在结束"
    : manualRecording
      ? "结束并识别"
      : voiceBusy
        ? "正在处理"
        : "点击说话";
  elements.autoListen.disabled = !state.ready || manualRecording || voiceBusy || state.voice_state === "error";
  elements.sound.disabled = !state.ready || manualRecording || voiceBusy || robotBusy || state.voice_state === "error";
  elements.reconnect.disabled = !state.ready || manualRecording || voiceBusy || robotBusy || robotReconnecting;
  elements.autoListen.checked = Boolean(state.auto_listen);
  targetLevel = Number(state.audio_level) || 0;
  const confidence = state.recognition_confidence;
  elements.confidence.textContent = confidence == null ? "--" : `${Math.round(confidence * 100)}%`;
  elements.snr.textContent = state.snr_db == null ? "-- dB" : `${Number(state.snr_db).toFixed(1)} dB`;
  elements.duration.textContent = `${Number(state.recording_duration || 0).toFixed(1)} s`;

  const stageForState = ["recording", "listening", "hearing", "stopping"].includes(state.voice_state)
    ? "capture"
    : ["recognizing", "thinking"].includes(state.voice_state)
      ? "recognize"
      : state.command_gate_level && state.command_gate_level !== "idle"
        ? "interlock"
        : "";
  elements.processStages.forEach((stage) => {
    stage.classList.toggle("is-active", stage.dataset.stage === stageForState);
  });

  const gateLevel = state.command_gate_level || "idle";
  elements.commandGate.dataset.level = gateLevel;
  elements.commandGateText.textContent = state.command_gate || "等待语音指令";
  elements.commandName.textContent = state.command_name || "尚未解析";
  elements.commandRegister.textContent = state.command_register || "--";
  elements.commandValue.textContent = state.command_value || "--";
  elements.commandGateVerdict.textContent = {
    active: "RELEASED",
    checking: "CHECKING",
    capturing: "CAPTURE",
    warning: "REPEAT",
    locked: "LOCKED",
  }[gateLevel] || "LOCKED";

  elements.modeButtons.forEach((button) => {
    button.disabled = !state.ready || manualRecording || voiceBusy || robotBusy;
    button.classList.toggle("is-active", button.dataset.mode === state.mode);
    button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
  });

  elements.operation.dataset.level = state.current_action_level || "idle";
  elements.currentAction.textContent = state.current_action || "等待机器人指令";
  elements.operationCode.textContent = state.current_action_level === "active" ? "RUN" : state.current_action_level === "error" ? "FAULT" : "IDLE";

  renderStatuses(state.statuses || {});
  renderMessages(state.messages || []);
  renderCommandButtons(state);
  elements.footerStatus.textContent = state.ready ? "本地语音服务已就绪" : state.last_error || "本地服务连接中";
}

async function pollState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "本地后端无响应");
    render(data.state);
    pollTimer = window.setTimeout(pollState, 260);
  } catch (error) {
    elements.footerStatus.textContent = "本地后端连接中断";
    elements.safety.dataset.level = "error";
    elements.safetyTitle.textContent = "控制链路已锁止";
    pollTimer = window.setTimeout(pollState, 1200);
  }
}

function drawWave() {
  const canvas = elements.wave;
  const bounds = canvas.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(bounds.width * ratio));
  const height = Math.max(1, Math.round(bounds.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  displayLevel += (targetLevel - displayLevel) * 0.18;
  targetLevel *= 0.94;
  wavePhase += 0.055;

  const voiceState = currentState ? currentState.voice_state : "initializing";
  const active = ["listening", "hearing", "recognizing", "thinking", "speaking"].includes(voiceState);
  const base = active ? 0.12 : 0.035;
  const amplitude = Math.min(0.44, base + displayLevel * 0.42) * height;
  const center = height / 2;
  const points = 120;

  ctx.lineWidth = Math.max(1.25, ratio);
  ctx.strokeStyle = voiceState === "speaking" ? "#ff5c20" : voiceState === "error" ? "#d43d2f" : "#00bf84";
  ctx.shadowColor = ctx.strokeStyle;
  ctx.shadowBlur = 12 * ratio;
  ctx.beginPath();
  for (let index = 0; index <= points; index += 1) {
    const x = (index / points) * width;
    const envelope = Math.sin((index / points) * Math.PI);
    const carrier = Math.sin(index * 0.42 + wavePhase * 12) * 0.56 + Math.sin(index * 0.17 - wavePhase * 7) * 0.29 + Math.sin(index * 0.79 + wavePhase * 4) * 0.15;
    const y = center + carrier * amplitude * envelope;
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.shadowBlur = 0;

  const levelPercent = Math.round(displayLevel * 100);
  elements.levelValue.textContent = String(levelPercent).padStart(2, "0");
  elements.levelBar.style.width = `${levelPercent}%`;
  elements.levelBar.dataset.hot = String(levelPercent > 82);
  elements.talk.style.setProperty("--level", displayLevel.toFixed(3));
  window.requestAnimationFrame(drawWave);
}

elements.modeButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await postAction({ action: "set_mode", mode: button.dataset.mode });
    } catch (error) {
      showToast(error.message);
    }
  });
});

elements.inspectorTabs.forEach((button) => {
  button.addEventListener("click", () => setInspectorPanel(button.dataset.panelTab));
});

elements.talk.addEventListener("click", async () => {
  try {
    await postAction({ action: "manual_listen" });
  } catch (error) {
    showToast(error.message);
  }
});

elements.autoListen.addEventListener("change", async () => {
  try {
    await postAction({ action: "set_auto_listen", enabled: elements.autoListen.checked });
  } catch (error) {
    elements.autoListen.checked = !elements.autoListen.checked;
    showToast(error.message);
  }
});

elements.sound.addEventListener("click", async () => {
  try {
    await postAction({ action: "test_sound" });
  } catch (error) {
    showToast(error.message);
  }
});

elements.reconnect.addEventListener("click", async () => {
  try {
    await postAction({ action: "reconnect_robot" });
    showToast("正在重新检查机器人 Modbus 链路");
  } catch (error) {
    showToast(error.message);
  }
});

elements.clearMessages.addEventListener("click", async () => {
  try {
    await postAction({ action: "clear_messages" });
    showToast("会话记录已清空");
  } catch (error) {
    showToast(error.message);
  }
});

elements.commandButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    if (commandRequestPending) return;
    const value = Number(button.dataset.commandValue);
    const name = button.dataset.commandName;
    commandRequestPending = String(value);
    if (currentState) renderCommandButtons(currentState);
    try {
      await postAction({ action: "send_robot_command", value });
      showToast(`${name}指令已下发`);
    } catch (error) {
      showToast(error.message);
    } finally {
      commandRequestPending = false;
      if (currentState) renderCommandButtons(currentState);
    }
  });
});

elements.shutdown.addEventListener("click", async () => {
  if (!window.confirm("确定要安全关闭语音控制台吗？")) return;
  window.clearTimeout(pollTimer);
  try {
    await postAction({ action: "shutdown" });
    elements.footerStatus.textContent = "程序正在安全关闭";
    window.setTimeout(() => window.close(), 450);
  } catch (error) {
    showToast(error.message);
  }
});

setClock();
window.setInterval(setClock, 1000);
pollState();
drawWave();
