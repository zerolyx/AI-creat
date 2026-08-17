#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only deployment audit aligned with 语音模型安装步骤.docx."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PRIVATE_CONFIG_PATH = BASE_DIR / "robot_config.local.env"
PRIVATE_CONFIG_KEYS = {
    "LOCAL_MODBUS_HOST",
    "LOCAL_MODBUS_PORT",
    "ROBOT_MODBUS_HOST",
    "ROBOT_MODBUS_PORT",
    "ROBOT_COMMAND_REGISTER",
    "ROBOT_CONTROL_ENABLED",
}


def load_private_config():
    if not PRIVATE_CONFIG_PATH.is_file():
        return
    try:
        for raw_line in PRIVATE_CONFIG_PATH.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if key in PRIVATE_CONFIG_KEYS and key not in os.environ:
                os.environ[key] = value
    except OSError as exc:
        print(f"[WARN] 私有机器人配置读取失败：{exc}")


load_private_config()
ROBOT_HOST = os.environ.get("ROBOT_MODBUS_HOST", "127.0.0.1").strip() or "127.0.0.1"
try:
    ROBOT_PORT = max(1, min(65535, int(os.environ.get("ROBOT_MODBUS_PORT", "502"))))
except ValueError:
    ROBOT_PORT = 502
try:
    ROBOT_COMMAND_REGISTER = max(40001, min(49999, int(os.environ.get("ROBOT_COMMAND_REGISTER", "40001"))))
except ValueError:
    ROBOT_COMMAND_REGISTER = 40001
ROBOT_CONTROL_ENABLED = os.environ.get("ROBOT_CONTROL_ENABLED", "0").strip().casefold() in {"1", "true", "yes"}
EXPECTED_PACKAGES = {
    "pyttsx3": "2.99",
    "openai-whisper": "20250625",
    "sounddevice": "0.5.5",
    "numpy": "2.4.6",
    "ollama": "0.6.2",
    "pyModbusTCP": "0.3.0",
    "webrtcvad-wheels": "2.0.14",
    "soundfile": "0.14.0",
    "pywin32": "312",
    "opencc-python-reimplemented": "0.1.7",
    "torch": "2.13.0",
}


class Audit:
    def __init__(self):
        self.results = []

    def add(self, name, status, detail):
        self.results.append({"name": name, "status": status, "detail": str(detail)})
        print(f"[{status:<4}] {name}: {detail}")

    def pass_(self, name, detail):
        self.add(name, "PASS", detail)

    def warn(self, name, detail):
        self.add(name, "WARN", detail)

    def fail(self, name, detail):
        self.add(name, "FAIL", detail)

    def write_report(self):
        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_baseline": {
                "os": "Windows 11 x64",
                "python": "3.11.9 x64",
                "ollama": "0.20.4",
                "model": "qwen2.5:7b",
            },
            "results": self.results,
        }
        path = BASE_DIR / "deployment_report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @property
    def exit_code(self):
        return 1 if any(item["status"] == "FAIL" for item in self.results) else 0


def check_windows(audit):
    if os.name != "nt":
        audit.fail("操作系统", f"需要 Windows 11，当前为 {platform.platform()}")
        return
    version = sys.getwindowsversion()
    architecture = struct.calcsize("P") * 8
    if version.build >= 22000 and architecture == 64:
        audit.pass_("操作系统", f"Windows 11 build {version.build} / {architecture} 位")
    else:
        audit.fail("操作系统", f"需要 Windows 11 x64，当前 build {version.build} / {architecture} 位")


def check_python(audit):
    actual = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    architecture = struct.calcsize("P") * 8
    if actual == "3.11.9" and architecture == 64:
        audit.pass_("Python", f"{actual} x64 ({sys.executable})")
    else:
        audit.fail("Python", f"文档要求 3.11.9 x64，当前 {actual} x{architecture}")


def registry_value(path, name):
    try:
        import winreg

        views = [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]
        for view in views:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | view) as key:
                    return winreg.QueryValueEx(key, name)[0]
            except OSError:
                continue
    except ImportError:
        return None
    return None


