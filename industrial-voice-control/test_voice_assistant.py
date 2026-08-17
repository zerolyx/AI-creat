import importlib.util
import base64
import os
import pathlib
import tempfile
import threading
import unittest
import wave
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).with_name("1.py")
SPEC = importlib.util.spec_from_file_location("voice_assistant", MODULE_PATH)
voice = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voice)


class CommandSafetyTests(unittest.TestCase):
    def setUp(self):
        self.original_speak = voice.speak
        self.original_robot_write = voice.robot_write_register
        voice.local_state_fault_event.clear()
        self.events = []
        voice.speak = lambda text, timeout=None: self.events.append(("speak", text))
        voice.robot_write_register = lambda addr, value: self.events.append(
            ("robot", addr, value)
        ) or True

    def tearDown(self):
        voice.speak = self.original_speak
        voice.robot_write_register = self.original_robot_write
        voice.local_state_fault_event.clear()

    def test_first_example_command_is_sent(self):
        self.assertTrue(voice.process_command("请执行示例搬运"))
        self.assertIn(("robot", voice.ROBOT_COMMAND_REGISTER, 1), self.events)

    def test_second_example_command_is_sent(self):
        self.assertTrue(voice.process_command("请执行示例定位"))
        self.assertIn(("robot", voice.ROBOT_COMMAND_REGISTER, 2), self.events)

    def test_common_recognition_error_is_corrected(self):
        self.assertTrue(voice.process_command("请执行示例运输"))
        self.assertIn(("robot", voice.ROBOT_COMMAND_REGISTER, 1), self.events)

    def test_numeric_recognition_error_is_corrected(self):
        self.assertTrue(voice.process_command("请执行示例运输"))
        self.assertIn(("robot", voice.ROBOT_COMMAND_REGISTER, 1), self.events)

    def test_compact_example_command_is_sent(self):
        self.assertTrue(voice.process_command("请启动搬运"))
        self.assertIn(("robot", voice.ROBOT_COMMAND_REGISTER, 1), self.events)

    def test_fifth_example_command_is_sent(self):
        self.assertTrue(voice.process_command("请启动复位"))
        self.assertIn(("robot", voice.ROBOT_COMMAND_REGISTER, 5), self.events)

    def test_low_confidence_command_is_never_sent(self):
        details = {"confidence": 0.2, "snr_db": 12.0}
        self.assertTrue(voice.process_command("请执行示例搬运", recognition_details=details))
        self.assertFalse(any(event[0] == "robot" for event in self.events))
        self.assertTrue(any(event[0] == "speak" and "不够清楚" in event[1] for event in self.events))

    def test_low_snr_command_is_never_sent(self):
        details = {"confidence": 0.9, "snr_db": -1.0}
        self.assertTrue(voice.process_command("请执行示例搬运", recognition_details=details))
        self.assertFalse(any(event[0] == "robot" for event in self.events))
        self.assertTrue(any(event[0] == "speak" and "不够清楚" in event[1] for event in self.events))

    def test_local_state_fault_does_not_block_example_motion(self):
        voice.local_state_fault_event.set()
        self.assertTrue(voice.process_command("请执行示例搬运"))
        self.assertIn(("robot", voice.ROBOT_COMMAND_REGISTER, 1), self.events)

    def test_example_command_is_not_blocked_by_previous_local_step(self):
        original_read = voice.read_local_step
        try:
            voice.read_local_step = lambda: 0
            self.assertTrue(voice.process_command("请执行示例检测"))
            self.assertIn(("robot", voice.ROBOT_COMMAND_REGISTER, 3), self.events)
        finally:
            voice.read_local_step = original_read

    def test_successful_write_releases_command_state_immediately(self):
        events = []
        original_callback = voice.ui_event_callback
        original_speak = voice.speak
        lock_states_during_feedback = []
        try:
            voice.ui_event_callback = events.append
            voice.speak = lambda _text, timeout=None: lock_states_during_feedback.append(
                voice.robot_command_lock.locked()
            ) or True
            self.assertTrue(voice.process_command("请执行示例搬运"))
            self.assertFalse(voice.robot_command_lock.locked())
            self.assertEqual(lock_states_during_feedback, [False])
            action_events = [event for event in events if event.get("type") == "current_action"]
            self.assertEqual(action_events[-1]["level"], "ok")
            self.assertIn("最近下发", action_events[-1]["text"])
        finally:
            voice.ui_event_callback = original_callback
            voice.speak = original_speak

    def test_fixed_command_value_is_whitelisted(self):
        result, matched = voice.send_robot_command_value(4)
        self.assertEqual(result, "sent")
        self.assertEqual(matched["desc"], "示例装配")
        self.assertIn(("robot", voice.ROBOT_COMMAND_REGISTER, 4), self.events)
        with self.assertRaises(ValueError):
            voice.send_robot_command_value(99)

    def test_negative_command_is_never_sent(self):
        self.assertTrue(voice.process_command("不要执行示例搬运"))
        self.assertFalse(any(event[0] == "robot" for event in self.events))

    def test_short_negative_command_is_never_sent(self):
        self.assertTrue(voice.process_command("不执行示例搬运"))
        self.assertFalse(any(event[0] == "robot" for event in self.events))

    def test_question_is_not_a_robot_command(self):
        self.assertFalse(voice.process_command("示例搬运是什么"))
        self.assertFalse(any(event[0] == "robot" for event in self.events))

    def test_object_name_without_action_is_not_sent(self):
        self.assertFalse(voice.process_command("示例搬运"))
        self.assertFalse(any(event[0] == "robot" for event in self.events))

    def test_wake_word_requires_full_phrase(self):
        self.assertTrue(voice.is_wake_phrase("你好同学"))
        self.assertTrue(voice.is_wake_phrase("你好同雪"))
        self.assertTrue(voice.is_wake_phrase("提好同学"))
        self.assertFalse(voice.is_wake_phrase("同学"))

    def test_command_can_follow_wake_word_in_one_sentence(self):
        self.assertEqual(
            voice.strip_wake_phrase("你好同学，请执行示例搬运"),
            "请执行示例搬运",
        )


