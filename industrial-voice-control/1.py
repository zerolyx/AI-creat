#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Industrial robot voice-control reference implementation."""
import ollama
import base64
import pyttsx3
import sounddevice as sd
import numpy as np
import whisper
import webrtcvad
import winsound
import time
import warnings
import threading
import os
import re
import queue
import subprocess
import tempfile
import tkinter as tk
import wave
from tkinter import ttk
from collections import deque
from difflib import SequenceMatcher
import opencc
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.client import ModbusClient
import sys


PRIVATE_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "robot_config.local.env")
PRIVATE_CONFIG_KEYS = {
    "LOCAL_MODBUS_HOST",
    "LOCAL_MODBUS_PORT",
    "ROBOT_MODBUS_HOST",
    "ROBOT_MODBUS_PORT",
    "ROBOT_COMMAND_REGISTER",
    "ROBOT_CONTROL_ENABLED",
}


def _load_private_config():
    """Load only known key/value settings from an ignored local config file."""
    if not os.path.isfile(PRIVATE_CONFIG_PATH):
        return
    try:
        with open(PRIVATE_CONFIG_PATH, encoding="utf-8-sig") as stream:
            for raw_line in stream:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = (part.strip() for part in line.split("=", 1))
                if key in PRIVATE_CONFIG_KEYS and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        print(f"⚠️ 未能读取私有机器人配置：{exc}")


_load_private_config()

if sys.stdout is None:
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_assistant.log")
    log_stream = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_stream
    sys.stderr = log_stream

# Keep Chinese diagnostics readable in legacy Windows consoles that cannot
# encode emoji characters used by the status messages.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")

# ===================== 核心配置 =====================
WAKE_UP_WORDS = ["你好同学", "你好，同学", "你好同雪", "你好童鞋"]
WAKE_UP_RESPONSE = "我在，请说。"
STARTUP_ANNOUNCEMENT = "语音系统已就绪。"
ECHO_GUARD_SEC = 0.35

MICROPHONE_INDEX = None
SAMPLING_RATE = 16000
FRAME_DURATION_MS = 30
VAD_MODE = 2
PRE_ROLL_SEC = 0.3
END_SILENCE_SEC = 0.75
MIN_SPEECH_SEC = 0.35
FALSE_START_SEC = 1.2
MAX_RECORD_SEC = 8.0
MANUAL_MAX_RECORD_SEC = 20.0
WAKE_LISTEN_TIMEOUT = 4.0
COMMAND_LISTEN_TIMEOUT = 6.0
MIN_AUDIO_RMS = 0.0015
NOISE_RMS_RATIO = 1.25
TARGET_AUDIO_RMS = 0.08
MAX_AUDIO_GAIN = 8.0
MIN_ROBOT_ASR_CONFIDENCE = 0.48
MIN_ROBOT_SNR_DB = 1.0

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_INITIAL_PROMPT = (
    "工业机器人语音控制示例。唤醒词是你好同学。"
    "请识别普通话以及带口音的中文。示例指令包括执行示例搬运、定位、检测、装配和复位。"
)
WHISPER_WAKE_PROMPT = "请准确识别中文唤醒词：你好同学。"
WHISPER_COMMAND_PROMPT = (
    "请准确识别普通话或带口音的工业机器人示例指令。候选词："
    "执行示例搬运、执行示例定位、执行示例检测、执行示例装配、执行示例复位。"
)

OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5:7b"
STARTUP_CHECK_ONLY = os.environ.get("VOICE_ASSISTANT_STARTUP_CHECK_ONLY") == "1"
START_PAUSED = os.environ.get("VOICE_ASSISTANT_START_PAUSED", "1") != "0"
AUDIBLE_TONES_ENABLED = os.environ.get("VOICE_ASSISTANT_ENABLE_TONES") == "1"
try:
    TTS_RATE = max(120, min(240, int(os.environ.get("VOICE_ASSISTANT_TTS_RATE", "180"))))
except ValueError:
    TTS_RATE = 180
ollama_client = None
selected_microphone_index = None
whisper_model = None
RECORD_INTERRUPTED = object()

ui_event_callback = None

def emit_ui(event_type, **payload):
    callback = ui_event_callback
    if callback:
        try:
            callback({"type": event_type, **payload})
        except Exception:
            pass

def _read_int_env(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


# Public releases stay in safe demonstration mode until a private local
# configuration explicitly enables hardware control.
LOCAL_MODBUS_HOST = os.environ.get("LOCAL_MODBUS_HOST", "127.0.0.1").strip() or "127.0.0.1"
LOCAL_MODBUS_PORT = _read_int_env("LOCAL_MODBUS_PORT", 1502, 1, 65535)
ROBOT_MODBUS_HOST = os.environ.get("ROBOT_MODBUS_HOST", "127.0.0.1").strip() or "127.0.0.1"
ROBOT_MODBUS_PORT = _read_int_env("ROBOT_MODBUS_PORT", 502, 1, 65535)
ROBOT_COMMAND_REGISTER = _read_int_env("ROBOT_COMMAND_REGISTER", 40001, 40001, 49999)
ROBOT_COMMAND_REGISTER_LABEL = f"R{ROBOT_COMMAND_REGISTER}"
ROBOT_CONTROL_ENABLED = os.environ.get("ROBOT_CONTROL_ENABLED", "0").strip().casefold() in {"1", "true", "yes"}

# Local Modbus service
modbus_server = None
modbus_client_local = None
local_state_fault_event = threading.Event()

# Public demonstration command map. Replace only in a private local config or
# deployment-specific branch after a complete safety review.
COMMANDS = [
    {"words": ["执行示例搬运", "执行搬运", "启动搬运"], "objects": ["示例搬运", "搬运"], "value": 1, "desc": "示例搬运"},
    {"words": ["执行示例定位", "执行定位", "启动定位"], "objects": ["示例定位", "定位"], "value": 2, "desc": "示例定位"},
    {"words": ["执行示例检测", "执行检测", "启动检测"], "objects": ["示例检测", "检测"], "value": 3, "desc": "示例检测"},
    {"words": ["执行示例装配", "执行装配", "启动装配"], "objects": ["示例装配", "装配"], "value": 4, "desc": "示例装配"},
    {"words": ["执行示例复位", "执行复位", "启动复位"], "objects": ["示例复位", "复位"], "value": 5, "desc": "示例复位"},
]

ACTION_WORDS = ("执行", "启动", "运行", "开始", "触发")
NEGATIVE_WORDS = ("不要", "不准", "不许", "别", "取消", "停止", "不用", "不需要", "禁止", "不")
QUESTION_WORDS = ("什么", "怎么", "怎样", "为什么", "为何", "能否", "可以吗", "是不是", "介绍", "解释")

# Robot Modbus client
modbus_client_robot = None
robot_client_lock = threading.RLock()
robot_command_lock = threading.Lock()
last_robot_write_outcome = "idle"

ROBOT_READ_DISPLAY_ADDR = [30001 + i for i in range(16)]
ROBOT_WRITE_DISPLAY_ADDR = [40001 + i for i in range(16)]
ROBOT_POLL_INTERVAL = 0.5
last_robot_read_data = {addr: None for addr in ROBOT_READ_DISPLAY_ADDR}

warnings.filterwarnings("ignore")
converter = opencc.OpenCC('t2s')

# ===================== 语音合成 =====================
tts_engine = None
tts_thread = None
tts_queue = queue.Queue()
tts_ready_event = threading.Event()
tts_error = None
tts_voice_name = "系统默认语音"
tts_voice_id = ""
tts_output_device_index = None
tts_output_device_name = "系统默认扬声器"
tts_call_lock = threading.Lock()
audio_session_lock = threading.Lock()
audio_capture_event = threading.Event()
audio_output_event = threading.Event()
tts_fault_event = threading.Event()

FEEDBACK_TONES = {
    "listen": [(880, 65)],
    "captured": [(1047, 55), (1319, 70)],
    "sent": [(659, 65), (880, 90)],
    "cancel": [(523, 90)],
    "error": [(330, 160)],
    "ready": [(659, 60), (784, 60), (988, 90)],
}

def play_feedback_tone(kind):
    label_map = {
        "listen": "开始聆听",
        "captured": "已收到语音",
        "sent": "指令已发送",
        "cancel": "已取消",
        "error": "需要注意",
        "ready": "语音反馈正常",
    }
    if not AUDIBLE_TONES_ENABLED:
        emit_ui("sound", label="中文语音待机")
        return False

    emit_ui("sound", label=label_map.get(kind, "声音提示"))
    try:
        with audio_session_lock:
            audio_output_event.set()
            for frequency, duration in FEEDBACK_TONES.get(kind, []):
                winsound.Beep(frequency, duration)
                time.sleep(0.025)
    except RuntimeError:
        try:
            winsound.MessageBeep()
        except RuntimeError:
            return False
    finally:
        audio_output_event.clear()
        emit_ui("sound", label="中文语音待机")
    return True

def _voice_description(voice):
    return " ".join(
        str(value or "")
        for value in (
            getattr(voice, "id", ""),
            getattr(voice, "name", ""),
            getattr(voice, "languages", ""),
        )
    ).lower()

def _select_tts_voice(voices):
    if not voices:
        return None

    preferred_names = (
        "xiaoxiao", "huihui", "yaoyao", "yunxi", "kangkang", "hanhan",
    )

    def score(voice):
        description = _voice_description(voice)
        points = 0
        if any(token in description for token in ("zh-cn", "zh_cn", "chinese", "mandarin")):
            points += 100
        for index, token in enumerate(preferred_names):
            if token in description:
                points += 20 - index
        return points

    selected = max(voices, key=score)
    return selected if score(selected) >= 100 else None

def _prepare_spoken_text(text):
    spoken = str(text or "").strip()
    spoken = re.sub(r"\bModbus\b", "机器人通信", spoken, flags=re.IGNORECASE)
    spoken = re.sub(r"\bAI\b", "人工智能", spoken, flags=re.IGNORECASE)
    spoken = spoken.replace(ROBOT_COMMAND_REGISTER_LABEL, f"寄存器{ROBOT_COMMAND_REGISTER}")
    if spoken and spoken[-1] not in "。！？!?；;":
        spoken += "。"
    return spoken

def _select_output_device():
    devices = sd.query_devices()
    override = os.environ.get("VOICE_ASSISTANT_OUTPUT_DEVICE", "").strip()
    candidates = []

    if override:
        if override.isdigit():
            candidates.append(int(override))
        else:
            needle = override.casefold()
            candidates.extend(
                index
                for index, device in enumerate(devices)
                if device["max_output_channels"] > 0
                and needle in str(device["name"]).casefold()
            )

    try:
        candidates.append(int(sd.default.device[1]))
    except (TypeError, ValueError, IndexError):
        pass
    candidates.extend(
        index for index, device in enumerate(devices)
        if device["max_output_channels"] > 0
    )

    checked = set()
    for index in candidates:
        if index in checked or index < 0 or index >= len(devices):
            continue
        checked.add(index)
        device = devices[index]
        if device["max_output_channels"] > 0:
            return index, str(device["name"])
    raise RuntimeError("未找到可用的扬声器或耳机输出设备")

def _read_tts_wave(path):
    with wave.open(path, "rb") as stream:
        channels = stream.getnchannels()
        sample_width = stream.getsampwidth()
        sample_rate = stream.getframerate()
        frames = stream.getnframes()
        compression = stream.getcomptype()
        raw = stream.readframes(frames)

    if compression != "NONE" or sample_width != 2:
        raise RuntimeError("中文语音格式不受支持")
    if channels not in (1, 2) or sample_rate <= 0 or frames < sample_rate * 0.1:
        raise RuntimeError("中文语音没有生成有效音频帧")

    samples = np.frombuffer(raw, dtype=np.int16)
    if samples.size == 0:
        raise RuntimeError("中文语音音频为空")
    audio = samples.astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels)
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    peak = float(np.max(np.abs(audio)))
    if not np.isfinite(rms) or not np.isfinite(peak) or rms < 0.0005 or peak < 0.005:
        raise RuntimeError("中文语音合成结果为静音")
    return audio, sample_rate, rms, peak