def check_system_components(audit):
    vc_installed = registry_value(
        r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64", "Installed"
    )
    if vc_installed == 1:
        audit.pass_("VC++ 运行库", "Microsoft Visual C++ 2015-2022 x64 已安装")
    else:
        audit.fail("VC++ 运行库", "未检测到 Microsoft Visual C++ 2015-2022 x64")

    dotnet_release = registry_value(
        r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full", "Release"
    )
    if isinstance(dotnet_release, int) and dotnet_release >= 528040:
        audit.pass_(".NET Framework", f"4.8+ release {dotnet_release}")
    else:
        audit.fail(".NET Framework", f"需要 4.8+，当前注册表值 {dotnet_release}")

    powershell = shutil.which("powershell.exe")
    if not powershell:
        audit.fail("PowerShell", "未找到 powershell.exe")
        return
    try:
        output = subprocess.check_output(
            [powershell, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        ).strip()
        audit.pass_("PowerShell", output)
    except (OSError, subprocess.SubprocessError) as exc:
        audit.fail("PowerShell", exc)


def find_ollama():
    candidates = [
        shutil.which("ollama"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"),
    ]
    return next((Path(item) for item in candidates if item and Path(item).is_file()), None)


def check_ollama(audit):
    executable = find_ollama()
    if not executable:
        audit.fail("Ollama", "未找到 Ollama 0.20.4")
        return
    try:
        version = subprocess.check_output(
            [str(executable), "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        audit.fail("Ollama", exc)
        return
    if "0.20.4" in version:
        audit.pass_("Ollama", version)
    else:
        audit.warn("Ollama", f"文档基线为 0.20.4，当前 {version}；需做现场回归验证")

    try:
        request = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = {item.get("name", "") for item in payload.get("models", [])}
        if any(name == "qwen2.5:7b" or name.startswith("qwen2.5:7b-") for name in models):
            audit.pass_("Qwen 模型", "qwen2.5:7b 已就绪")
        else:
            audit.fail("Qwen 模型", "ollama list 中未发现 qwen2.5:7b")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        audit.fail("Ollama 服务", f"127.0.0.1:11434 无响应：{exc}")


def check_packages(audit):
    mismatches = []
    missing = []
    for package, expected in EXPECTED_PACKAGES.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing.append(package)
            continue
        if actual != expected:
            mismatches.append(f"{package}={actual} (锁定 {expected})")
    if missing:
        audit.fail("Python 依赖", "缺少：" + ", ".join(missing))
    elif mismatches:
        audit.warn("Python 依赖", "版本漂移：" + "; ".join(mismatches))
    else:
        audit.pass_("Python 依赖", f"{len(EXPECTED_PACKAGES)} 个核心包与锁定版本一致")


def check_microphone(audit):
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = [device["name"] for device in devices if device["max_input_channels"] > 0]
        if not inputs:
            audit.fail("麦克风", "未找到输入设备")
            return
        default_index = int(sd.default.device[0])
        default_name = devices[default_index]["name"] if default_index >= 0 else inputs[0]
        audit.pass_("麦克风", f"默认输入：{default_name}；可用输入设备 {len(inputs)} 个")

        outputs = [device["name"] for device in devices if device["max_output_channels"] > 0]
        if not outputs:
            audit.fail("音频输出", "未找到扬声器或耳机输出设备")
        else:
            default_output_index = int(sd.default.device[1])
            default_output = (
                devices[default_output_index]["name"]
                if default_output_index >= 0
                else outputs[0]
            )
            audit.pass_("音频输出", f"默认输出：{default_output}；可用输出设备 {len(outputs)} 个")
    except Exception as exc:
        audit.fail("麦克风", exc)


def check_chinese_speech(audit):
    engine = None
    temp_path = None
    try:
        import numpy as np
        import pyttsx3

        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        chinese_voices = []
        selected_voice = None
        for voice in voices:
            description = " ".join(
                str(value or "")
                for value in (
                    getattr(voice, "id", ""),
                    getattr(voice, "name", ""),
                    getattr(voice, "languages", ""),
                )
            ).lower()
            if any(token in description for token in ("zh-cn", "zh_cn", "chinese", "mandarin")):
                chinese_voices.append(str(getattr(voice, "name", "简体中文语音")))
                if selected_voice is None:
                    selected_voice = voice
        if selected_voice is None:
            audit.fail("中文语音播报", "未安装简体中文系统语音，程序将锁止语音控制")
            return

        handle, temp_path = tempfile.mkstemp(prefix="industrial_voice_audit_", suffix=".wav")
        os.close(handle)
        os.remove(temp_path)
        engine.setProperty("voice", selected_voice.id)
        engine.setProperty("volume", 1.0)
        engine.save_to_file("工业机器人中文语音合成检查。", temp_path)
        engine.runAndWait()
        if not os.path.isfile(temp_path) or os.path.getsize(temp_path) < 1024:
            raise RuntimeError("系统语音未生成有效文件")
        with wave.open(temp_path, "rb") as stream:
            if stream.getsampwidth() != 2 or stream.getcomptype() != "NONE":
                raise RuntimeError("系统语音生成格式异常")
            sample_rate = stream.getframerate()
            frames = stream.getnframes()
            samples = np.frombuffer(stream.readframes(frames), dtype=np.int16)
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0
        if frames < sample_rate * 0.1 or rms < 16.0:
            raise RuntimeError("系统语音生成结果为空或静音")
        audit.pass_(
            "中文语音播报",
            f"{getattr(selected_voice, 'name', chinese_voices[0])}；已生成 {frames} 帧非静音音频",
        )
    except Exception as exc:
        audit.fail("中文语音播报", exc)
    finally:
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def check_robot(audit):
    if not ROBOT_CONTROL_ENABLED:
        audit.pass_("机器人控制", "安全开源示例模式：未启用现场机器人探测或控制")
        return
    try:
        route_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        route_probe.connect((ROBOT_HOST, ROBOT_PORT))
        local_ip = route_probe.getsockname()[0]
        route_probe.close()
        same_subnet = local_ip.rsplit(".", 1)[0] == ROBOT_HOST.rsplit(".", 1)[0]
        if same_subnet:
            audit.pass_("局域网", f"本机 {local_ip} 与机器人 {ROBOT_HOST} 位于同一 /24 网段")
        else:
            audit.warn("局域网", f"本机 {local_ip} 与机器人 {ROBOT_HOST} 可能不在同一网段")
    except OSError as exc:
        audit.warn("局域网", exc)

    try:
        with socket.create_connection((ROBOT_HOST, ROBOT_PORT), timeout=1.5):
            pass
        audit.pass_("机器人 TCP", f"{ROBOT_HOST}:{ROBOT_PORT} 可连接")
    except OSError as exc:
        audit.warn("机器人 TCP", f"现场设备未连通：{exc}")
        return

    try:
        from pyModbusTCP.client import ModbusClient

        client = ModbusClient(
            host=ROBOT_HOST,
            port=ROBOT_PORT,
            auto_open=False,
            auto_close=False,
            timeout=2,
        )
        if not client.open():
            audit.warn("机器人 Modbus", client.last_error_as_txt)
            return
        values = client.read_holding_registers(ROBOT_COMMAND_REGISTER - 40001, 1)
        client.close()
        if values is None:
            audit.warn("机器人 Modbus", f"TCP 可连通，但 R{ROBOT_COMMAND_REGISTER} 只读探测无响应；运动指令将保持锁止")
        else:
            audit.pass_("机器人 Modbus", f"R{ROBOT_COMMAND_REGISTER} 只读探测成功，当前值 {values[0]}")
    except Exception as exc:
        audit.warn("机器人 Modbus", f"只读检查失败：{exc}")


def check_product_files(audit):
    required = [
        "1.py",
        "web_app.py",
        "web/index.html",
        "web/styles.css",
        "web/app.js",
        "web/theme.js",
        "web/engineer.html",
        "web/engineer.css",
        "web/engineer.js",
        "scripts/synthesize_speech.py",
        "requirements-lock.txt",
        "语音模型安装步骤.docx",
        "开源版-v2.6.0-安全发布说明.md",
    ]
    missing = [item for item in required if not (BASE_DIR / item).is_file()]
    if missing:
        audit.fail("产品文件", "缺少：" + ", ".join(missing))
    else:
        audit.pass_("产品文件", "前端、后端、锁定依赖和安装文档齐全")

    free_gb = shutil.disk_usage(BASE_DIR).free / (1024 ** 3)
    if free_gb >= 10:
        audit.pass_("磁盘空间", f"可用 {free_gb:.1f} GB")
    else:
        audit.warn("磁盘空间", f"仅剩 {free_gb:.1f} GB，模型与缓存建议预留 10 GB 以上")


def main():
    print("=" * 68)
    print("工业机器人语音控制示例 - 文档基线环境检查（全程只读）")
    print("=" * 68)
    audit = Audit()
    check_windows(audit)
    check_python(audit)
    check_system_components(audit)
    check_ollama(audit)
    check_packages(audit)
    check_microphone(audit)
    check_chinese_speech(audit)
    check_robot(audit)
    check_product_files(audit)
    report = audit.write_report()
    failures = sum(item["status"] == "FAIL" for item in audit.results)
    warnings = sum(item["status"] == "WARN" for item in audit.results)
    print("-" * 68)
    print(f"检查完成：FAIL={failures}，WARN={warnings}，报告：{report}")
    return audit.exit_code


if __name__ == "__main__":
    sys.exit(main())
