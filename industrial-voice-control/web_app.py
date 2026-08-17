#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local web console for the industrial robot voice-control example."""

from __future__ import annotations

import copy
import importlib.util
import json
import mimetypes
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
VOICE_MODULE_PATH = BASE_DIR / "1.py"
PRODUCT_ID = "industrial-voice-console"
PRODUCT_VERSION = "2.6.0-open-source"
HTTP_HOST = "127.0.0.1"
HTTP_PORT_RANGE = range(8765, 8776)
LOG_PATH = BASE_DIR / "voice_assistant.log"
DEPLOYMENT_REPORT_PATH = BASE_DIR / "deployment_report.json"
STARTUP_ANNOUNCEMENT = "语音系统已就绪。"


def load_voice_module():
    spec = importlib.util.spec_from_file_location("industrial_voice_core", VOICE_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载语音控制核心 1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


voice = load_voice_module()


def read_deployment_report():
    if not DEPLOYMENT_REPORT_PATH.is_file() or DEPLOYMENT_REPORT_PATH.stat().st_size > 1024 * 1024:
        return {"available": False, "generated_at": None, "results": []}
    try:
        report = json.loads(DEPLOYMENT_REPORT_PATH.read_text(encoding="utf-8"))
        results = report.get("results", [])
        if not isinstance(results, list):
            results = []
        return {
            "available": True,
            "generated_at": report.get("generated_at"),
            "results": [
                {
                    "name": str(item.get("name", "检查项")),
                    "status": str(item.get("status", "WARN")),
                    "detail": str(item.get("detail", "")),
                }
                for item in results[:100]
                if isinstance(item, dict)
            ],
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return {"available": False, "generated_at": None, "results": []}


class ConsoleState:
    """Thread-safe UI state derived from the core's event stream."""

    def __init__(self):
        self._lock = threading.RLock()
        self._event_id = 0
        self._diagnostic_id = 0
        self._messages = deque(maxlen=80)
        self._diagnostics = deque(maxlen=500)
        self._state = {
            "product": PRODUCT_ID,
            "version": PRODUCT_VERSION,
            "ready": False,
            "mode": "robot",
            "auto_listen": False,
            "manual_recording": False,
            "voice_state": "initializing",
            "voice_text": "正在初始化本地语音系统",
            "transcript": "等待语音输入",
            "current_action": "等待机器人指令",
            "current_action_level": "idle",
            "sound_label": "中文语音待机",
            "audio_level": 0.0,
            "audio_rms": 0.0,
            "noise_rms": 0.0,
            "recognition_confidence": None,
            "snr_db": None,
            "recording_duration": 0.0,
            "command_gate": "等待语音指令",
            "command_gate_level": "idle",
            "command_name": "尚未解析",
            "command_register": "--",
            "command_value": "--",
            "statuses": {
                "robot": {"value": "正在检查", "level": "pending"},
                "microphone": {"value": "正在检查", "level": "pending"},
                "recognition": {"value": "正在检查", "level": "pending"},
                "ollama": {"value": "正在检查", "level": "pending"},
                "speaker": {"value": "正在检查", "level": "pending"},
                "local_modbus": {"value": "正在检查", "level": "pending"},
            },
            "robot_endpoint": f"{voice.ROBOT_MODBUS_HOST}:{voice.ROBOT_MODBUS_PORT}",
            "ollama_model": voice.OLLAMA_MODEL,
            "whisper_model": voice.WHISPER_MODEL,
            "wake_word": voice.WAKE_UP_WORDS[0],
            "last_error": "",
            "updated_at": time.time(),
        }
        self._record_diagnostic("info", "system", "系统初始化开始")

    def _record_diagnostic(self, level, source, message, detail=""):
        self._diagnostic_id += 1
        self._diagnostics.append(
            {
                "id": self._diagnostic_id,
                "timestamp": time.time(),
                "level": level if level in {"info", "success", "warning", "error"} else "info",
                "source": str(source or "system"),
                "message": str(message or ""),
                "detail": str(detail or ""),
            }
        )

    def _touch(self):
        self._event_id += 1
        self._state["event_id"] = self._event_id
        self._state["updated_at"] = time.time()

    def add_message(self, role, text):
        text = str(text or "").strip()
        if not text:
            return
        with self._lock:
            self._messages.append(
                {
                    "id": self._event_id + 1,
                    "role": "user" if role == "user" else "assistant",
                    "text": text,
                    "timestamp": time.time(),
                }
            )
            self._record_diagnostic(
                "info",
                "conversation",
                "操作员语音已识别" if role == "user" else "系统反馈已生成",
                text,
            )
            self._touch()

    def clear_messages(self):
        with self._lock:
            self._messages.clear()
            self._record_diagnostic("info", "operator", "会话记录已清空")
            self._touch()

    def set_mode(self, mode):
        with self._lock:
            self._state["mode"] = mode
            self._touch()

    def set_auto_listen(self, enabled):
        with self._lock:
            self._state["auto_listen"] = bool(enabled)
            self._touch()

    def emit(self, event):
        event_type = event.get("type", "")
        with self._lock:
            if event_type == "status":
                key = event.get("key")
                if key in self._state["statuses"]:
                    previous = self._state["statuses"][key]
                    updated = {
                        "value": str(event.get("value", "")),
                        "level": str(event.get("level", "pending")),
                    }
                    self._state["statuses"][key] = updated
                    if updated != previous:
                        diagnostic_level = {
                            "ok": "success",
                            "error": "error",
                            "active": "info",
                        }.get(updated["level"], "info")
                        self._record_diagnostic(
                            diagnostic_level,
                            key,
                            updated["value"],
                        )
            elif event_type == "message":
                self.add_message(event.get("role", "assistant"), event.get("text", ""))
                return
            elif event_type == "voice_state":
                previous_state = self._state["voice_state"]
                self._state["voice_state"] = str(event.get("state", "waiting"))
                self._state["voice_text"] = str(event.get("text", ""))
                if self._state["voice_state"] != previous_state:
                    self._record_diagnostic(
                        "error" if self._state["voice_state"] == "error" else "info",
                        "voice",
                        self._state["voice_text"],
                        self._state["voice_state"],
                    )
            elif event_type == "transcript":
                self._state["transcript"] = str(event.get("text", ""))
                self._record_diagnostic(
                    "info", "recognition", "识别文本已更新", self._state["transcript"]
                )
            elif event_type == "manual_recording":
                self._state["manual_recording"] = bool(event.get("active"))
            elif event_type == "recording_progress":
                self._state["recording_duration"] = max(0.0, float(event.get("duration", 0)))
            elif event_type == "recognition_metrics":
                confidence = event.get("confidence")
                self._state["recognition_confidence"] = (
                    None if confidence is None else max(0.0, min(1.0, float(confidence)))
                )
                self._state["snr_db"] = float(event.get("snr_db", 0))
                self._state["recording_duration"] = max(0.0, float(event.get("duration", 0)))
            elif event_type == "command_gate":
                self._state["command_gate"] = str(event.get("text", ""))
                self._state["command_gate_level"] = str(event.get("level", "idle"))
                self._state["command_name"] = str(event.get("command", "尚未解析"))
                self._state["command_register"] = str(event.get("register", "--"))
                self._state["command_value"] = str(event.get("value", "--"))
                gate_level = self._state["command_gate_level"]
                self._record_diagnostic(
                    "error" if gate_level == "locked" else "warning" if gate_level == "warning" else "info",
                    "interlock",
                    self._state["command_gate"],
                    f"{self._state['command_name']} / {self._state['command_register']} / {self._state['command_value']}",
                )
            elif event_type == "current_action":
                self._state["current_action"] = str(event.get("text", ""))
                self._state["current_action_level"] = str(event.get("level", "active"))
                self._record_diagnostic(
                    "error" if self._state["current_action_level"] == "error" else "info",
                    "robot_action",
                    self._state["current_action"],
                )
            elif event_type == "sound":
                self._state["sound_label"] = str(event.get("label", "声音反馈"))
            elif event_type == "audio_level":
                self._state["audio_level"] = max(0.0, min(1.0, float(event.get("level", 0))))
                self._state["audio_rms"] = max(0.0, float(event.get("rms", 0)))
                self._state["noise_rms"] = max(0.0, float(event.get("noise", 0)))
            elif event_type == "backend_ready":
                self._state["ready"] = True
                self._state["last_error"] = ""
                self._record_diagnostic("success", "system", "语音系统初始化完成")
            elif event_type == "backend_failed":
                self._state["ready"] = False
                self._state["voice_state"] = "error"
                self._state["voice_text"] = str(event.get("text", "后端初始化失败"))
                self._state["last_error"] = self._state["voice_text"]
                self._record_diagnostic("error", "system", self._state["last_error"])
            self._touch()

    def snapshot(self):
        with self._lock:
            snapshot = copy.deepcopy(self._state)
            snapshot["messages"] = list(self._messages)
            return snapshot

    def diagnostic_events(self):
        with self._lock:
            return copy.deepcopy(list(self._diagnostics))

    def clear_diagnostics(self):
        with self._lock:
            self._diagnostics.clear()
            self._touch()


class VoiceRuntime:
    def __init__(self, start_backend=True):
        self.state = ConsoleState()
        self.shutdown_event = threading.Event()
        self.auto_listen_event = threading.Event()
        self.manual_listen_event = threading.Event()
        self.manual_stop_event = threading.Event()
        self.mode_lock = threading.Lock()
        self.mode = "robot"
        self.ready = False
        self.started_at = time.time()
        self.backend_thread = None
        self.shutdown_hook = None
        self.sound_test_lock = threading.Lock()
        self.reconnect_lock = threading.Lock()
        self.engineer_log_lock = threading.Lock()
        self.engineer_log_start_offset = 0
        self.engineer_session_id = 0
        self.engineer_session_started_at = None
        self._start_backend = start_backend
        voice.ui_event_callback = self.state.emit

    def start(self):
        if not self._start_backend or self.backend_thread:
            return
        self.backend_thread = threading.Thread(
            target=self._backend_worker,
            daemon=True,
            name="voice-web-backend",
        )
        self.backend_thread.start()

    def get_mode(self):
        with self.mode_lock:
            return self.mode

    def set_mode(self, mode):
        if mode not in {"robot", "chat"}:
            raise ValueError("不支持的工作模式")
        snapshot = self.state.snapshot()
        if snapshot["manual_recording"] or snapshot["voice_state"] in {
            "recognizing", "thinking", "speaking", "stopping"
        }:
            raise RuntimeError("当前语音任务尚未完成，暂时不能切换模式")
        if voice.robot_command_lock.locked():
            raise RuntimeError("机器人指令正在下发，暂时不能切换模式")
        with self.mode_lock:
            self.mode = mode
        self.state.set_mode(mode)
        label = "机器人控制" if mode == "robot" else "AI 对话"
        if mode == "chat":
            self.state.emit(
                {
                    "type": "command_gate",
                    "text": "AI 对话模式，不会下发机器人动作",
                    "level": "locked",
                    "command": "对话回复",
                    "register": "--",
                    "value": "--",
                }
            )
        else:
            self.state.emit(
                {
                    "type": "command_gate",
                    "text": "等待语音指令",
                    "level": "idle",
                    "command": "尚未解析",
                    "register": "--",
                    "value": "--",
                }
            )
        self.state.add_message("assistant", f"已切换到{label}模式。")

    def set_auto_listen(self, enabled):
        enabled = bool(enabled)
        snapshot = self.state.snapshot()
        if snapshot["manual_recording"] or snapshot["voice_state"] in {
            "recognizing", "thinking", "speaking", "stopping"
        }:
            raise RuntimeError("当前语音任务尚未完成，暂时不能切换自动唤醒")
        if voice.robot_command_lock.locked():
            raise RuntimeError("机器人指令正在下发，暂时不能切换自动唤醒")
        if enabled:
            self.auto_listen_event.set()
            self.state.emit(
                {"type": "voice_state", "state": "waiting", "text": "等待唤醒：你好同学"}
            )
        else:
            self.auto_listen_event.clear()
            self.state.emit(
                {"type": "voice_state", "state": "paused", "text": "唤醒监听已暂停"}
            )
        self.state.set_auto_listen(enabled)

    def request_manual_listen(self):
        if not self.ready:
            raise RuntimeError("语音系统尚未就绪")
        snapshot = self.state.snapshot()
        if snapshot["voice_state"] == "error":
            raise RuntimeError("语音系统处于故障状态，请重启程序")
        if voice.robot_command_lock.locked():
            raise RuntimeError("机器人指令正在下发，请稍候再开始录音")
        if snapshot["manual_recording"]:
            if snapshot["voice_state"] == "stopping":
                return
            self.manual_stop_event.set()
            self.state.emit(
                {"type": "voice_state", "state": "stopping", "text": "正在结束录音"}
            )
            return
        if snapshot["voice_state"] in {"recognizing", "thinking", "speaking", "stopping"}:
            raise RuntimeError("当前语音任务尚未完成")
        self.manual_stop_event.clear()
        self.state.emit({"type": "manual_recording", "active": True})
        self.state.emit(
            {
                "type": "command_gate",
                "text": "正在采集语音，结束后开始解析",
                "level": "capturing",
                "command": "采集中",
                "register": "--",
                "value": "--",
            }
        )
        self.manual_listen_event.set()
        self.state.emit(
            {"type": "voice_state", "state": "recording", "text": "正在录音，再次点击麦克风结束"}
        )

    def reconnect_robot(self):
        snapshot = self.state.snapshot()
        if snapshot["manual_recording"] or snapshot["voice_state"] in {
            "recognizing", "thinking", "speaking", "stopping"
        }:
            raise RuntimeError("当前语音任务尚未完成，暂时不能重连机器人")
        if voice.robot_command_lock.locked():
            raise RuntimeError("机器人指令正在下发，禁止重连通信")
        if not self.reconnect_lock.acquire(blocking=False):
            raise RuntimeError("机器人正在重新连接")
        self.state.emit(
            {"type": "status", "key": "robot", "value": "正在重新连接", "level": "active"}
        )
        def worker():
            try:
                voice.init_modbus_client_robot()
            finally:
                self.reconnect_lock.release()

        try:
            threading.Thread(
                target=worker,
                daemon=True,
                name="robot-reconnect",
            ).start()
        except Exception:
            self.reconnect_lock.release()
            raise

    def test_sound(self):
        if not self.sound_test_lock.acquire(blocking=False):
            raise RuntimeError("中文语音正在播报")

        def worker():
            try:
                voice.speak("你好，我是工业机器人语音控制示例。中文语音播报正常。")
            finally:
                self.sound_test_lock.release()

        try:
            threading.Thread(target=worker, daemon=True, name="sound-test").start()
        except Exception:
            self.sound_test_lock.release()
            raise

    def clear_messages(self):
        self.state.clear_messages()

    def reset_engineer_logs(self):
        with self.engineer_log_lock:
            self.engineer_log_start_offset = LOG_PATH.stat().st_size if LOG_PATH.is_file() else 0
            self.engineer_session_id += 1
            self.engineer_session_started_at = time.time()
        self.state.clear_diagnostics()

    def read_engineer_log(self, limit=240, level="all", query=""):
        with self.engineer_log_lock:
            start_offset = self.engineer_log_start_offset
        return read_log_tail(limit=limit, level=level, query=query, start_offset=start_offset)

    def send_robot_command(self, value):
        if not self.ready:
            raise RuntimeError("语音系统尚未就绪")
        if self.get_mode() != "robot":
            raise RuntimeError("请先切换到机器人控制模式")
        snapshot = self.state.snapshot()
        if snapshot["manual_recording"] or snapshot["voice_state"] in {
            "listening", "hearing", "recording", "recognizing", "thinking", "speaking", "stopping"
        }:
            raise RuntimeError("当前语音任务尚未完成")
        if voice.tts_fault_event.is_set():
            raise RuntimeError("中文语音播报故障，指令下发已锁止")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("机器人指令值必须是整数")
        matched = next((command for command in voice.COMMANDS if command["value"] == value), None)
        if matched is None:
            raise ValueError("不支持的机器人指令")
        self.state.add_message("user", f"点击下发：{matched['desc']}")
        result, _ = voice.send_robot_command_value(value)
        if result == "sent":
            return
        if result == "busy":
            raise RuntimeError("另一条指令正在下发")
        if result == "unknown":
            raise RuntimeError("指令状态不明，禁止重试，请人工检查")
        raise RuntimeError("机器人未连接，指令未发送")

    def announce_ready(self):
        if not voice.speak(STARTUP_ANNOUNCEMENT):
            raise RuntimeError("启动语音播报失败，语音控制已锁止")

    def engineer_snapshot(self):
        state = self.state.snapshot()
        log_size = LOG_PATH.stat().st_size if LOG_PATH.is_file() else 0
        with self.engineer_log_lock:
            log_start_offset = self.engineer_log_start_offset
            engineer_session_id = self.engineer_session_id
            engineer_session_started_at = self.engineer_session_started_at
        return {
            "product": PRODUCT_ID,
            "version": PRODUCT_VERSION,
            "generated_at": time.time(),
            "uptime_seconds": max(0.0, time.time() - self.started_at),
            "process": {
                "pid": os.getpid(),
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "service": {
                "ready": state["ready"],
                "mode": state["mode"],
                "voice_state": state["voice_state"],
                "voice_text": state["voice_text"],
                "auto_listen": state["auto_listen"],
                "manual_recording": state["manual_recording"],
                "last_error": state["last_error"],
            },
            "safety": {
                "command_gate": state["command_gate"],
                "command_gate_level": state["command_gate_level"],
                "current_action": state["current_action"],
                "current_action_level": state["current_action_level"],
                "robot_write_outcome": voice.last_robot_write_outcome,
                "robot_action_pending": "指令下发中" if voice.robot_command_lock.locked() else None,
                "tts_fault": voice.tts_fault_event.is_set(),
                "local_state_fault": voice.local_state_fault_event.is_set(),
            },
            "audio": {
                "transcript": state["transcript"],
                "confidence": state["recognition_confidence"],
                "snr_db": state["snr_db"],
                "duration": state["recording_duration"],
                "rms": state["audio_rms"],
                "noise_rms": state["noise_rms"],
                "sound_label": state["sound_label"],
            },
            "statuses": state["statuses"],
            "events": self.state.diagnostic_events(),
            "log": {
                "name": LOG_PATH.name,
                "available": LOG_PATH.is_file(),
                "bytes": log_size,
                "visible_bytes": max(0, log_size - min(log_size, log_start_offset)),
                "history_bytes": min(log_size, log_start_offset),
                "updated_at": LOG_PATH.stat().st_mtime if LOG_PATH.is_file() else None,
                "session_id": engineer_session_id,
                "session_started_at": engineer_session_started_at,
            },
            "deployment": read_deployment_report(),
        }

    def dispatch(self, payload):
        action = str(payload.get("action", ""))
        if action == "manual_listen":
            self.request_manual_listen()
        elif action == "set_auto_listen":
            if not self.ready:
                raise RuntimeError("语音系统尚未就绪")
            self.set_auto_listen(bool(payload.get("enabled")))
        elif action == "set_mode":
            self.set_mode(str(payload.get("mode", "")))
        elif action == "reconnect_robot":
            if not self.ready:
                raise RuntimeError("语音系统尚未就绪")
            self.reconnect_robot()
        elif action == "test_sound":
            if not self.ready:
                raise RuntimeError("语音系统尚未就绪")
            self.test_sound()
        elif action == "clear_messages":
            self.clear_messages()
        elif action == "reset_engineer_logs":
            self.reset_engineer_logs()
        elif action == "send_robot_command":
            self.send_robot_command(payload.get("value"))
        elif action == "shutdown":
            threading.Thread(target=self.request_shutdown, daemon=True).start()
        else:
            raise ValueError("未知操作")
        return self.state.snapshot()

    def _backend_worker(self):
        try:
            exit_code = voice.initialize_system(require_robot=False, require_ollama=False)
            if exit_code != 0:
                self.state.emit(
                    {
                        "type": "backend_failed",
                        "text": f"系统初始化失败，错误码 {exit_code}",
                    }
                )
                return
            self.ready = True
            if not voice.START_PAUSED:
                self.auto_listen_event.set()
                self.state.set_auto_listen(True)
            self.state.emit({"type": "backend_ready"})
            self.announce_ready()
            voice.voice_control_loop(
                self.shutdown_event,
                self.auto_listen_event,
                self.manual_listen_event,
                self.manual_stop_event,
                self.get_mode,
            )
        except Exception as exc:
            print(f"❌ Web 后端服务异常：{exc}")
            self.state.emit({"type": "backend_failed", "text": f"后端服务异常：{exc}"})
        finally:
            self.ready = False
            voice.cleanup_resources()

    def request_shutdown(self):
        if self.shutdown_event.is_set():
            return
        self.shutdown_event.set()
        self.auto_listen_event.clear()
        self.manual_listen_event.set()
        self.manual_stop_event.set()
        self.state.emit({"type": "manual_recording", "active": False})
        self.state.set_auto_listen(False)
        self.state.emit({"type": "voice_state", "state": "paused", "text": "正在安全关闭"})
        if self.shutdown_hook:
            self.shutdown_hook()

    def stop(self):
        self.request_shutdown()
        if self.backend_thread and self.backend_thread.is_alive():
            self.backend_thread.join(timeout=6)
        voice.cleanup_resources()


def _is_local_request(handler):
    host = handler.headers.get("Host", "").split(":", 1)[0].lower()
    if host not in {"127.0.0.1", "localhost"}:
        return False
    origin = handler.headers.get("Origin")
    if not origin:
        return True
    parsed = urllib.parse.urlparse(origin)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def _log_level(line):
    lowered = line.casefold()
    if any(token in line for token in ("❌",)) or any(
        token in lowered for token in ("error", "失败", "异常")
    ):
        return "error"
    if any(token in line for token in ("⚠",)) or any(
        token in lowered for token in ("warning", "warn", "超时", "锁止")
    ):
        return "warning"
    if "✅" in line or any(token in lowered for token in ("success", "已就绪", "正常")):
        return "success"
    return "info"


def read_log_tail(limit=240, level="all", query="", start_offset=0):
    limit = max(20, min(500, int(limit)))
    level = level if level in {"all", "info", "success", "warning", "error"} else "all"
    query = str(query or "").strip()[:80].casefold()
    if not LOG_PATH.is_file():
        return []

    max_bytes = 512 * 1024
    with LOG_PATH.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        requested_offset = max(0, int(start_offset))
        start_offset = 0 if size < requested_offset else requested_offset
        read_start = max(start_offset, size - max_bytes)
        stream.seek(read_start)
        raw = stream.read(max_bytes)
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if read_start > start_offset and lines:
        lines = lines[1:]

    results = []
    for index, line in enumerate(lines):
        text = line.strip()[:2000]
        if not text:
            continue
        item_level = _log_level(text)
        if level != "all" and item_level != level:
            continue
        if query and query not in text.casefold():
            continue
        results.append({"id": index + 1, "level": item_level, "text": text})
    return results[-limit:]


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "IndustrialVoiceConsole/2.6"

    @property
    def runtime(self):
        return self.server.runtime

    def _send_headers(self, status, content_type, content_length=None, api=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self'; font-src 'self'; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        self.send_header("Cache-Control", "no-store" if api else "no-cache")
        self.end_headers()

    def _send_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_headers(status, "application/json; charset=utf-8", len(data), api=True)
        self.wfile.write(data)

    def _send_static(self, request_path):
        relative = "index.html" if request_path == "/" else request_path.lstrip("/")
        try:
            candidate = (WEB_DIR / urllib.parse.unquote(relative)).resolve(strict=True)
        except (FileNotFoundError, OSError):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if candidate != WEB_DIR and WEB_DIR not in candidate.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(candidate.name)
        if candidate.suffix == ".woff2":
            content_type = "font/woff2"
        self._send_headers(HTTPStatus.OK, content_type or "application/octet-stream", len(content))
        self.wfile.write(content)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(
                {"ok": True, "product": PRODUCT_ID, "version": PRODUCT_VERSION}
            )
            return
        if parsed.path == "/api/state":
            if not _is_local_request(self):
                self._send_json({"ok": False, "error": "拒绝非本机访问"}, HTTPStatus.FORBIDDEN)
                return
            self._send_json({"ok": True, "state": self.runtime.state.snapshot()})
            return
        if parsed.path == "/api/engineer":
            if not _is_local_request(self):
                self._send_json({"ok": False, "error": "拒绝非本机访问"}, HTTPStatus.FORBIDDEN)
                return
            self._send_json({"ok": True, "diagnostics": self.runtime.engineer_snapshot()})
            return
        if parsed.path == "/api/engineer/logs":
            if not _is_local_request(self):
                self._send_json({"ok": False, "error": "拒绝非本机访问"}, HTTPStatus.FORBIDDEN)
                return
            query = urllib.parse.parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["240"])[0])
            except ValueError:
                limit = 240
            self._send_json(
                {
                    "ok": True,
                    "lines": self.runtime.read_engineer_log(
                        limit=limit,
                        level=query.get("level", ["all"])[0],
                        query=query.get("query", [""])[0],
                    ),
                }
            )
            return
        self._send_static(parsed.path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/action":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not _is_local_request(self):
            self._send_json({"ok": False, "error": "拒绝非本机访问"}, HTTPStatus.FORBIDDEN)
            return
        if "application/json" not in self.headers.get("Content-Type", ""):
            self._send_json({"ok": False, "error": "仅接受 JSON"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            self._send_json({"ok": False, "error": "请求大小无效"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求必须是对象")
            state = self.runtime.dispatch(payload)
            self._send_json({"ok": True, "state": state})
        except (ValueError, RuntimeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            print(f"❌ Web 操作失败：{exc}")
            self._send_json({"ok": False, "error": "操作执行失败"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/") and not self.path.startswith("/api/health"):
            return
        print(f"🌐 {self.address_string()} - {fmt % args}")


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, runtime):
        self.runtime = runtime
        super().__init__(address, ConsoleHandler)


def _existing_console_url():
    for port in HTTP_PORT_RANGE:
        url = f"http://{HTTP_HOST}:{port}"
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=0.25) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("product") == PRODUCT_ID:
                return url
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return None


def _find_available_port():
    for port in HTTP_PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HTTP_HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError("本地控制台端口 8765-8775 均被占用")


def _find_edge():
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


def open_console(url):
    edge = _find_edge()
    if edge:
        profile = Path(os.environ.get("LOCALAPPDATA", str(BASE_DIR))) / "IndustrialVoiceConsole/EdgeProfile"
        profile.mkdir(parents=True, exist_ok=True)
        return subprocess.Popen(
            [
                str(edge),
                f"--app={url}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    webbrowser.open(url)
    return None


def main():
    no_browser = os.environ.get("VOICE_ASSISTANT_NO_BROWSER") == "1"
    existing = _existing_console_url()
    if existing:
        if not no_browser:
            open_console(existing)
        return 0

    port = _find_available_port()
    url = f"http://{HTTP_HOST}:{port}"
    runtime = VoiceRuntime()
    server = ConsoleServer((HTTP_HOST, port), runtime)
    runtime.shutdown_hook = server.shutdown
    runtime.start()
    browser_process = None if no_browser else open_console(url)

    if browser_process is not None:
        def monitor_browser():
            browser_process.wait()
            time.sleep(0.5)
            runtime.request_shutdown()

        threading.Thread(target=monitor_browser, daemon=True, name="browser-monitor").start()

    print(f"✅ 工业语音控制台已启动：{url}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