def _play_tts_wave(path):
    audio, sample_rate, rms, peak = _read_tts_wave(path)
    device = sd.query_devices(tts_output_device_index)
    try:
        sd.check_output_settings(
            device=tts_output_device_index,
            channels=1 if audio.ndim == 1 else audio.shape[1],
            dtype="float32",
            samplerate=sample_rate,
        )
    except Exception:
        target_rate = int(device["default_samplerate"])
        source_frames = audio.shape[0]
        target_frames = max(1, int(round(source_frames * target_rate / sample_rate)))
        source_points = np.linspace(0.0, 1.0, source_frames, endpoint=False)
        target_points = np.linspace(0.0, 1.0, target_frames, endpoint=False)
        if audio.ndim == 1:
            audio = np.interp(target_points, source_points, audio).astype(np.float32)
        else:
            audio = np.column_stack(
                [np.interp(target_points, source_points, audio[:, channel])
                 for channel in range(audio.shape[1])]
            ).astype(np.float32)
        sample_rate = target_rate

    sd.play(audio, sample_rate, device=tts_output_device_index, blocking=True)
    return {
        "frames": int(audio.shape[0]),
        "sample_rate": int(sample_rate),
        "rms": rms,
        "peak": peak,
    }

def _synthesize_tts_file(text, path):
    helper = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "scripts",
        "synthesize_speech.py",
    )
    completed = subprocess.run(
        [
            sys.executable,
            helper,
            path,
            tts_voice_id,
            str(TTS_RATE),
            base64.b64encode(text.encode("utf-8")).decode("ascii"),
        ],
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=12,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()
        raise RuntimeError(f"中文语音合成进程失败（{completed.returncode}）：{detail}")

def _tts_worker():
    global tts_engine, tts_error, tts_voice_name, tts_voice_id
    global tts_output_device_index, tts_output_device_name
    probe_engine = None
    try:
        probe_engine = pyttsx3.init()
        voices = probe_engine.getProperty("voices")
        selected_voice = _select_tts_voice(voices)
        if selected_voice is None:
            raise RuntimeError("未找到可用的简体中文系统语音")
        tts_voice_id = str(selected_voice.id)
        tts_voice_name = str(getattr(selected_voice, "name", "") or "简体中文系统语音")
        tts_output_device_index, tts_output_device_name = _select_output_device()
        tts_engine = True
    except Exception as exc:
        tts_error = exc
    finally:
        if probe_engine is not None:
            try:
                probe_engine.stop()
            except Exception:
                pass
        tts_ready_event.set()

    if tts_engine is None:
        return

    while True:
        item = tts_queue.get()
        if item is None:
            break
        text, done_event, result = item
        temp_path = None
        try:
            handle, temp_path = tempfile.mkstemp(prefix="industrial_voice_", suffix=".wav")
            os.close(handle)
            os.remove(temp_path)
            _synthesize_tts_file(text, temp_path)
            if not os.path.isfile(temp_path) or os.path.getsize(temp_path) < 1024:
                raise RuntimeError("中文语音文件没有生成")
            result.update(_play_tts_wave(temp_path))
            result["device"] = tts_output_device_name
            result["ok"] = True
        except Exception as exc:
            result["ok"] = False
            result["error"] = str(exc)
            print(f"❌ 语音合成失败：{exc}")
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            done_event.set()

def init_tts_engine():
    global tts_thread
    if tts_thread and tts_thread.is_alive():
        return
    tts_ready_event.clear()
    tts_thread = threading.Thread(target=_tts_worker, daemon=True, name="tts-worker")
    tts_thread.start()
    if not tts_ready_event.wait(8) or tts_engine is None:
        raise RuntimeError(f"语音合成初始化失败：{tts_error or '等待超时'}")
    tts_fault_event.clear()
    emit_ui(
        "status",
        key="speaker",
        value=f"{tts_voice_name} → {tts_output_device_name}",
        level="ok",
    )

def speak(text, timeout=None):
    text = str(text or "").strip()
    if not text:
        return True
    emit_ui("message", role="assistant", text=text)
    if tts_engine is None:
        print(f"语音播报：{text}")
        emit_ui("status", key="speaker", value="语音播报不可用", level="error")
        emit_ui("sound", label="播报不可用")
        return False
    if tts_fault_event.is_set():
        emit_ui("voice_state", state="error", text="语音播报故障，监听已锁止")
        emit_ui("sound", label="播报故障")
        return False

    with tts_call_lock:
        with audio_session_lock:
            audio_output_event.set()
            try:
                emit_ui("voice_state", state="speaking", text="正在进行中文语音播报")
                emit_ui("sound", label="中文语音播报中")
                if timeout is None:
                    timeout = max(15, min(60, len(text) * 0.45 + 12))
                done_event = threading.Event()
                result = {"ok": True, "error": ""}
                tts_queue.put((_prepare_spoken_text(text), done_event, result))
                completed = done_event.wait(timeout)
                if not completed:
                    print("⚠️ 语音播报超时，正在等待音频设备安全收尾")
                    completed = done_event.wait(3)
                if not completed:
                    sd.stop()

                success = completed and result.get("ok", False)
                if not success:
                    tts_fault_event.set()
                    error = result.get("error") or "播报超时"
                    print(f"❌ 语音播报故障：{error}")
                    emit_ui("status", key="speaker", value="语音播报故障，监听已锁止", level="error")
                    emit_ui("voice_state", state="error", text="语音播报故障，监听已锁止")
                    emit_ui("sound", label="播报故障")
                    return False

                time.sleep(ECHO_GUARD_SEC)
                emit_ui(
                    "status",
                    key="speaker",
                    value=f"中文播报正常 · {result.get('device', tts_output_device_name)}",
                    level="ok",
                )
                emit_ui("sound", label="中文语音待机")
                return True
            finally:
                audio_output_event.clear()

# ===================== Modbus =====================
def start_modbus_server():
    global modbus_server
    try:
        modbus_server = ModbusServer(host=LOCAL_MODBUS_HOST, port=LOCAL_MODBUS_PORT, no_block=True)
        modbus_server.start()
        print(f"✅ 本地Modbus服务器已启动：{LOCAL_MODBUS_HOST}:{LOCAL_MODBUS_PORT}")
        emit_ui("status", key="local_modbus", value="本地服务正常", level="ok")
        return True
    except Exception as e:
        print(f"❌ Modbus服务器启动失败：{e}")
        emit_ui("status", key="local_modbus", value="本地端口异常", level="error")
        modbus_server = None
        return False

def init_modbus_client_local():
    global modbus_client_local
    modbus_client_local = ModbusClient(
        host=LOCAL_MODBUS_HOST, port=LOCAL_MODBUS_PORT,
        auto_open=False, auto_close=False, timeout=2
    )
    if not modbus_client_local.open():
        print(f"❌ 本地Modbus客户端连接失败：{modbus_client_local.last_error_as_txt}")
        local_state_fault_event.set()
        emit_ui("status", key="local_modbus", value="本地状态服务异常", level="error")
        return False
    local_state_fault_event.clear()
    emit_ui("status", key="local_modbus", value="工艺参考服务正常 · 不参与联锁", level="ok")
    return True

def init_modbus_client_robot(retries=3):
    global modbus_client_robot
    with robot_client_lock:
        if modbus_client_robot:
            modbus_client_robot.close()
        if not ROBOT_CONTROL_ENABLED:
            modbus_client_robot = None
            print("ℹ️ 安全开源示例模式：机器人控制未启用")
            emit_ui("status", key="robot", value="示例模式：控制未启用", level="error")
            return False

        modbus_client_robot = ModbusClient(
            host=ROBOT_MODBUS_HOST, port=ROBOT_MODBUS_PORT,
            auto_open=False, auto_close=False, timeout=2
        )
        for attempt in range(1, retries + 1):
            if modbus_client_robot.open():
                probe = modbus_client_robot.read_holding_registers(ROBOT_COMMAND_REGISTER - 40001, 1)
                if probe is not None:
                    print(
                        f"✅ 机器人Modbus已连接：{ROBOT_MODBUS_HOST}:"
                        f"{ROBOT_MODBUS_PORT}，{ROBOT_COMMAND_REGISTER_LABEL}={probe[0]}"
                    )
                    emit_ui("status", key="robot", value="机器人Modbus已连接", level="ok")
                    return True
                print(
                    f"⚠️ TCP端口可连接，但机器人Modbus无响应（{attempt}/{retries}）："
                    f"{modbus_client_robot.last_error_as_txt}"
                )
                modbus_client_robot.close()
            else:
                print(
                    f"⚠️ 机器人连接失败（{attempt}/{retries}）："
                    f"{modbus_client_robot.last_error_as_txt}"
                )
            if attempt < retries:
                time.sleep(1)
        emit_ui("status", key="robot", value="机器人Modbus无响应", level="error")
        return False

def ensure_robot_connected():
    if modbus_client_robot and modbus_client_robot.is_open:
        return True
    return init_modbus_client_robot(retries=1)

def robot_write_register(display_addr, value):
    global last_robot_write_outcome
    offset = display_addr - 40001
    with robot_client_lock:
        if not ROBOT_CONTROL_ENABLED:
            last_robot_write_outcome = "disabled"
            print("❌ 安全开源示例模式未启用机器人控制，已阻止指令下发")
            return False
        last_robot_write_outcome = "checking"
        if not ensure_robot_connected():
            last_robot_write_outcome = "not_sent"
            print("❌ 机器人未连接，已阻止指令下发")
            return False

        write_error = "未知通信错误"
        try:
            if modbus_client_robot.write_single_register(offset, value):
                last_robot_write_outcome = "confirmed"
                return True
            write_error = modbus_client_robot.last_error_as_txt
        except Exception as e:
            write_error = str(e)

        # A Modbus timeout does not prove that the robot missed the request.
        # Never repeat a motion write: reconnect and perform a read-only check.
        print(f"⚠️ 写入响应异常：{write_error}")
        print("ℹ️ 为避免重复动作，不会重发；正在只读确认机器人寄存器")
        if modbus_client_robot:
            modbus_client_robot.close()
        time.sleep(0.15)
        confirmed_value = robot_read_register(display_addr)
        if confirmed_value == value:
            last_robot_write_outcome = "confirmed"
            print(f"✅ 只读确认成功：{display_addr}={value}")
            return True

        print(
            f"❌ 无法确认指令状态（读取值：{confirmed_value}），"
            "已停止后续流程，请人工检查机器人"
        )
        emit_ui("status", key="robot", value="机器人通信异常", level="error")
        last_robot_write_outcome = "unknown"
        return False

def robot_read_register(display_addr):
    offset = display_addr - 40001
    with robot_client_lock:
        try:
            if not ensure_robot_connected():
                return None
            values = modbus_client_robot.read_holding_registers(offset, 1)
            return values[0] if values else None
        except Exception as e:
            print(f"⚠️ 读取机器人地址失败：{e}")
            if modbus_client_robot:
                modbus_client_robot.close()
            return None

def update_local(addr, val):
    try:
        if modbus_client_local is None or not modbus_client_local.write_single_register(addr, val):
            raise RuntimeError("本地寄存器写入未确认")
        return True
    except Exception as exc:
        local_state_fault_event.set()
        print(f"❌ 本地装配状态写入失败：{exc}")
        emit_ui("status", key="local_modbus", value="装配状态记录故障", level="error")
        return False

def read_local_step():
    try:
        if modbus_client_local is None:
            raise RuntimeError("本地状态客户端未初始化")
        r = modbus_client_local.read_holding_registers(0, 1)
        if not r:
            raise RuntimeError("本地装配状态读取无响应")
        return r[0]
    except Exception as exc:
        local_state_fault_event.set()
        print(f"❌ 本地装配状态读取失败：{exc}")
        emit_ui("status", key="local_modbus", value="装配状态读取故障", level="error")
        return None

def robot_read_batch_registers(start=30001, c=16):
    try:
        if not ensure_robot_connected():
            return None
        return modbus_client_robot.read_input_registers(start - 30001, c)
    except Exception as e:
        print(f"❌ 批量读取失败：{e}")
        return None

def robot_write_batch_registers(start, values):
    try:
        if not ensure_robot_connected():
            return False
        return modbus_client_robot.write_multiple_registers(start - 40001, values)
    except Exception as e:
        print(f"❌ 批量写入失败：{e}")
        return False

def update_modbus_register(addr, val):
    try:
        modbus_client_local.write_single_register(addr, val)
    except Exception as e:
        print(f"❌ 更新本地寄存器失败：{e}")

# ===================== 语音识别 =====================
def select_microphone():
    global selected_microphone_index
    devices = sd.query_devices()

    if MICROPHONE_INDEX is not None:
        if MICROPHONE_INDEX < 0 or MICROPHONE_INDEX >= len(devices):
            raise RuntimeError(f"麦克风索引{MICROPHONE_INDEX}无效")
        if devices[MICROPHONE_INDEX]["max_input_channels"] < 1:
            raise RuntimeError(f"设备{MICROPHONE_INDEX}不是输入设备")
        selected_microphone_index = MICROPHONE_INDEX
    else:
        default_input = int(sd.default.device[0])
        if default_input >= 0 and devices[default_input]["max_input_channels"] > 0:
            selected_microphone_index = default_input
        else:
            selected_microphone_index = next(
                (i for i, dev in enumerate(devices) if dev["max_input_channels"] > 0),
                None,
            )

    if selected_microphone_index is None:
        raise RuntimeError("未找到可用麦克风")

    device = devices[selected_microphone_index]
    print(f"✅ 麦克风：[{selected_microphone_index}] {device['name']}")
    emit_ui("status", key="microphone", value=device["name"], level="ok")
    return selected_microphone_index

def _pcm_rms(frame_bytes):
    samples = np.frombuffer(frame_bytes, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean((samples / 32768.0) ** 2)))

