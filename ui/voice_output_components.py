"""Settings controls and preview worker for speech synthesis (TTS)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
)
from core.secrets import SecretStore
from core.services.session_secret_store import SessionSecretStore

@dataclass(frozen=True)
class VoiceOutputSettings:
    enabled: bool
    provider: str
    voice: str
    speed: float
    base_url: str
    model: str


def play_audio_bytes(data: bytes) -> bool:
    """Play synthesized audio bytes (WAV or MP3) on Windows."""
    if not data:
        return False
    if data.startswith(b"RIFF"):
        try:
            import winsound

            winsound.PlaySound(data, winsound.SND_MEMORY)
            return True
        except Exception:
            pass
    try:
        import ctypes
        import os
        import tempfile

        suffix = ".wav" if data.startswith(b"RIFF") else ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            alias = f"pixkin_tts_{abs(hash(tmp_path)) % 1000000}"
            mci = ctypes.windll.winmm.mciSendStringW
            mci(f'open "{tmp_path}" alias {alias}', None, 0, None)
            mci(f"play {alias} wait", None, 0, None)
            mci(f"close {alias}", None, 0, None)
            return True
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception:
        pass
    return False


class TtsTestWorker(QObject):
    """Run TTS synthesis off the main Qt thread."""

    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        provider,
        text: str,
        voice: str,
        speed: float,
        *,
        permission_service: ContextPermissionService | None = None,
        permission_token: str | None = None,
    ):
        super().__init__()
        self.provider = provider
        self.text = text
        self.voice = voice
        self.speed = speed
        self.permission_service = permission_service or ContextPermissionService()
        self.permission_token = permission_token
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        cancel = getattr(self.provider, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass

    def run(self):
        try:
            if not self.provider.capabilities.offline:
                decision = self.permission_service.decide(
                    PermissionResource.NETWORK,
                    PermissionOperation.WRITE,
                    resolve_ask=False,
                    consume_pending=True,
                    handoff_token=self.permission_token,
                )
                if not decision.allowed:
                    raise PermissionError("网络权限未允许")
            if self._cancelled:
                self.finished.emit(False, "试听已取消")
                return
            audio_bytes = self.provider.synthesize(
                self.text,
                voice=self.voice,
                speed=self.speed,
            )
            if self._cancelled:
                self.finished.emit(False, "试听已取消")
                return
            if audio_bytes:
                play_audio_bytes(audio_bytes)
            self.finished.emit(True, "试听成功")
        except Exception as exc:
            self.finished.emit(False, f"试听失败: {exc}")
        finally:
            try:
                self.provider.close()
            except Exception:
                pass


class VoiceOutputSettingsPanel(QWidget):
    """Settings card for configuring text-to-speech output."""

    def __init__(
        self,
        config_manager,
        *,
        provider_registry=None,
        session_secrets: SessionSecretStore | None = None,
        permission_service: ContextPermissionService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.provider_registry = provider_registry
        self.session_secrets = session_secrets
        self.permission_service = permission_service or ContextPermissionService()
        self._test_thread: Optional[QThread] = None
        self._test_worker: Optional[TtsTestWorker] = None
        self._permission_token: str | None = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        disclosure = QLabel(
            "桌宠回复时可同步朗读文本；支持 Windows 原生离线语音及云端高保真接口。"
        )
        disclosure.setObjectName("muted")
        disclosure.setWordWrap(True)
        layout.addWidget(disclosure)

        self.enabled_input = QCheckBox("启用桌宠语音发声 (TTS)")
        self.enabled_input.setChecked(
            bool(self.config_manager.get("voice_output", "enabled", False))
        )
        layout.addWidget(self.enabled_input)

        form = QFormLayout()
        form.setSpacing(8)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Windows 原生语音 (离线免配置)", "windows_sapi")
        self.provider_combo.addItem("OpenAI 兼容端点 (云端高保真)", "openai_compatible")

        current_provider = str(
            self.config_manager.get("voice_output", "provider", "windows_sapi")
        )
        idx = self.provider_combo.findData(current_provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        form.addRow("语音引擎", self.provider_combo)

        self.voice_combo = QComboBox()
        form.addRow("发声音色", self.voice_combo)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setValue(
            float(self.config_manager.get("voice_output", "speed", 1.0))
        )
        form.addRow("语速倍率", self.speed_spin)

        self.base_url_input = QLineEdit(
            str(
                self.config_manager.get(
                    "voice_output", "base_url", "https://api.openai.com/v1"
                )
            )
        )
        self.model_input = QLineEdit(
            str(self.config_manager.get("voice_output", "model", "tts-1"))
        )
        form.addRow("接口地址", self.base_url_input)
        form.addRow("模型名称", self.model_input)

        layout.addLayout(form)

        # Test listen controls
        test_box = QHBoxLayout()
        self.test_btn = QPushButton("🔊 试听发音")
        self.test_status = QLabel("")
        self.test_status.setObjectName("muted")
        test_box.addWidget(self.test_btn)
        test_box.addWidget(self.test_status)
        test_box.addStretch(1)
        layout.addLayout(test_box)

        # Event connections
        self.provider_combo.currentIndexChanged.connect(self._refresh_voices)
        self.test_btn.clicked.connect(self._on_test_listen)
        self._refresh_voices()

    def _refresh_voices(self):
        provider_key = self.provider_combo.currentData()
        self.voice_combo.clear()
        saved_voice = str(self.config_manager.get("voice_output", "voice", ""))

        if self.provider_registry is None:
            self.voice_combo.addItem("系统默认", "")
        elif provider_key == "windows_sapi":
            self.base_url_input.setEnabled(False)
            self.model_input.setEnabled(False)
            try:
                sapi = self.provider_registry.create("windows_sapi")
                voices = sapi.list_voices()
                for v in voices:
                    self.voice_combo.addItem(f"{v.name} ({v.language})", v.id)
            except Exception:
                self.voice_combo.addItem("系统默认", "")
        else:
            self.base_url_input.setEnabled(True)
            self.model_input.setEnabled(True)
            openai_provider = self.provider_registry.create(
                "openai_compatible",
                base_url=self.base_url_input.text().strip(),
                api_key=self._voice_api_key(),
                model=self.model_input.text().strip(),
            )
            try:
                for v in openai_provider.list_voices():
                    self.voice_combo.addItem(v.name, v.id)
            finally:
                close = getattr(openai_provider, "close", None)
                if callable(close):
                    close()

        # Restore saved voice if found
        if saved_voice:
            idx = self.voice_combo.findData(saved_voice)
            if idx >= 0:
                self.voice_combo.setCurrentIndex(idx)

    def _on_test_listen(self):
        self.test_btn.setEnabled(False)
        self.test_status.setText("正在生成语音...")

        provider_key = self.provider_combo.currentData()
        voice_id = str(self.voice_combo.currentData() or "")
        speed = self.speed_spin.value()

        if self.provider_registry is None:
            self.test_btn.setEnabled(True)
            self.test_status.setText("语音引擎未注册")
            return
        if provider_key != "windows_sapi":
            permission_token = self.permission_service.new_handoff_token()
            self._permission_token = permission_token
            decision = self.permission_service.decide(
                PermissionResource.NETWORK,
                PermissionOperation.WRITE,
                handoff_token=permission_token,
            )
            if not decision.allowed:
                self.test_btn.setEnabled(True)
                self.test_status.setText(
                    "网络权限未允许；请先在“隐私与审计”中授权。"
                )
                self.permission_service.clear_pending(
                    PermissionResource.NETWORK,
                    PermissionOperation.WRITE,
                    handoff_token=permission_token,
                )
                self._permission_token = None
                return
        if provider_key == "windows_sapi":
            provider = self.provider_registry.create("windows_sapi")
        else:
            provider = self.provider_registry.create(
                "openai_compatible",
                base_url=self.base_url_input.text().strip(),
                api_key=self._voice_api_key(),
                model=self.model_input.text().strip(),
            )

        self._test_thread = QThread()
        self._test_worker = TtsTestWorker(
            provider,
            "你好呀！我是你的桌面伙伴 Pixkin。",
            voice_id,
            speed,
            permission_service=self.permission_service,
            permission_token=(
                permission_token if provider_key != "windows_sapi" else None
            ),
        )
        self._test_worker.moveToThread(self._test_thread)
        self._test_thread.started.connect(self._test_worker.run)
        self._test_worker.finished.connect(self._on_test_finished)
        self._test_worker.finished.connect(self._test_thread.quit)
        self._test_worker.finished.connect(self._test_worker.deleteLater)
        self._test_thread.finished.connect(self._test_thread.deleteLater)
        self._test_thread.start()

    def closeEvent(self, event) -> None:
        """Stop a preview before the settings widget is destroyed."""
        worker = self._test_worker
        thread = self._test_thread
        if worker is not None and thread is not None and thread.isRunning():
            try:
                worker.finished.disconnect(self._on_test_finished)
            except (TypeError, RuntimeError):
                pass
            worker.cancel()
        if self._permission_token:
            self.permission_service.clear_pending(
                PermissionResource.NETWORK,
                PermissionOperation.WRITE,
                handoff_token=self._permission_token,
            )
            self._permission_token = None
        super().closeEvent(event)

    def _voice_api_key(self) -> str:
        if self.session_secrets is not None:
            value = self.session_secrets.get_voice_api_key()
            if value:
                return value
        return SecretStore.get_voice_api_key()

    def _on_test_finished(self, success: bool, message: str):
        self.test_btn.setEnabled(True)
        self.test_status.setText(message)
        if self._permission_token:
            self.permission_service.clear_pending(
                PermissionResource.NETWORK,
                PermissionOperation.WRITE,
                handoff_token=self._permission_token,
            )
            self._permission_token = None

    def values(self) -> VoiceOutputSettings:
        return VoiceOutputSettings(
            enabled=self.enabled_input.isChecked(),
            provider=str(self.provider_combo.currentData() or "windows_sapi"),
            voice=str(self.voice_combo.currentData() or ""),
            speed=float(self.speed_spin.value()),
            base_url=self.base_url_input.text().strip(),
            model=self.model_input.text().strip(),
        )

    def save(self) -> None:
        vals = self.values()
        self.config_manager.set("voice_output", "enabled", vals.enabled)
        self.config_manager.set("voice_output", "provider", vals.provider)
        self.config_manager.set("voice_output", "voice", vals.voice)
        self.config_manager.set("voice_output", "speed", vals.speed)
        self.config_manager.set("voice_output", "base_url", vals.base_url)
        self.config_manager.set("voice_output", "model", vals.model)
