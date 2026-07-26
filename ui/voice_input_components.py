"""Push-to-talk capture, transcription worker, and settings controls."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QThread, QTimer
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from core.secrets import SecretStore
from core.services.session_secret_store import SessionSecretStore
from core.services.voice_input_service import (
    VoiceEndpoint,
    VoiceTranscriptionService,
)


class PushToTalkRecorder(QObject):
    """Capture only while held; audio remains in memory until released."""

    status_changed = pyqtSignal(str)
    recording_finished = pyqtSignal(bytes)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._buffer = None
        self._format = None
        self._maximum_timer = QTimer(self)
        self._maximum_timer.setSingleShot(True)
        self._maximum_timer.timeout.connect(self.stop)

    @property
    def recording(self) -> bool:
        return self._source is not None

    def start(self) -> bool:
        if self.recording:
            return True
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            self.failed.emit("未找到可用麦克风")
            return False
        audio_format = QAudioFormat()
        audio_format.setSampleRate(16000)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(audio_format):
            self.failed.emit("当前麦克风不支持 16 kHz 单声道录音")
            return False
        self._format = audio_format
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray())
        self._buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        self._source = QAudioSource(device, audio_format, self)
        self._source.start(self._buffer)
        self._maximum_timer.start(60_000)
        self.status_changed.emit("正在录音 · 松开即停止")
        return True

    def stop(self):
        if not self.recording:
            return
        self._maximum_timer.stop()
        self._source.stop()
        raw = bytes(self._buffer.data())
        self._buffer.close()
        self._source.deleteLater()
        self._buffer.deleteLater()
        audio_format = self._format
        self._source = None
        self._buffer = None
        self._format = None
        if not raw:
            self.failed.emit("没有录到声音")
            return
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(audio_format.channelCount())
            wav.setsampwidth(2)
            wav.setframerate(audio_format.sampleRate())
            wav.writeframes(raw)
        self.status_changed.emit("录音完成 · 正在转写")
        self.recording_finished.emit(output.getvalue())

    def cancel(self):
        if not self.recording:
            return
        self._maximum_timer.stop()
        self._source.stop()
        self._buffer.close()
        self._source.deleteLater()
        self._buffer.deleteLater()
        self._source = None
        self._buffer = None
        self._format = None
        self.status_changed.emit("麦克风已关闭")


class VoiceTranscriptionWorker(QThread):
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, service, wav_bytes, api_key, parent=None):
        super().__init__(parent)
        self.service = service
        self.wav_bytes = wav_bytes
        self.api_key = api_key

    def run(self):
        try:
            text = self.service.transcribe(
                self.wav_bytes,
                api_key=self.api_key,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(text)


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool
    muted: bool
    base_url: str
    model: str
    api_key: str
    session_only: bool
    hotkeys_enabled: bool
    hotkeys: dict[str, str]


class VoiceSettingsPanel(QWidget):
    """Voice endpoint, credential and hotkey settings with disclosure."""

    def __init__(
        self,
        config_manager,
        session_secrets: SessionSecretStore,
        parent=None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.session_secrets = session_secrets
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        disclosure = QLabel(
            "按住说话时才访问麦克风；松开后仅将本次 WAV 发送到下方"
            "独立转写端点。Pixkin 不保留录音文件；服务商保留策略以其条款为准。"
        )
        disclosure.setObjectName("muted")
        disclosure.setWordWrap(True)
        layout.addWidget(disclosure)
        self.enabled_input = QCheckBox("启用按住说话")
        self.enabled_input.setChecked(
            bool(self.config_manager.get("voice_input", "enabled", False))
        )
        self.muted_input = QCheckBox("麦克风静音")
        self.muted_input.setChecked(
            bool(self.config_manager.get("voice_input", "muted", False))
        )
        layout.addWidget(self.enabled_input)
        layout.addWidget(self.muted_input)
        form = QFormLayout()
        self.base_url_input = QLineEdit(
            str(
                self.config_manager.get(
                    "voice_input",
                    "base_url",
                    "https://api.openai.com/v1",
                )
            )
        )
        self.model_input = QLineEdit(
            str(
                self.config_manager.get(
                    "voice_input", "model", "whisper-1"
                )
            )
        )
        key = (
            self.session_secrets.get_voice_api_key()
            or SecretStore.get_voice_api_key()
        )
        self.api_key_input = QLineEdit(key)
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.session_only_input = QCheckBox(
            "仅本次运行使用语音 API Key"
        )
        self.session_only_input.setChecked(
            bool(self.session_secrets.get_voice_api_key())
        )
        form.addRow("转写接口", self.base_url_input)
        form.addRow("转写模型", self.model_input)
        form.addRow("独立 API Key", self.api_key_input)
        form.addRow("", self.session_only_input)
        layout.addLayout(form)
        self.hotkeys_enabled_input = QCheckBox("启用全局快捷键")
        self.hotkeys_enabled_input.setChecked(
            bool(
                self.config_manager.get(
                    "voice_input", "hotkeys_enabled", True
                )
            )
        )
        layout.addWidget(self.hotkeys_enabled_input)
        defaults = {
            "toggle_pet": "Ctrl+Alt+P",
            "open_chat": "Ctrl+Alt+C",
            "stop_generation": "Ctrl+Alt+S",
            "mute_voice": "Ctrl+Alt+M",
        }
        configured = self.config_manager.get(
            "voice_input", "hotkeys", defaults
        )
        if not isinstance(configured, dict):
            configured = defaults
        grid = QGridLayout()
        self.hotkey_inputs = {}
        for row, (key_name, label) in enumerate(
            (
                ("toggle_pet", "显示 / 隐藏桌宠"),
                ("open_chat", "打开聊天"),
                ("stop_generation", "停止生成"),
                ("mute_voice", "麦克风静音"),
            )
        ):
            editor = QLineEdit(
                str(configured.get(key_name, defaults[key_name]))
            )
            editor.setPlaceholderText(defaults[key_name])
            self.hotkey_inputs[key_name] = editor
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(editor, row, 1)
        layout.addLayout(grid)

    def values(self) -> VoiceSettings:
        return VoiceSettings(
            enabled=self.enabled_input.isChecked(),
            muted=self.muted_input.isChecked(),
            base_url=self.base_url_input.text().strip(),
            model=self.model_input.text().strip(),
            api_key=self.api_key_input.text().strip(),
            session_only=self.session_only_input.isChecked(),
            hotkeys_enabled=self.hotkeys_enabled_input.isChecked(),
            hotkeys={
                key: editor.text().strip()
                for key, editor in self.hotkey_inputs.items()
            },
        )

    def build_service(self):
        values = self.values()
        return VoiceTranscriptionService(
            VoiceEndpoint(values.base_url, values.model)
        )