def record_utterance(
    listen_timeout,
    shutdown_event=None,
    interrupt_event=None,
    manual_stop_event=None,
    manual_mode=False,
):
    if tts_fault_event.is_set():
        emit_ui("status", key="speaker", value="语音播报故障，监听已锁止", level="error")
        emit_ui("voice_state", state="error", text="语音播报故障，监听已锁止")
        return None
    if selected_microphone_index is None:
        select_microphone()

    frame_samples = int(SAMPLING_RATE * FRAME_DURATION_MS / 1000)
    pre_roll_frames = max(1, int(PRE_ROLL_SEC * 1000 / FRAME_DURATION_MS))
    end_window_frames = max(1, int(END_SILENCE_SEC * 1000 / FRAME_DURATION_MS))
    min_speech_frames = max(1, int(MIN_SPEECH_SEC * 1000 / FRAME_DURATION_MS))
    vad = webrtcvad.Vad(VAD_MODE)
    pre_roll = deque(maxlen=pre_roll_frames)
    start_votes = deque(maxlen=5)
    end_votes = deque(maxlen=end_window_frames)
    noise_levels = deque(maxlen=100)
    speech_frames = []
    voiced_frames = 0
    triggered = False
    triggered_at = None
    started_at = time.monotonic()
    overflow_reported = False

    if audio_output_event.is_set():
        emit_ui("voice_state", state="recording", text="等待当前语音播报结束")
    audio_session_lock.acquire()
    audio_capture_event.set()
    print("\n🎙️ 请开始说话...")
    try:
        if manual_mode:
            emit_ui("voice_state", state="recording", text="正在录音，再次点击麦克风结束")
        else:
            emit_ui("voice_state", state="listening", text="正在聆听")
        with sd.RawInputStream(
            samplerate=SAMPLING_RATE,
            blocksize=frame_samples,
            device=selected_microphone_index,
            channels=1,
            dtype="int16",
        ) as stream:
            while True:
                if shutdown_event and shutdown_event.is_set():
                    return RECORD_INTERRUPTED
                if manual_mode and manual_stop_event and manual_stop_event.is_set():
                    print("ℹ️ 已收到手动结束录音请求")
                    break
                if interrupt_event and interrupt_event.is_set():
                    return RECORD_INTERRUPTED
                elapsed = time.monotonic() - started_at
                if not manual_mode and not triggered and elapsed >= listen_timeout:
                    print("ℹ️ 等待说话超时")
                    return None
                record_limit = MANUAL_MAX_RECORD_SEC if manual_mode else MAX_RECORD_SEC
                if len(speech_frames) * FRAME_DURATION_MS / 1000 >= record_limit:
                    print("ℹ️ 已达到单次录音最长时间")
                    break

                frame, overflowed = stream.read(frame_samples)
                frame_bytes = bytes(frame)
                if overflowed and not overflow_reported:
                    print("⚠️ 麦克风缓冲区发生溢出，已继续采集")
                    overflow_reported = True

                rms = _pcm_rms(frame_bytes)
                try:
                    vad_speech = vad.is_speech(frame_bytes, SAMPLING_RATE)
                except Exception:
                    vad_speech = False

                if not triggered and not vad_speech:
                    noise_levels.append(rms)

                noise_rms = float(np.median(noise_levels)) if noise_levels else 0.0
                energy_threshold = max(MIN_AUDIO_RMS, noise_rms * NOISE_RMS_RATIO)
                is_speech = vad_speech and rms >= energy_threshold
                display_level = min(1.0, rms / max(energy_threshold * 2.5, 0.008))
                emit_ui("audio_level", level=display_level, rms=rms, noise=noise_rms)

                if manual_mode:
                    speech_frames.append(frame_bytes)
                    if is_speech:
                        voiced_frames += 1
                        if not triggered:
                            triggered = True
                            emit_ui("voice_state", state="recording", text="正在录音，已检测到语音")
                    if len(speech_frames) % 8 == 0:
                        emit_ui(
                            "recording_progress",
                            duration=len(speech_frames) * FRAME_DURATION_MS / 1000,
                        )
                    continue

                pre_roll.append(frame_bytes)

                if not triggered:
                    start_votes.append(is_speech)
                    if len(start_votes) == start_votes.maxlen and sum(start_votes) >= 4:
                        triggered = True
                        triggered_at = time.monotonic()
                        speech_frames.extend(pre_roll)
                        voiced_frames = sum(start_votes)
                        end_votes.clear()
                        print(
                            f"🔊 检测到语音，环境噪声={noise_rms:.4f}，"
                            f"触发阈值={energy_threshold:.4f}"
                        )
                        emit_ui("voice_state", state="hearing", text="已检测到语音")
                    continue

                speech_frames.append(frame_bytes)
                end_votes.append(is_speech)
                if is_speech:
                    voiced_frames += 1

                enough_speech = voiced_frames >= min_speech_frames
                end_detected = (
                    len(end_votes) == end_votes.maxlen and sum(end_votes) <= 2
                )
                false_start = (
                    not enough_speech
                    and end_detected
                    and time.monotonic() - triggered_at >= FALSE_START_SEC
                )
                if false_start:
                    print("ℹ️ 已忽略短促环境噪声，继续等待说话")
                    triggered = False
                    triggered_at = None
                    speech_frames.clear()
                    voiced_frames = 0
                    pre_roll.clear()
                    start_votes.clear()
                    end_votes.clear()
                    continue
                if enough_speech and end_detected:
                    break
    except Exception as e:
        print(f"❌ 麦克风采集失败：{e}")
        emit_ui("status", key="microphone", value="麦克风采集异常", level="error")
        return None
    finally:
        audio_capture_event.clear()
        audio_session_lock.release()
        emit_ui("audio_level", level=0.0, rms=0.0, noise=0.0)

    if voiced_frames < min_speech_frames:
        print("ℹ️ 有效语音过短，已忽略")
        return None
    return b"".join(speech_frames)