class RobotDisconnectTests(unittest.TestCase):
    def test_private_env_loader_accepts_only_whitelisted_keys(self):
        config_path = MODULE_PATH.with_name("robot_config.local.env")
        original_values = {
            key: os.environ.get(key)
            for key in (
                "ROBOT_CONTROL_ENABLED",
                "ROBOT_MODBUS_HOST",
                "ROBOT_MODBUS_PORT",
                "ROBOT_COMMAND_REGISTER",
                "UNSAFE_SHELL",
            )
        }
        try:
            for key in original_values:
                os.environ.pop(key, None)
            config_path.write_text(
                "ROBOT_CONTROL_ENABLED=false\n"
                "ROBOT_MODBUS_HOST=203.0.113.10\n"
                "ROBOT_MODBUS_PORT=15020\n"
                "ROBOT_COMMAND_REGISTER=40002\n"
                "UNSAFE_SHELL=should-not-load\n",
                encoding="utf-8",
            )
            spec = importlib.util.spec_from_file_location("safe_config_voice", MODULE_PATH)
            configured_voice = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(configured_voice)
            self.assertFalse(configured_voice.ROBOT_CONTROL_ENABLED)
            self.assertEqual(configured_voice.ROBOT_MODBUS_HOST, "203.0.113.10")
            self.assertEqual(configured_voice.ROBOT_MODBUS_PORT, 15020)
            self.assertEqual(configured_voice.ROBOT_COMMAND_REGISTER, 40002)
            self.assertNotIn("UNSAFE_SHELL", os.environ)
        finally:
            config_path.unlink(missing_ok=True)
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_public_default_disables_robot_control(self):
        self.assertFalse(voice.ROBOT_CONTROL_ENABLED)

    def test_disconnected_write_returns_false_without_exception(self):
        original_init = voice.init_modbus_client_robot
        original_client = voice.modbus_client_robot
        try:
            voice.modbus_client_robot = None
            voice.init_modbus_client_robot = lambda retries=1: False
            self.assertFalse(voice.robot_write_register(voice.ROBOT_COMMAND_REGISTER, 1))
        finally:
            voice.init_modbus_client_robot = original_init
            voice.modbus_client_robot = original_client

    def test_timed_out_motion_write_is_never_retried(self):
        class TimeoutClient:
            is_open = True
            last_error_as_txt = "recv timeout occur"

            def __init__(self):
                self.write_count = 0

            def write_single_register(self, _address, _value):
                self.write_count += 1
                return False

            def close(self):
                self.is_open = False

        client = TimeoutClient()
        original_client = voice.modbus_client_robot
        original_ensure = voice.ensure_robot_connected
        original_read = voice.robot_read_register
        original_enabled = voice.ROBOT_CONTROL_ENABLED
        try:
            voice.ROBOT_CONTROL_ENABLED = True
            voice.modbus_client_robot = client
            voice.ensure_robot_connected = lambda: True
            voice.robot_read_register = lambda _address: None
            self.assertFalse(voice.robot_write_register(voice.ROBOT_COMMAND_REGISTER, 1))
            self.assertEqual(client.write_count, 1)
            self.assertEqual(voice.last_robot_write_outcome, "unknown")
        finally:
            voice.modbus_client_robot = original_client
            voice.ensure_robot_connected = original_ensure
            voice.robot_read_register = original_read
            voice.ROBOT_CONTROL_ENABLED = original_enabled


