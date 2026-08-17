import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import web_app


class ConsoleStateTests(unittest.TestCase):
    def test_conversation_starts_empty_and_can_be_cleared(self):
        state = web_app.ConsoleState()
        self.assertEqual(state.snapshot()["messages"], [])
        state.add_message("assistant", "语音系统已就绪。")
        state.clear_messages()
        self.assertEqual(state.snapshot()["messages"], [])
        self.assertEqual(state.diagnostic_events()[-1]["message"], "会话记录已清空")

    def test_core_events_update_snapshot(self):
        state = web_app.ConsoleState()
        state.emit({"type": "status", "key": "robot", "value": "已连接", "level": "ok"})
        state.emit({"type": "voice_state", "state": "listening", "text": "正在聆听"})
        state.emit({"type": "message", "role": "user", "text": "执行示例搬运"})

        snapshot = state.snapshot()
        self.assertEqual(snapshot["statuses"]["robot"]["level"], "ok")
        self.assertEqual(snapshot["voice_state"], "listening")
        self.assertEqual(snapshot["messages"][-1]["text"], "执行示例搬运")

    def test_diagnostic_clear_does_not_change_operator_state(self):
        state = web_app.ConsoleState()
        state.add_message("assistant", "语音系统已就绪。")
        state.emit({"type": "command_gate", "text": "等待指令", "level": "idle"})
        before = state.snapshot()

        state.clear_diagnostics()

        after = state.snapshot()
        self.assertEqual(state.diagnostic_events(), [])
        self.assertEqual(after["messages"], before["messages"])
        self.assertEqual(after["command_gate"], before["command_gate"])

    def test_recognition_metrics_and_command_gate_update_snapshot(self):
        state = web_app.ConsoleState()
        state.emit(
            {
                "type": "recognition_metrics",
                "confidence": 0.82,
                "snr_db": 14.3,
                "duration": 2.4,
            }
        )
        state.emit(
            {
                "type": "command_gate",
                "text": "机器人已确认接收",
                "level": "active",
                "command": "示例搬运",
                "register": "R40001",
                "value": "1",
            }
        )

        snapshot = state.snapshot()
        self.assertEqual(snapshot["recognition_confidence"], 0.82)
        self.assertEqual(snapshot["snr_db"], 14.3)
        self.assertEqual(snapshot["command_gate_level"], "active")
        self.assertEqual(snapshot["command_name"], "示例搬运")


class VoiceRuntimeTests(unittest.TestCase):
    def test_startup_announcement_is_exactly_one_short_phrase(self):
        runtime = web_app.VoiceRuntime(start_backend=False)
        original_speak = web_app.voice.speak
        spoken = []
        try:
            web_app.voice.speak = lambda text: spoken.append(text) or True
            runtime.announce_ready()
            self.assertEqual(spoken, ["语音系统已就绪。"])
        finally:
            web_app.voice.speak = original_speak

    def test_failed_startup_announcement_is_a_hard_failure(self):
        runtime = web_app.VoiceRuntime(start_backend=False)
        original_speak = web_app.voice.speak
        try:
            web_app.voice.speak = lambda _text: False
            with self.assertRaisesRegex(RuntimeError, "启动语音播报失败"):
                runtime.announce_ready()
        finally:
            web_app.voice.speak = original_speak

    def test_engineer_snapshot_is_read_only_diagnostic_data(self):
        runtime = web_app.VoiceRuntime(start_backend=False)
        snapshot = runtime.engineer_snapshot()
        self.assertIn("statuses", snapshot)
        self.assertIn("events", snapshot)
        self.assertIn("deployment", snapshot)
        self.assertIn("tts_fault", snapshot["safety"])
        self.assertNotIn("action", snapshot)

    def test_engineer_log_reset_starts_a_new_view_without_deleting_history(self):
        original_log_path = web_app.LOG_PATH
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "voice_assistant.log"
            log_path.write_text("历史诊断记录\n", encoding="utf-8")
            web_app.LOG_PATH = log_path
            try:
                runtime = web_app.VoiceRuntime(start_backend=False)
                runtime.state.add_message("assistant", "保留的操作会话")
                before = runtime.state.snapshot()

                runtime.reset_engineer_logs()

                self.assertEqual(runtime.state.diagnostic_events(), [])
                self.assertEqual(runtime.state.snapshot()["messages"], before["messages"])
                self.assertEqual(log_path.read_text(encoding="utf-8"), "历史诊断记录\n")
                self.assertEqual(runtime.read_engineer_log(), [])

                with log_path.open("a", encoding="utf-8") as stream:
                    stream.write("本次诊断记录\n")
                visible_lines = runtime.read_engineer_log()
                self.assertEqual([item["text"] for item in visible_lines], ["本次诊断记录"])
                snapshot = runtime.engineer_snapshot()
                self.assertEqual(snapshot["log"]["session_id"], 1)
                self.assertGreater(snapshot["log"]["history_bytes"], 0)
                self.assertGreater(snapshot["log"]["visible_bytes"], 0)
            finally:
                web_app.LOG_PATH = original_log_path

    def test_manual_listen_toggles_start_then_stop(self):
        runtime = web_app.VoiceRuntime(start_backend=False)
        runtime.ready = True
        runtime.state.emit({"type": "backend_ready"})

        runtime.request_manual_listen()
        started = runtime.state.snapshot()
        self.assertTrue(started["manual_recording"])
        self.assertTrue(runtime.manual_listen_event.is_set())
        self.assertFalse(runtime.manual_stop_event.is_set())

        runtime.request_manual_listen()
        stopping = runtime.state.snapshot()
        self.assertTrue(runtime.manual_stop_event.is_set())
        self.assertEqual(stopping["voice_state"], "stopping")

    def test_mode_change_is_rejected_during_voice_processing(self):
        runtime = web_app.VoiceRuntime(start_backend=False)
        runtime.state.emit(
            {"type": "voice_state", "state": "recognizing", "text": "正在识别"}
        )
        with self.assertRaises(RuntimeError):
            runtime.set_mode("chat")

    def test_reconnect_is_rejected_while_robot_command_is_being_sent(self):
        runtime = web_app.VoiceRuntime(start_backend=False)
        acquired = web_app.voice.robot_command_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with self.assertRaises(RuntimeError):
                runtime.reconnect_robot()
        finally:
            web_app.voice.robot_command_lock.release()

    def test_click_command_requires_robot_mode_and_whitelisted_value(self):
        runtime = web_app.VoiceRuntime(start_backend=False)
        runtime.ready = True
        runtime.mode = "chat"
        with self.assertRaisesRegex(RuntimeError, "机器人控制模式"):
            runtime.send_robot_command(1)
        runtime.mode = "robot"
        with self.assertRaises(ValueError):
            runtime.send_robot_command(99)

    def test_click_command_uses_shared_fixed_command_path(self):
        runtime = web_app.VoiceRuntime(start_backend=False)
        runtime.ready = True
        runtime.state.emit({"type": "voice_state", "state": "paused", "text": "监听已暂停"})
        original_send = web_app.voice.send_robot_command_value
        calls = []
        try:
            web_app.voice.send_robot_command_value = lambda value: calls.append(value) or (
                "sent", {"value": value, "desc": "示例检测"}
            )
            runtime.send_robot_command(3)
            self.assertEqual(calls, [3])
            self.assertIn("点击下发：示例检测", [
                message["text"] for message in runtime.state.snapshot()["messages"]
            ])
        finally:
            web_app.voice.send_robot_command_value = original_send


class ConsoleApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = web_app.VoiceRuntime(start_backend=False)
        cls.server = web_app.ConsoleServer(("127.0.0.1", 0), cls.runtime)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request_json(self, path, payload=None, origin=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8")), response.headers

    def test_health_and_state_are_available(self):
        status, health, _ = self.request_json("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["product"], web_app.PRODUCT_ID)

        status, payload, headers = self.request_json("/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_mode_change_is_applied(self):
        status, payload, _ = self.request_json(
            "/api/action", {"action": "set_mode", "mode": "chat"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"]["mode"], "chat")

    def test_conversation_can_be_cleared_without_changing_control_state(self):
        self.runtime.state.add_message("assistant", "待清理")
        before = self.runtime.state.snapshot()
        status, payload, _ = self.request_json(
            "/api/action", {"action": "clear_messages"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"]["messages"], [])
        self.assertEqual(payload["state"]["command_gate"], before["command_gate"])

    def test_api_click_command_rejects_non_whitelisted_value(self):
        self.runtime.ready = True
        self.runtime.mode = "robot"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request_json(
                "/api/action", {"action": "send_robot_command", "value": 99}
            )
        self.assertEqual(caught.exception.code, 400)

    def test_engineer_diagnostics_are_available_locally(self):
        status, payload, _ = self.request_json("/api/engineer")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["diagnostics"]["product"], web_app.PRODUCT_ID)

        status, logs, _ = self.request_json("/api/engineer/logs?limit=20&level=all")
        self.assertEqual(status, 200)
        self.assertTrue(logs["ok"])
        self.assertIsInstance(logs["lines"], list)

    def test_engineer_logs_can_be_reset_without_changing_control_state(self):
        self.runtime.state.emit({"type": "command_gate", "text": "安全门保持", "level": "idle"})
        before = self.runtime.state.snapshot()
        status, payload, _ = self.request_json(
            "/api/action", {"action": "reset_engineer_logs"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"]["command_gate"], before["command_gate"])
        status, diagnostics, _ = self.request_json("/api/engineer")
        self.assertEqual(status, 200)
        self.assertEqual(diagnostics["diagnostics"]["events"], [])
        self.assertGreaterEqual(diagnostics["diagnostics"]["log"]["session_id"], 1)

    def test_cross_origin_action_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request_json(
                "/api/action",
                {"action": "set_mode", "mode": "robot"},
                origin="https://example.com",
            )
        self.assertEqual(caught.exception.code, 403)

    def test_static_path_cannot_escape_web_root(self):
        request = urllib.request.Request(f"{self.base_url}/..%2F1.py")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