def estimate_snr_db(audio):
    """Estimate speech-to-background ratio from short-time frame energy."""
    samples = np.asarray(audio, dtype=np.float32)
    frame_size = 512
    hop_size = 128
    if samples.size < frame_size:
        return 0.0

    powers = []
    for start in range(0, samples.size - frame_size + 1, hop_size):
        frame = samples[start:start + frame_size]
        powers.append(float(np.mean(frame ** 2)))
    if not powers:
        return 0.0

    noise_power = max(float(np.percentile(powers, 20)), 1e-10)
    speech_power = max(float(np.percentile(powers, 90)), noise_power)
    speech_only_power = max(speech_power - noise_power, 1e-10)
    return float(np.clip(10.0 * np.log10(speech_only_power / noise_power), -10.0, 40.0))

def spectral_denoise(audio):
    """Apply conservative stationary-noise suppression without altering timing."""
    samples = np.asarray(audio, dtype=np.float32)
    frame_size = 512
    hop_size = 128
    if samples.size < frame_size:
        return np.ascontiguousarray(samples, dtype=np.float32)

    frame_count = 1 + int(np.ceil((samples.size - frame_size) / hop_size))
    padded_size = (frame_count - 1) * hop_size + frame_size
    padded = np.pad(samples, (0, padded_size - samples.size))
    window = np.hanning(frame_size).astype(np.float32)
    spectra = np.empty((frame_count, frame_size // 2 + 1), dtype=np.complex64)
    frame_powers = np.empty(frame_count, dtype=np.float32)

    for index in range(frame_count):
        start = index * hop_size
        frame = padded[start:start + frame_size]
        frame_powers[index] = np.mean(frame ** 2)
        spectra[index] = np.fft.rfft(frame * window)

    quiet_count = max(3, int(np.ceil(frame_count * 0.2)))
    quiet_indices = np.argsort(frame_powers)[:quiet_count]
    noise_power = np.median(np.abs(spectra[quiet_indices]) ** 2, axis=0)
    spectrum_power = np.abs(spectra) ** 2
    gain = 1.0 - (0.9 * noise_power[None, :]) / np.maximum(spectrum_power, 1e-12)
    gain = np.clip(gain, 0.18, 1.0)

    if gain.shape[1] > 2:
        gain[:, 1:-1] = (
            gain[:, :-2] + 2.0 * gain[:, 1:-1] + gain[:, 2:]
        ) / 4.0
    for index in range(1, frame_count):
        gain[index] = 0.72 * gain[index - 1] + 0.28 * gain[index]

    frequencies = np.fft.rfftfreq(frame_size, 1.0 / SAMPLING_RATE)
    gain[:, (frequencies < 80.0) | (frequencies > 7600.0)] = 0.0
    cleaned_spectra = spectra * gain
    cleaned = np.zeros(padded_size, dtype=np.float32)
    window_sum = np.zeros(padded_size, dtype=np.float32)
    for index in range(frame_count):
        start = index * hop_size
        frame = np.fft.irfft(cleaned_spectra[index], n=frame_size).astype(np.float32)
        cleaned[start:start + frame_size] += frame * window
        window_sum[start:start + frame_size] += window ** 2

    valid = window_sum > 1e-6
    cleaned[valid] /= window_sum[valid]
    cleaned = cleaned[:samples.size]
    # The dry-signal blend protects consonants and accent-specific formants.
    cleaned = 0.84 * cleaned + 0.16 * samples
    return np.ascontiguousarray(cleaned, dtype=np.float32)

def prepare_audio(pcm_bytes):
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return None

    audio -= float(np.mean(audio))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < MIN_AUDIO_RMS:
        print(f"ℹ️ 音量过低（RMS={rms:.4f}），已忽略")
        return None

    audio = spectral_denoise(audio)
    cleaned_rms = float(np.sqrt(np.mean(audio ** 2)))
    gain = min(MAX_AUDIO_GAIN, max(0.5, TARGET_AUDIO_RMS / max(cleaned_rms, 1e-8)))
    audio *= gain
    peak = float(np.max(np.abs(audio)))
    if peak > 0.98:
        audio *= 0.98 / peak

    fade_samples = min(int(SAMPLING_RATE * 0.01), audio.size // 2)
    if fade_samples > 0:
        fade = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
        audio[:fade_samples] *= fade
        audio[-fade_samples:] *= fade[::-1]
    return np.ascontiguousarray(audio, dtype=np.float32)

def _decode_recognition_metrics(result):
    segments = result.get("segments") or []
    if not segments:
        return {"confidence": None, "no_speech_probability": None}

    weights = []
    log_probabilities = []
    no_speech_probabilities = []
    for segment in segments:
        duration = max(0.1, float(segment.get("end", 0)) - float(segment.get("start", 0)))
        weights.append(duration)
        log_probabilities.append(float(segment.get("avg_logprob", -5.0)))
        no_speech_probabilities.append(float(segment.get("no_speech_prob", 1.0)))
    average_log_probability = float(np.average(log_probabilities, weights=weights))
    no_speech_probability = float(np.average(no_speech_probabilities, weights=weights))
    confidence = float(
        np.clip(
            np.exp(np.clip(average_log_probability, -5.0, 0.0))
            * (1.0 - no_speech_probability),
            0.0,
            1.0,
        )
    )
    return {
        "confidence": confidence,
        "no_speech_probability": no_speech_probability,
    }

def transcribe_audio(audio, initial_prompt=WHISPER_INITIAL_PROMPT, return_details=False):
    use_fp16 = getattr(whisper_model, "device", None) is not None
    use_fp16 = use_fp16 and whisper_model.device.type == "cuda"
    result = whisper_model.transcribe(
        audio,
        language="zh",
        task="transcribe",
        temperature=0.0,
        beam_size=5,
        patience=1.2,
        no_speech_threshold=0.6,
        logprob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        condition_on_previous_text=False,
        initial_prompt=initial_prompt,
        fp16=use_fp16,
        without_timestamps=True,
        verbose=None,
    )

    text = converter.convert(result.get("text", "").strip())
    text = re.sub(r"\s+", "", text)
    if not return_details:
        return text
    return {"text": text, **_decode_recognition_metrics(result)}

def listen_and_recognize(
    listen_timeout=COMMAND_LISTEN_TIMEOUT,
    initial_prompt=WHISPER_INITIAL_PROMPT,
    shutdown_event=None,
    interrupt_event=None,
    manual_stop_event=None,
    manual_mode=False,
    audible_prompt=False,
    return_details=False,
):
    try:
        if audible_prompt:
            play_feedback_tone("listen")
        pcm_bytes = record_utterance(
            listen_timeout,
            shutdown_event=shutdown_event,
            interrupt_event=interrupt_event,
            manual_stop_event=manual_stop_event,
            manual_mode=manual_mode,
        )
        if pcm_bytes is RECORD_INTERRUPTED:
            return RECORD_INTERRUPTED
        if not pcm_bytes:
            return ""
        raw_audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        raw_audio -= float(np.mean(raw_audio))
        snr_db = estimate_snr_db(raw_audio)
        duration = len(raw_audio) / SAMPLING_RATE
        audio = prepare_audio(pcm_bytes)
        if audio is None:
            return ""

        play_feedback_tone("captured")
        emit_ui("voice_state", state="recognizing", text="录音已结束，正在识别")
        decoded = transcribe_audio(audio, initial_prompt=initial_prompt, return_details=True)
        text = decoded["text"]
        details = {
            **decoded,
            "snr_db": snr_db,
            "duration": duration,
        }
        emit_ui("recognition_metrics", **details)
        if not text:
            print("ℹ️ 未识别到有效内容")
            return ""
        print(f"✅ 识别：【{text}】")
        emit_ui("transcript", text=text)
        return details if return_details else text
    except Exception as e:
        print(f"❌ 识别异常：{e}")
        emit_ui("voice_state", state="error", text="识别失败")
        play_feedback_tone("error")
        return ""

# =====================同音修正 =====================
def fix_common_errors(text):
    text = converter.convert(text)
    text = re.sub(r"[\s，。！？、,.!?：:；;]", "", text)
    replacements = (
        ("示例运输", "示例搬运"), ("示例校准", "示例定位"),
        ("示例检验", "示例检测"), ("示例组装", "示例装配"),
        ("示例归位", "示例复位"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return text

def _best_window_similarity(text, phrase):
    if phrase in text:
        return 1.0
    best = 0.0
    min_size = max(1, len(phrase) - 1)
    max_size = min(len(text), len(phrase) + 1)
    for size in range(min_size, max_size + 1):
        for start in range(0, len(text) - size + 1):
            score = SequenceMatcher(None, text[start:start + size], phrase).ratio()
            best = max(best, score)
    return best

def is_wake_phrase(text):
    normalized = fix_common_errors(text)
    for wake_word in WAKE_UP_WORDS:
        wake_normalized = fix_common_errors(wake_word)
        if wake_normalized in normalized:
            return True
        if _best_window_similarity(normalized, wake_normalized) >= 0.75:
            return True
    return False

def strip_wake_phrase(text):
    normalized = fix_common_errors(text)
    best_start = None
    best_end = None
    best_score = 0.0
    for wake_word in WAKE_UP_WORDS:
        wake = fix_common_errors(wake_word)
        direct_start = normalized.find(wake)
        if direct_start >= 0:
            return normalized[:direct_start] + normalized[direct_start + len(wake):]
        for start in range(0, max(1, len(normalized) - len(wake) + 2)):
            segment = normalized[start:start + len(wake)]
            score = SequenceMatcher(None, segment, wake).ratio()
            if score > best_score:
                best_score = score
                best_start = start
                best_end = start + len(wake)
    if best_score >= 0.75 and best_start is not None:
        return normalized[:best_start] + normalized[best_end:]
    return normalized

def detect_wake(shutdown_event=None, interrupt_event=None):
    print("\n📡 等待唤醒：你好同学")
    emit_ui("voice_state", state="waiting", text="等待唤醒：你好同学")
    result = listen_and_recognize(
        WAKE_LISTEN_TIMEOUT,
        WHISPER_WAKE_PROMPT,
        shutdown_event=shutdown_event,
        interrupt_event=interrupt_event,
        return_details=True,
    )
    if result is RECORD_INTERRUPTED:
        return RECORD_INTERRUPTED
    if not result:
        return ""
    text = result["text"]
    if not is_wake_phrase(text):
        return ""
    print("🔔 已唤醒！")
    return result

def classify_robot_command(text):
    normalized = fix_common_errors(text)
    candidates = []
    for command in COMMANDS:
        object_score = max(
            _best_window_similarity(normalized, obj)
            for obj in command["objects"]
        )
        phrase_score = max(
            _best_window_similarity(normalized, phrase)
            for phrase in command["words"]
        )
        score = max(object_score, phrase_score)
        if score >= 0.78:
            candidates.append((score, command))

    if not candidates:
        return "none", None

    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.08:
        print("⚠️ 指令对象不明确，已阻止执行")
        return "ambiguous", None

    command = candidates[0][1]
    if any(word in normalized for word in NEGATIVE_WORDS):
        return "cancel", command
    if any(word in normalized for word in QUESTION_WORDS):
        return "none", None
    if not any(word in normalized for word in ACTION_WORDS):
        return "none", None
    return "execute", command

def execute_robot_command(matched, recognition_details=None):
    recognition_details = recognition_details or {}
    confidence = recognition_details.get("confidence")
    snr_db = recognition_details.get("snr_db")
    quality_reasons = []
    if recognition_details and confidence is None:
        quality_reasons.append("识别置信度不可用")
    elif confidence is not None and confidence < MIN_ROBOT_ASR_CONFIDENCE:
        quality_reasons.append("识别置信度偏低")
    if recognition_details and snr_db is None:
        quality_reasons.append("环境质量不可用")
    elif snr_db is not None and snr_db < MIN_ROBOT_SNR_DB:
        quality_reasons.append("环境信噪比偏低")
    if quality_reasons:
        reason = "、".join(quality_reasons)
        emit_ui(
            "command_gate",
            text=f"{reason}，指令未下发",
            level="warning",
            command=matched["desc"],
            register=ROBOT_COMMAND_REGISTER_LABEL,
            value=str(matched["value"]),
        )
        print(f"⚠️ {reason}，已阻止机器人动作")
        play_feedback_tone("error")
        speak("刚才的语音不够清楚，我没有执行。请靠近麦克风，再说一次。")
        return "quality_blocked"

    v = matched["value"]
    name = matched["desc"]
    if not robot_command_lock.acquire(blocking=False):
        emit_ui(
            "command_gate",
            text="另一条指令正在下发，请稍候",
            level="warning",
            command=name,
            register=ROBOT_COMMAND_REGISTER_LABEL,
            value=str(v),
        )
        play_feedback_tone("error")
        speak("另一条指令正在下发。请稍候再试。")
        return "busy"

    result = "not_sent"
    spoken_feedback = ""
    feedback_tone = "error"
    try:
        print(f"🚀 准备单次下发指令：{name}")
        emit_ui(
            "command_gate",
            text="指令已放行，正在执行 Modbus 单次写入",
            level="checking",
            command=name,
            register=ROBOT_COMMAND_REGISTER_LABEL,
            value=str(v),
        )
        emit_ui("current_action", text=f"正在下发：{name}", level="active")
        if not robot_write_register(ROBOT_COMMAND_REGISTER, v):
            if last_robot_write_outcome == "unknown":
                emit_ui(
                    "command_gate",
                    text="指令状态不明，禁止重试，请人工检查",
                    level="locked",
                    command=name,
                    register=ROBOT_COMMAND_REGISTER_LABEL,
                    value=str(v),
                )
                emit_ui("current_action", text=f"状态不明：{name}", level="error")
                spoken_feedback = "机器人没有确认这条指令的状态。我不会自动重试，请人工检查现场。"
                result = "unknown"
            else:
                emit_ui(
                    "command_gate",
                    text="机器人未连接，指令未发送",
                    level="locked",
                    command=name,
                    register=ROBOT_COMMAND_REGISTER_LABEL,
                    value=str(v),
                )
                emit_ui("current_action", text=f"未下发：{name}", level="error")
                spoken_feedback = "机器人当前没有连接。这条指令没有发送。"
                result = "not_sent"
        else:
            print(f"✅ 单向指令已下发并释放任务状态：{name}")
            emit_ui(
                "command_gate",
                text="单次写入已确认，任务状态已释放",
                level="active",
                command=name,
                register=ROBOT_COMMAND_REGISTER_LABEL,
                value=str(v),
            )
            emit_ui("current_action", text=f"最近下发：{name}", level="ok")
            result = "sent"
            spoken_feedback = f"好的，{name}指令已经下发。"
            feedback_tone = "sent"
    finally:
        robot_command_lock.release()

    play_feedback_tone(feedback_tone)
    speak(spoken_feedback)
    return result

def send_robot_command_value(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("机器人指令值必须是整数")
    matched = next((command for command in COMMANDS if command["value"] == value), None)
    if matched is None:
        raise ValueError("不支持的机器人指令")
    return execute_robot_command(matched), matched

def process_command(text, recognition_details=None):
    status, matched = classify_robot_command(text)
    if status == "cancel":
        emit_ui(
            "command_gate",
            text="已识别取消语义，禁止下发",
            level="locked",
            command="已取消",
            register="--",
            value="--",
        )
        print("🛑 检测到否定或取消指令，不执行机器人动作")
        play_feedback_tone("cancel")
        speak("好的，已取消。机器人不会执行这条指令。")
        return True
    if status == "ambiguous":
        emit_ui(
            "command_gate",
            text="动作对象存在歧义，请重新说",
            level="warning",
            command="需要复述",
            register="--",
            value="--",
        )
        play_feedback_tone("error")
        speak("我还不能确定具体动作。请重新说出完整指令，例如，执行示例搬运。")
        return True
    if status != "execute" or matched is None:
        return False
    execute_robot_command(matched, recognition_details=recognition_details)
    return True

def chat_with_ai(prompt):
    try:
        response = ollama_client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "你是工业机器人现场语音助手。使用简洁中文回答，通常不超过三句话。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        return response["message"]["content"]
    except Exception as e:
        print(f"❌ AI对话失败：{e}")
        return "AI服务未启动"

def init_ollama_client():
    global ollama_client
    try:
        # The machine uses a system proxy for network routing. Local Ollama
        # requests must bypass it or httpx can return a proxy-generated 502.
        ollama_client = ollama.Client(
            host=OLLAMA_HOST,
            trust_env=False,
            timeout=120,
        )
        ollama_client.show(OLLAMA_MODEL)
        print(f"✅ Ollama模型可用：{OLLAMA_MODEL}")
        emit_ui("status", key="ollama", value=f"{OLLAMA_MODEL} 就绪", level="ok")
        return True
    except Exception as e:
        print(f"❌ Ollama服务或模型不可用：{e}")
        emit_ui("status", key="ollama", value="AI服务异常", level="error")
        ollama_client = None
        return False

# ===================== 主程序 =====================
def initialize_system(require_robot=True, require_ollama=True):
    emit_ui("voice_state", state="initializing", text="正在初始化音频设备")
    try:
        select_microphone()
        init_tts_engine()
    except Exception as e:
        print(f"❌ 音频设备初始化失败：{e}")
        return 2

    ollama_ready = init_ollama_client()
    if require_ollama and not ollama_ready:
        return 3
    local_service_ready = start_modbus_server()
    local_state_ready = local_service_ready and init_modbus_client_local()
    if not local_state_ready:
        local_state_fault_event.set()
        print("⚠️ 工艺参考状态服务不可用，不影响机器人固定指令下发")
        emit_ui(
            "status",
            key="local_modbus",
            value="工艺参考状态不可用 · 不参与联锁",
            level="error",
        )
    robot_connected = init_modbus_client_robot()
    if require_robot and not robot_connected:
        print("❌ 为防止误动作，机器人未连接时程序不会进入语音控制")
        return 6

    global whisper_model
    try:
        print(f"⏳ 正在加载Whisper模型：{WHISPER_MODEL}")
        emit_ui("voice_state", state="initializing", text=f"正在加载语音模型 {WHISPER_MODEL}")
        whisper_model = whisper.load_model(WHISPER_MODEL)
        print(f"✅ Whisper模型已加载，运行设备：{whisper_model.device}")
        emit_ui(
            "status",
            key="recognition",
            value=f"Whisper {WHISPER_MODEL} · {str(whisper_model.device).upper()}",
            level="ok",
        )
    except Exception as e:
        print(f"❌ Whisper模型加载失败：{e}")
        emit_ui("status", key="recognition", value="语音模型加载失败", level="error")
        return 7
    return 0

def handle_recognized_text(text, mode, recognition_details=None):
    normalized = fix_common_errors(text)
    emit_ui("message", role="user", text=text)
    if any(word in normalized for word in ("退出", "结束对话", "暂停监听")):
        speak("好的，语音监听已经暂停。")
        return "pause"

    if mode == "robot":
        emit_ui("voice_state", state="thinking", text="正在解析指令与安全联锁")
        if not process_command(text, recognition_details=recognition_details):
            emit_ui(
                "command_gate",
                text="未形成明确动作，指令未下发",
                level="warning",
                command="需要复述",
                register="--",
                value="--",
            )
            play_feedback_tone("error")
            speak("我听到了，但还不能确定具体动作。请再说一次完整指令。")
        return "handled"

    emit_ui(
        "command_gate",
        text="AI 对话模式，不会下发机器人动作",
        level="locked",
        command="对话回复",
        register="--",
        value="--",
    )
    emit_ui("voice_state", state="thinking", text="AI 正在思考")
    reply = chat_with_ai(text)
    print(f"🤖 {reply}")
    speak(reply)
    return "handled"

def voice_control_loop(
    shutdown_event,
    auto_listen_event,
    manual_listen_event,
    manual_stop_event,
    mode_getter,
):
    while not shutdown_event.is_set():
        if tts_fault_event.is_set():
            manual_listen_event.clear()
            manual_stop_event.clear()
            emit_ui("manual_recording", active=False)
            emit_ui("voice_state", state="error", text="语音播报故障，监听已锁止，请重启程序")
            shutdown_event.wait(0.5)
            continue
        if manual_listen_event.is_set():
            manual_listen_event.clear()
            mode = mode_getter()
            prompt = WHISPER_COMMAND_PROMPT if mode == "robot" else WHISPER_INITIAL_PROMPT
            emit_ui("manual_recording", active=True)
            try:
                recognized = listen_and_recognize(
                    COMMAND_LISTEN_TIMEOUT,
                    prompt,
                    shutdown_event=shutdown_event,
                    manual_stop_event=manual_stop_event,
                    manual_mode=True,
                    audible_prompt=True,
                    return_details=True,
                )
            finally:
                emit_ui("manual_recording", active=False)
                manual_stop_event.clear()
            if recognized and recognized is not RECORD_INTERRUPTED:
                result = handle_recognized_text(
                    recognized["text"],
                    mode,
                    recognition_details=recognized,
                )
                if result == "pause":
                    auto_listen_event.clear()
            elif recognized is not RECORD_INTERRUPTED:
                emit_ui(
                    "command_gate",
                    text="未检测到足够语音，请重新录制",
                    level="warning",
                    command="需要复述",
                    register="--",
                    value="--",
                )
                play_feedback_tone("error")
                speak("这次录音里没有检测到清楚的语音。请重新录制。")
            continue

        if not auto_listen_event.is_set():
            if audio_output_event.is_set():
                manual_listen_event.wait(0.1)
                continue
            emit_ui("voice_state", state="paused", text="监听已暂停")
            manual_listen_event.wait(0.2)
            continue

        wake_result = detect_wake(
            shutdown_event=shutdown_event,
            interrupt_event=manual_listen_event,
        )
        if wake_result is RECORD_INTERRUPTED:
            continue
        if not wake_result:
            continue

        emit_ui("message", role="user", text="你好同学")
        remainder = strip_wake_phrase(wake_result["text"])
        mode = mode_getter()
        if remainder:
            handle_recognized_text(remainder, mode, recognition_details=wake_result)
            continue

        speak(WAKE_UP_RESPONSE)
        prompt = WHISPER_COMMAND_PROMPT if mode == "robot" else WHISPER_INITIAL_PROMPT
        command_result = listen_and_recognize(
            COMMAND_LISTEN_TIMEOUT,
            prompt,
            shutdown_event=shutdown_event,
            interrupt_event=manual_listen_event,
            audible_prompt=True,
            return_details=True,
        )
        if command_result is RECORD_INTERRUPTED:
            continue
        if not command_result:
            play_feedback_tone("error")
            speak("我没有听清。请靠近麦克风，再说一次。")
            continue
        result = handle_recognized_text(
            command_result["text"],
            mode,
            recognition_details=command_result,
        )
        if result == "pause":
            auto_listen_event.clear()

class VoiceAssistantApp:
    COLORS = {
        "bg": "#F2F5F4",
        "surface": "#FFFFFF",
        "text": "#18221F",
        "muted": "#68746F",
        "line": "#DCE4E1",
        "green": "#1B8A68",
        "green_soft": "#E6F4EF",
        "blue": "#2F6FED",
        "blue_soft": "#EAF0FD",
        "amber": "#D88416",
        "amber_soft": "#FFF3DC",
        "red": "#C53F3F",
        "red_soft": "#FCECEC",
        "charcoal": "#23302C",
    }

    def __init__(self, start_backend=True):
        global ui_event_callback
        self.root = tk.Tk()
        self.root.title("工业机器人语音控制示例")
        self.root.geometry("1080x720")
        self.root.minsize(900, 620)
        self.root.configure(bg=self.COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.event_queue = queue.Queue()
        self.shutdown_event = threading.Event()
        self.auto_listen_event = threading.Event()
        self.manual_listen_event = threading.Event()
        self.manual_stop_event = threading.Event()
        self.mode_lock = threading.Lock()
        self.mode = "robot"
        self.ready = False
        self.manual_recording = False
        self.closing = False
        self.audio_target = 0.0
        self.audio_level = 0.0
        self.voice_state = "initializing"
        self.wave_phase = 0
        self.status_widgets = {}
        self.messages = []
        ui_event_callback = self.post_event

        self._configure_styles()
        self._build_ui()
        self.root.after(40, self._drain_events)
        self.root.after(50, self._animate_waveform)
        self.backend_thread = None
        if start_backend:
            self.backend_thread = threading.Thread(
                target=self._backend_worker,
                daemon=True,
                name="voice-backend",
            )
            self.backend_thread.start()

    def _configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Voice.Vertical.TScrollbar",
            background="#C7D1CD",
            troughcolor=self.COLORS["surface"],
            bordercolor=self.COLORS["surface"],
            arrowcolor=self.COLORS["muted"],
        )

    def _build_ui(self):
        header = tk.Frame(self.root, bg=self.COLORS["surface"], height=78)
        header.pack(fill="x")
        header.pack_propagate(False)
        title_box = tk.Frame(header, bg=self.COLORS["surface"])
        title_box.pack(side="left", padx=28, pady=16)
        tk.Label(
            title_box,
            text="工业机器人语音控制示例",
            font=("Microsoft YaHei UI", 19, "bold"),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="语音识别 · 即时反馈 · Modbus TCP",
            font=("Microsoft YaHei UI", 9),
            bg=self.COLORS["surface"],
            fg=self.COLORS["muted"],
        ).pack(anchor="w", pady=(2, 0))

        mode_box = tk.Frame(header, bg="#E9EEEC", padx=3, pady=3)
        mode_box.pack(side="right", padx=28)
        self.robot_mode_button = self._mode_button(mode_box, "机器人控制", "robot")
        self.robot_mode_button.pack(side="left")
        self.chat_mode_button = self._mode_button(mode_box, "AI 对话", "chat")
        self.chat_mode_button.pack(side="left")
        self._refresh_mode_buttons()

        status_bar = tk.Frame(self.root, bg=self.COLORS["bg"], height=70)
        status_bar.pack(fill="x", padx=24, pady=(14, 8))
        status_bar.pack_propagate(False)
        for column in range(4):
            status_bar.grid_columnconfigure(column, weight=1, uniform="status")
        for column, (key, title) in enumerate((
            ("robot", "工业机器人"),
            ("microphone", "麦克风"),
            ("recognition", "语音识别"),
            ("ollama", "本地大模型"),
        )):
            self._make_status_item(status_bar, key, title).grid(
                row=0, column=column, sticky="nsew", padx=4
            )

        body = tk.Frame(self.root, bg=self.COLORS["bg"])
        body.pack(fill="both", expand=True, padx=28, pady=(0, 22))
        body.grid_columnconfigure(0, weight=1, minsize=500)
        body.grid_columnconfigure(1, weight=0, minsize=260)
        body.grid_rowconfigure(0, weight=1)

        conversation = tk.Frame(
            body,
            bg=self.COLORS["surface"],
            highlightthickness=1,
            highlightbackground=self.COLORS["line"],
        )
        conversation.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.conversation_frame = conversation
        conversation.grid_rowconfigure(1, weight=1)
        conversation.grid_columnconfigure(0, weight=1)

        conversation_header = tk.Frame(conversation, bg=self.COLORS["surface"], height=52)
        conversation_header.grid(row=0, column=0, sticky="ew", padx=18)
        conversation_header.pack_propagate(False)
        tk.Label(
            conversation_header,
            text="语音会话",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
        ).pack(side="left", pady=15)
        self.speaker_label = tk.Label(
            conversation_header,
            text="播报：中文语音待机",
            font=("Microsoft YaHei UI", 9),
            bg=self.COLORS["green_soft"],
            fg=self.COLORS["green"],
            padx=10,
            pady=4,
        )
        self.speaker_label.pack(side="right", pady=12)

        chat_host = tk.Frame(conversation, bg="#F8FAF9")
        chat_host.grid(row=1, column=0, sticky="nsew")
        chat_host.grid_rowconfigure(0, weight=1)
        chat_host.grid_columnconfigure(0, weight=1)
        self.chat_canvas = tk.Canvas(
            chat_host,
            bg="#F8FAF9",
            highlightthickness=0,
            bd=0,
        )
        scrollbar = ttk.Scrollbar(
            chat_host,
            orient="vertical",
            command=self.chat_canvas.yview,
            style="Voice.Vertical.TScrollbar",
        )
        self.chat_canvas.configure(yscrollcommand=scrollbar.set)
        self.chat_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.chat_frame = tk.Frame(self.chat_canvas, bg="#F8FAF9")
        self.chat_window = self.chat_canvas.create_window(
            (0, 0), window=self.chat_frame, anchor="nw"
        )
        self.chat_frame.bind("<Configure>", self._on_chat_configure)
        self.chat_canvas.bind("<Configure>", self._on_chat_canvas_configure)

        controls = tk.Frame(conversation, bg=self.COLORS["surface"], height=150)
        self.controls_frame = controls
        controls.grid(row=2, column=0, sticky="ew")
        controls.grid_columnconfigure(1, weight=1)
        controls.grid_propagate(False)

        state_box = tk.Frame(controls, bg=self.COLORS["surface"])
        state_box.grid(row=0, column=0, rowspan=2, padx=(20, 14), pady=16)
        self.state_dot = tk.Canvas(
            state_box, width=16, height=16, bg=self.COLORS["surface"], highlightthickness=0
        )
        self.state_dot.pack(side="left", padx=(0, 8))
        self.state_dot_id = self.state_dot.create_oval(3, 3, 13, 13, fill=self.COLORS["amber"], outline="")
        self.state_label = tk.Label(
            state_box,
            text="正在初始化",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
        )
        self.state_label.pack(side="left")

        self.wave_canvas = tk.Canvas(
            controls,
            width=210,
            height=66,
            bg=self.COLORS["surface"],
            highlightthickness=0,
        )
        self.wave_canvas.grid(row=0, column=1, sticky="ew", padx=8, pady=(13, 0))
        self.wave_bars = [
            self.wave_canvas.create_rectangle(0, 0, 0, 0, fill=self.COLORS["green"], outline="")
            for _ in range(30)
        ]
        self.transcript_label = tk.Label(
            controls,
            text="等待语音输入",
            font=("Microsoft YaHei UI", 9),
            bg=self.COLORS["surface"],
            fg=self.COLORS["muted"],
            anchor="w",
        )
        self.transcript_label.grid(row=1, column=1, sticky="ew", padx=14, pady=(0, 10))

        buttons = tk.Frame(controls, bg=self.COLORS["surface"])
        buttons.grid(row=0, column=2, rowspan=2, padx=(12, 20), pady=14)
        self.talk_button = self._action_button(
            buttons,
            "点击说话",
            self.request_manual_listen,
            self.COLORS["green"],
            "#FFFFFF",
            width=12,
        )
        self.talk_button.pack(fill="x", pady=(0, 7))
        self.auto_button = self._action_button(
            buttons,
            "暂停唤醒",
            self.toggle_auto_listen,
            "#E8EEEC",
            self.COLORS["charcoal"],
            width=12,
        )
        self.auto_button.pack(fill="x")
        self.talk_button.configure(state="disabled")
        self.auto_button.configure(state="disabled")

        sidebar = tk.Frame(
            body,
            bg=self.COLORS["surface"],
            highlightthickness=1,
            highlightbackground=self.COLORS["line"],
        )
        sidebar.grid(row=0, column=1, sticky="nsew")
        self.sidebar_frame = sidebar
        tk.Label(
            sidebar,
            text="当前任务",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
        ).pack(anchor="w", padx=20, pady=(20, 8))
        self.action_label = tk.Label(
            sidebar,
            text="等待机器人指令",
            font=("Microsoft YaHei UI", 11),
            bg=self.COLORS["green_soft"],
            fg=self.COLORS["green"],
            anchor="w",
            justify="left",
            wraplength=190,
            padx=14,
            pady=13,
        )
        self.action_label.pack(fill="x", padx=20)

        tk.Frame(sidebar, height=1, bg=self.COLORS["line"]).pack(fill="x", padx=20, pady=18)
        tk.Label(
            sidebar,
            text="可识别任务",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
        ).pack(anchor="w", padx=20)
        for command in (item["desc"] for item in COMMANDS):
            row = tk.Frame(sidebar, bg=self.COLORS["surface"])
            row.pack(fill="x", padx=20, pady=5)
            dot = tk.Canvas(row, width=12, height=12, bg=self.COLORS["surface"], highlightthickness=0)
            dot.create_oval(3, 3, 9, 9, fill=self.COLORS["blue"], outline="")
            dot.pack(side="left", padx=(0, 8))
            tk.Label(
                row,
                text=command,
                font=("Microsoft YaHei UI", 9),
                bg=self.COLORS["surface"],
                fg=self.COLORS["text"],
            ).pack(side="left")

        utilities = tk.Frame(sidebar, bg=self.COLORS["surface"])
        utilities.pack(side="bottom", fill="x", padx=20, pady=20)
        self._action_button(
            utilities,
            "试听播报",
            self.test_sound,
            "#EAF0FD",
            self.COLORS["blue"],
        ).pack(fill="x", pady=(0, 7))
        self._action_button(
            utilities,
            "重新连接机器人",
            self.reconnect_robot,
            "#F1F3F2",
            self.COLORS["charcoal"],
        ).pack(fill="x")

    def _mode_button(self, parent, text, mode):
        return tk.Button(
            parent,
            text=text,
            command=lambda: self.set_mode(mode),
            font=("Microsoft YaHei UI", 9, "bold"),
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
        )

    def _action_button(self, parent, text, command, bg, fg, width=None):
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            relief="flat",
            bd=0,
            padx=13,
            pady=9,
            width=width,
            cursor="hand2",
        )

    def _make_status_item(self, parent, key, title):
        item = tk.Frame(
            parent,
            bg=self.COLORS["surface"],
            highlightthickness=1,
            highlightbackground=self.COLORS["line"],
        )
        dot = tk.Canvas(item, width=18, height=18, bg=self.COLORS["surface"], highlightthickness=0)
        dot.pack(side="left", padx=(13, 8), pady=12)
        dot_id = dot.create_oval(4, 4, 14, 14, fill=self.COLORS["amber"], outline="")
        labels = tk.Frame(item, bg=self.COLORS["surface"])
        labels.pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(
            labels,
            text=title,
            font=("Microsoft YaHei UI", 8),
            bg=self.COLORS["surface"],
            fg=self.COLORS["muted"],
        ).pack(anchor="w")
        value_label = tk.Label(
            labels,
            text="正在检查",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
            anchor="w",
        )
        value_label.pack(anchor="w")
        self.status_widgets[key] = (dot, dot_id, value_label)
        return item

    def _on_chat_configure(self, _event=None):
        self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all"))

    def _on_chat_canvas_configure(self, event):
        self.chat_canvas.itemconfigure(self.chat_window, width=event.width)

    def add_message(self, role, text):
        row = tk.Frame(self.chat_frame, bg="#F8FAF9")
        row.pack(fill="x", padx=16, pady=6)
        is_user = role == "user"
        bubble_bg = self.COLORS["blue"] if is_user else self.COLORS["surface"]
        bubble_fg = "#FFFFFF" if is_user else self.COLORS["text"]
        bubble = tk.Frame(
            row,
            bg=bubble_bg,
            highlightthickness=1 if not is_user else 0,
            highlightbackground=self.COLORS["line"],
        )
        bubble.pack(side="right" if is_user else "left", padx=(80, 0) if is_user else (0, 80))
        tk.Label(
            bubble,
            text=text,
            font=("Microsoft YaHei UI", 10),
            bg=bubble_bg,
            fg=bubble_fg,
            justify="left",
            wraplength=360,
            padx=13,
            pady=9,
        ).pack()
        self.messages.append(row)
        if len(self.messages) > 80:
            self.messages.pop(0).destroy()
        self.root.after(20, lambda: self.chat_canvas.yview_moveto(1.0))

    def post_event(self, event):
        self.event_queue.put(event)

    def _drain_events(self):
        latest_audio = None
        try:
            while True:
                event = self.event_queue.get_nowait()
                if event["type"] == "audio_level":
                    latest_audio = event
                else:
                    self._apply_event(event)
        except queue.Empty:
            pass
        if latest_audio:
            self.audio_target = latest_audio.get("level", 0.0)
        if not self.closing:
            self.root.after(40, self._drain_events)

    def _apply_event(self, event):
        event_type = event["type"]
        if event_type == "status":
            self._update_status(event["key"], event["value"], event.get("level", "ok"))
        elif event_type == "message":
            self.add_message(event.get("role", "assistant"), event.get("text", ""))
        elif event_type == "voice_state":
            self._set_voice_state(event.get("state", "waiting"), event.get("text", ""))
        elif event_type == "transcript":
            self.transcript_label.configure(text=f"识别结果：{event.get('text', '')}")
        elif event_type == "manual_recording":
            self.manual_recording = bool(event.get("active"))
            self.talk_button.configure(
                text="结束并识别" if self.manual_recording else "点击说话",
                state="normal" if self.ready else "disabled",
            )
        elif event_type == "sound":
            self.speaker_label.configure(text=f"播报：{event.get('label', '中文语音待机')}")
        elif event_type == "current_action":
            level = event.get("level", "active")
            bg = self.COLORS["red_soft"] if level == "error" else self.COLORS["green_soft"]
            fg = self.COLORS["red"] if level == "error" else self.COLORS["green"]
            self.action_label.configure(text=event.get("text", ""), bg=bg, fg=fg)
        elif event_type == "backend_ready":
            self.ready = True
            self.talk_button.configure(state="normal")
            self.auto_button.configure(state="normal")
            if START_PAUSED:
                self.auto_button.configure(text="开始唤醒")
                self._set_voice_state("paused", "监听已暂停")
            else:
                self.auto_listen_event.set()
                self.auto_button.configure(text="暂停唤醒")
                self._set_voice_state("waiting", "等待唤醒：你好同学")
        elif event_type == "backend_failed":
            self._set_voice_state("error", event.get("text", "初始化失败"))
            self.add_message("assistant", event.get("text", "初始化失败"))

    def _update_status(self, key, value, level):
        if key not in self.status_widgets:
            return
        dot, dot_id, label = self.status_widgets[key]
        color = {
            "ok": self.COLORS["green"],
            "error": self.COLORS["red"],
            "active": self.COLORS["blue"],
        }.get(level, self.COLORS["amber"])
        dot.itemconfigure(dot_id, fill=color)
        label.configure(text=value)

    def _set_voice_state(self, state, text):
        self.voice_state = state
        color = {
            "waiting": self.COLORS["green"],
            "listening": self.COLORS["blue"],
            "hearing": self.COLORS["green"],
            "recording": self.COLORS["red"],
            "stopping": self.COLORS["amber"],
            "recognizing": self.COLORS["blue"],
            "thinking": self.COLORS["amber"],
            "speaking": self.COLORS["amber"],
            "paused": self.COLORS["muted"],
            "error": self.COLORS["red"],
            "initializing": self.COLORS["amber"],
        }.get(state, self.COLORS["green"])
        self.state_dot.itemconfigure(self.state_dot_id, fill=color)
        self.state_label.configure(text=text or state)

    def _animate_waveform(self):
        self.audio_level += (self.audio_target - self.audio_level) * 0.28
        self.audio_target *= 0.92
        width = max(300, self.wave_canvas.winfo_width())
        height = max(50, self.wave_canvas.winfo_height())
        count = len(self.wave_bars)
        gap = 5
        bar_width = max(3, (width - gap * (count - 1)) / count)
        self.wave_phase += 1
        active = self.voice_state in ("recognizing", "thinking", "speaking")
        color = self.COLORS["amber"] if self.voice_state == "speaking" else self.COLORS["green"]
        for index, bar in enumerate(self.wave_bars):
            wave = (np.sin((index + self.wave_phase * 0.55) * 0.7) + 1.0) / 2.0
            activity = max(self.audio_level, 0.12 + wave * 0.34 if active else 0.06)
            shape = 0.55 + 0.45 * np.sin((index / max(1, count - 1)) * np.pi)
            bar_height = max(4, min(height - 8, height * activity * shape))
            x1 = index * (bar_width + gap)
            y1 = (height - bar_height) / 2
            self.wave_canvas.coords(bar, x1, y1, x1 + bar_width, y1 + bar_height)
            self.wave_canvas.itemconfigure(bar, fill=color)
        if not self.closing:
            self.root.after(50, self._animate_waveform)

    def set_mode(self, mode):
        with self.mode_lock:
            self.mode = mode
        self._refresh_mode_buttons()
        label = "机器人控制" if mode == "robot" else "AI 对话"
        self.add_message("assistant", f"已切换到{label}模式。")

    def get_mode(self):
        with self.mode_lock:
            return self.mode

    def _refresh_mode_buttons(self):
        for button, mode in (
            (self.robot_mode_button, "robot"),
            (self.chat_mode_button, "chat"),
        ):
            selected = self.mode == mode
            button.configure(
                bg=self.COLORS["surface"] if selected else "#E9EEEC",
                fg=self.COLORS["green"] if selected else self.COLORS["muted"],
                activebackground=self.COLORS["surface"] if selected else "#E9EEEC",
            )

    def request_manual_listen(self):
        if not self.ready:
            return
        if self.manual_recording:
            self.manual_stop_event.set()
            self.talk_button.configure(text="正在结束...", state="disabled")
            self._set_voice_state("stopping", "正在结束录音")
            return
        if self.voice_state in ("recognizing", "thinking", "speaking", "stopping"):
            return
        self.manual_recording = True
        self.manual_stop_event.clear()
        self.manual_listen_event.set()
        self.talk_button.configure(text="结束并识别")
        self.transcript_label.configure(text="正在录音，再次点击结束")

    def toggle_auto_listen(self):
        if not self.ready:
            return
        if self.auto_listen_event.is_set():
            self.auto_listen_event.clear()
            self.auto_button.configure(text="开始唤醒")
            self._set_voice_state("paused", "监听已暂停")
        else:
            self.auto_listen_event.set()
            self.auto_button.configure(text="暂停唤醒")
            self._set_voice_state("waiting", "等待唤醒：你好同学")

    def test_sound(self):
        threading.Thread(
            target=lambda: speak("你好，我是工业机器人语音控制示例。中文语音播报正常。"),
            daemon=True,
        ).start()

    def reconnect_robot(self):
        self._update_status("robot", "正在重新连接", "active")
        threading.Thread(target=init_modbus_client_robot, daemon=True).start()

    def _backend_worker(self):
        try:
            exit_code = initialize_system(require_robot=False, require_ollama=False)
            if exit_code != 0:
                self.post_event(
                    {
                        "type": "backend_failed",
                        "text": f"系统初始化失败，错误码 {exit_code}",
                    }
                )
                return
            self.post_event({"type": "backend_ready"})
            if not speak(STARTUP_ANNOUNCEMENT):
                self.post_event(
                    {"type": "backend_failed", "text": "启动语音播报失败，语音控制已锁止"}
                )
                return
            voice_control_loop(
                self.shutdown_event,
                self.auto_listen_event,
                self.manual_listen_event,
                self.manual_stop_event,
                self.get_mode,
            )
        except Exception as exc:
            print(f"❌ 后台服务异常：{exc}")
            self.post_event({"type": "backend_failed", "text": f"后台服务异常：{exc}"})
        finally:
            cleanup_resources()

    def on_close(self):
        if self.closing:
            return
        self.closing = True
        self.shutdown_event.set()
        self.auto_listen_event.clear()
        self.manual_listen_event.set()
        self.manual_stop_event.set()
        self.talk_button.configure(state="disabled")
        self.auto_button.configure(state="disabled")
        self._set_voice_state("paused", "正在关闭")
        self.root.after(250, self._finish_close)

    def _finish_close(self):
        if self.backend_thread and self.backend_thread.is_alive():
            self.root.after(250, self._finish_close)
            return
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return 0

def main():
    if STARTUP_CHECK_ONLY:
        exit_code = initialize_system(require_robot=True)
        if exit_code == 0:
            print("✅ 一键启动检查通过（未进入语音监听，未下发机器人指令）")
        return exit_code

    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    return VoiceAssistantApp().run()

cleanup_lock = threading.Lock()
cleanup_done = False

def cleanup_resources():
    global cleanup_done
    with cleanup_lock:
        if cleanup_done:
            return
        cleanup_done = True
        if modbus_client_local:
            modbus_client_local.close()
        if modbus_client_robot:
            modbus_client_robot.close()
        if modbus_server:
            modbus_server.stop()
        try:
            sd.stop()
        except Exception:
            pass
        if tts_thread and tts_thread.is_alive():
            tts_queue.put(None)
            tts_thread.join(timeout=2)
        print("✅ 已清理资源")

if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print("\n🛑 程序退出")
    except Exception as e:
        print(f"\n❌ 程序异常：{e}")
        exit_code = 1
    finally:
        cleanup_resources()
    sys.exit(exit_code)
