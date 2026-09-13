"""Windows SAPI / OneCore TTS provider using pywin32."""

import io
import logging
import wave
from contextlib import contextmanager
from typing import List, Optional

from core.providers.tts.base import (
    TtsError,
    TtsProvider,
    TtsProviderCapabilities,
    TtsProviderHealth,
    TtsVoice,
)

LOGGER = logging.getLogger("desktop_pet.tts.sapi")
MAX_TTS_TEXT_CHARS = 20_000
MAX_AUDIO_BYTES = 20 * 1024 * 1024


class WindowsSapiProvider(TtsProvider):
    """Offline, zero-config Windows SAPI Text-to-Speech provider."""

    def __init__(self):
        self._capabilities = TtsProviderCapabilities(
            streaming=False,
            offline=True,
            voice_list=True,
            pitch_control=False,
            speed_control=True,
        )

    @property
    def capabilities(self) -> TtsProviderCapabilities:
        return self._capabilities

    @contextmanager
    def _voice_com(self):
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            sp_voice = None
            try:
                sp_voice = win32com.client.Dispatch("SAPI.SpVoice")
                yield sp_voice
            finally:
                # Drop the local wrapper before uninitializing this thread's
                # COM apartment.  Pywin32 releases the underlying IUnknown
                # during normal Python finalization; probing private COM
                # methods here can itself dispatch into SAPI and crash.
                sp_voice = None
                pythoncom.CoUninitialize()
        except Exception as exc:
            raise TtsError(f"初始化 Windows SAPI 失败: {exc}", category="sapi_init") from exc

    def list_voices(self) -> List[TtsVoice]:
        try:
            with self._voice_com() as sp_voice:
                voices: List[TtsVoice] = []
                collection = sp_voice.GetVoices()
                try:
                    for item in collection:
                        desc = str(item.GetDescription() or "").strip()
                        voice_id = str(item.Id or "").strip()
                        # Determine language roughly
                        lang = "zh-CN" if "Chinese" in desc or "ZH-CN" in voice_id.upper() else "en-US"
                        gender = "female" if any(k in desc.lower() for k in ("huihui", "zira", "female")) else "male"
                        voices.append(
                            TtsVoice(
                                id=voice_id,
                                name=desc or voice_id,
                                language=lang,
                                gender=gender,
                                description=desc,
                            )
                        )
                        item = None
                finally:
                    collection = None
                return voices
        except Exception as exc:
            LOGGER.warning("获取 SAPI 音色列表失败: %s", exc)
            return []

    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        speed: float = 1.0,
    ) -> bytes:
        clean_text = str(text or "").strip()
        if not clean_text:
            return b""
        if len(clean_text) > MAX_TTS_TEXT_CHARS:
            raise TtsError(
                "TTS 文本超过大小上限",
                category="input_too_large",
            )
        try:
            import win32com.client

            with self._voice_com() as sp_voice:
                sp_stream = win32com.client.Dispatch("SAPI.SpMemoryStream")

                # Set voice token if specified
                if voice:
                    collection = sp_voice.GetVoices()
                    try:
                        for item in collection:
                            if item.Id == voice or item.GetDescription() == voice:
                                sp_voice.Voice = item
                                break
                            item = None
                    finally:
                        collection = None

                # Rate mapping: 1.0 -> 0, 0.5 -> -5, 2.0 -> 5
                rate = int((speed - 1.0) * 10)
                sp_voice.Rate = max(-10, min(10, rate))

                # Bind stream and speak
                sp_voice.AudioOutputStream = sp_stream
                sp_voice.Speak(clean_text)

                raw_data = bytes(sp_stream.GetData())
                # Release COM references before leaving the initialized
                # apartment; retaining a stream can leak the SAPI object.
                sp_voice.AudioOutputStream = None
                sp_stream = None
            if not raw_data:
                return b""
            if len(raw_data) > MAX_AUDIO_BYTES:
                raise TtsError(
                    "TTS 音频响应超过大小上限",
                    category="response_too_large",
                )

            # If raw_data already has RIFF header, return directly
            if raw_data.startswith(b"RIFF"):
                return raw_data

            # Otherwise wrap raw PCM into standard 22050Hz 16-bit Mono WAV
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(22050)
                wav_file.writeframes(raw_data)
            result = wav_buffer.getvalue()
            if len(result) > MAX_AUDIO_BYTES:
                raise TtsError(
                    "TTS 音频响应超过大小上限",
                    category="response_too_large",
                )
            return result

        except Exception as exc:
            raise TtsError(f"Windows SAPI 语音合成失败: {exc}", category="sapi_synth") from exc

    def health_check(self) -> TtsProviderHealth:
        try:
            voices = self.list_voices()
            if not voices:
                return TtsProviderHealth(
                    healthy=False,
                    message="未找到可用的 Windows SAPI 语音",
                    category="no_voices",
                )
            return TtsProviderHealth(
                healthy=True,
                message=f"Windows SAPI 准备就绪，包含 {len(voices)} 种音色",
                voices_count=len(voices),
            )
        except Exception as exc:
            return TtsProviderHealth(
                healthy=False,
                message=f"Windows SAPI 检查异常: {exc}",
                category="sapi_error",
            )

    def close(self) -> None:
        pass