class FeedbackStateTests(unittest.TestCase):
    def test_industrial_startup_defaults_to_manual_listening(self):
        self.assertTrue(voice.START_PAUSED)

    def test_speak_returns_sound_status_to_idle(self):
        class ImmediateQueue:
            @staticmethod
            def put(item):
                item[1].set()

        original_engine = voice.tts_engine
        original_queue = voice.tts_queue
        original_callback = voice.ui_event_callback
        original_guard = voice.ECHO_GUARD_SEC
        events = []
        try:
            voice.tts_engine = object()
            voice.tts_queue = ImmediateQueue()
            voice.ui_event_callback = events.append
            voice.ECHO_GUARD_SEC = 0
            voice.speak("测试播报", timeout=0.1)
            sound_labels = [
                event.get("label") for event in events if event.get("type") == "sound"
            ]
            self.assertEqual(sound_labels[-1], "中文语音待机")
        finally:
            voice.tts_engine = original_engine
            voice.tts_queue = original_queue
            voice.ui_event_callback = original_callback
            voice.ECHO_GUARD_SEC = original_guard

    def test_normal_feedback_never_calls_beeper_by_default(self):
        original_enabled = voice.AUDIBLE_TONES_ENABLED
        original_beep = voice.winsound.Beep
        calls = []
        try:
            voice.AUDIBLE_TONES_ENABLED = False
            voice.winsound.Beep = lambda *_args: calls.append(_args)
            self.assertFalse(voice.play_feedback_tone("ready"))
            self.assertEqual(calls, [])
        finally:
            voice.AUDIBLE_TONES_ENABLED = original_enabled
            voice.winsound.Beep = original_beep

    def test_spoken_text_is_naturalized(self):
        self.assertEqual(
            voice._prepare_spoken_text(f"AI 正在检查 Modbus {voice.ROBOT_COMMAND_REGISTER_LABEL}"),
            f"人工智能 正在检查 机器人通信 寄存器{voice.ROBOT_COMMAND_REGISTER}。",
        )

    def test_tts_voice_selection_requires_chinese_voice(self):
        class FakeVoice:
            def __init__(self, name, languages):
                self.id = name
                self.name = name
                self.languages = languages

        voices = [
            FakeVoice("Microsoft Zira Desktop", ["en-US"]),
            FakeVoice("Microsoft Huihui Desktop", ["zh-CN"]),
        ]
        self.assertEqual(voice._select_tts_voice(voices).name, "Microsoft Huihui Desktop")
        self.assertIsNone(voice._select_tts_voice(voices[:1]))

    def test_generated_speech_wave_must_contain_audible_frames(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        try:
            samples = (voice.np.sin(voice.np.linspace(0, 16 * voice.np.pi, 2205)) * 12000).astype(voice.np.int16)
            with wave.open(path, "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(22050)
                stream.writeframes(samples.tobytes())
            audio, sample_rate, rms, peak = voice._read_tts_wave(path)
            self.assertEqual(sample_rate, 22050)
            self.assertEqual(audio.shape, (2205,))
            self.assertGreater(rms, 0.01)
            self.assertGreater(peak, 0.1)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)

    def test_silent_speech_wave_is_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        try:
            with wave.open(path, "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(22050)
                stream.writeframes(voice.np.zeros(2205, dtype=voice.np.int16).tobytes())
            with self.assertRaisesRegex(RuntimeError, "静音"):
                voice._read_tts_wave(path)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)

    def test_isolated_synthesizer_transfers_chinese_as_utf8_base64(self):
        completed = mock.Mock(returncode=0, stderr="", stdout="")
        original_voice_id = voice.tts_voice_id
        try:
            voice.tts_voice_id = "test-voice"
            with mock.patch.object(voice.subprocess, "run", return_value=completed) as run:
                voice._synthesize_tts_file("中文播报", "output.wav")
            command = run.call_args.args[0]
            self.assertEqual(
                base64.b64decode(command[-1]).decode("utf-8"),
                "中文播报",
            )
        finally:
            voice.tts_voice_id = original_voice_id

    def test_speech_waits_until_recording_releases_audio_session(self):
        class ImmediateQueue:
            @staticmethod
            def put(item):
                item[1].set()

        original_engine = voice.tts_engine
        original_queue = voice.tts_queue
        original_guard = voice.ECHO_GUARD_SEC
        completed = threading.Event()
        try:
            voice.tts_engine = object()
            voice.tts_queue = ImmediateQueue()
            voice.ECHO_GUARD_SEC = 0
            voice.tts_fault_event.clear()
            voice.audio_session_lock.acquire()
            worker = threading.Thread(
                target=lambda: (voice.speak("测试互斥"), completed.set()),
                daemon=True,
            )
            worker.start()
            self.assertFalse(completed.wait(0.05))
            voice.audio_session_lock.release()
            self.assertTrue(completed.wait(1.0))
            worker.join(timeout=1)
        finally:
            if voice.audio_session_lock.locked():
                voice.audio_session_lock.release()
            voice.tts_engine = original_engine
            voice.tts_queue = original_queue
            voice.ECHO_GUARD_SEC = original_guard
            voice.tts_fault_event.clear()

    def test_tts_fault_blocks_microphone_capture(self):
        original_microphone = voice.selected_microphone_index
        events = []
        original_callback = voice.ui_event_callback
        try:
            voice.tts_fault_event.set()
            voice.selected_microphone_index = 0
            voice.ui_event_callback = events.append
            self.assertIsNone(voice.record_utterance(0.1))
            self.assertTrue(any(event.get("type") == "voice_state" and event.get("state") == "error" for event in events))
        finally:
            voice.tts_fault_event.clear()
            voice.selected_microphone_index = original_microphone
            voice.ui_event_callback = original_callback


class AudioPipelineTests(unittest.TestCase):
    def test_spectral_denoise_preserves_shape_and_finite_values(self):
        rng = voice.np.random.default_rng(7)
        duration = 1.2
        timeline = voice.np.arange(int(voice.SAMPLING_RATE * duration)) / voice.SAMPLING_RATE
        signal = 0.08 * voice.np.sin(2 * voice.np.pi * 520 * timeline)
        noisy = (signal + rng.normal(0, 0.025, timeline.size)).astype(voice.np.float32)

        cleaned = voice.spectral_denoise(noisy)

        self.assertEqual(cleaned.shape, noisy.shape)
        self.assertEqual(cleaned.dtype, voice.np.float32)
        self.assertTrue(voice.np.isfinite(cleaned).all())

    def test_recognition_confidence_uses_segment_quality(self):
        metrics = voice._decode_recognition_metrics(
            {
                "segments": [
                    {"start": 0.0, "end": 1.0, "avg_logprob": -0.2, "no_speech_prob": 0.05},
                    {"start": 1.0, "end": 2.0, "avg_logprob": -0.3, "no_speech_prob": 0.08},
                ]
            }
        )
        self.assertGreater(metrics["confidence"], 0.5)
        self.assertLess(metrics["no_speech_probability"], 0.1)

    def test_manual_recording_waits_for_explicit_stop_despite_silence(self):
        frame_samples = int(voice.SAMPLING_RATE * voice.FRAME_DURATION_MS / 1000)
        speech_frame = (voice.np.ones(frame_samples, dtype=voice.np.int16) * 2200).tobytes()
        silence_frame = voice.np.zeros(frame_samples, dtype=voice.np.int16).tobytes()
        stop_event = threading.Event()

        class FakeVad:
            def __init__(self, _mode):
                self.calls = 0

            def is_speech(self, _frame, _sample_rate):
                self.calls += 1
                return self.calls <= 12

        class FakeStream:
            def __init__(self, **_kwargs):
                self.read_count = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _frames):
                self.read_count += 1
                if self.read_count >= 45:
                    stop_event.set()
                frame = speech_frame if self.read_count <= 12 else silence_frame
                return frame, False

        original_stream = voice.sd.RawInputStream
        original_vad = voice.webrtcvad.Vad
        original_microphone = voice.selected_microphone_index
        try:
            voice.sd.RawInputStream = FakeStream
            voice.webrtcvad.Vad = FakeVad
            voice.selected_microphone_index = 0
            captured = voice.record_utterance(
                1.0,
                manual_stop_event=stop_event,
                manual_mode=True,
            )
            self.assertEqual(len(captured), 45 * len(speech_frame))
        finally:
            voice.sd.RawInputStream = original_stream
            voice.webrtcvad.Vad = original_vad
            voice.selected_microphone_index = original_microphone


if __name__ == "__main__":
    unittest.main()
