import logging


LOGGER = logging.getLogger("desktop_pet.secrets")
CHAT_TARGET = "Pixkin/ChatAPI"
IMAGE_TARGET = "Pixkin/ImageAPI"
VOICE_TARGET = "Pixkin/VoiceAPI"
LEGACY_TARGET = "DesktopPet/OpenAICompatibleAPI"


class SecretStore:
    """使用 Windows Credential Manager 保存 API Key。"""

    @staticmethod
    def _read(target: str) -> str:
        try:
            import win32cred

            credential = win32cred.CredRead(
                target, win32cred.CRED_TYPE_GENERIC, 0
            )
            blob = credential.get("CredentialBlob", b"")
            if isinstance(blob, bytes):
                return blob.decode("utf-16-le").rstrip("\x00")
            return str(blob or "")
        except Exception:
            return ""

    @staticmethod
    def _write(target: str, value: str, comment: str) -> bool:
        try:
            import win32cred

            if not value:
                try:
                    win32cred.CredDelete(
                        target, win32cred.CRED_TYPE_GENERIC, 0
                    )
                except Exception as exc:
                    error_code = (
                        getattr(exc, "winerror", None)
                        or (exc.args[0] if exc.args else None)
                    )
                    if error_code != 1168:
                        LOGGER.warning("Windows 凭据删除失败: %s", exc)
                        return False
                return True

            try:
                win32cred.CredWrite(
                    {
                        "Type": win32cred.CRED_TYPE_GENERIC,
                        "TargetName": target,
                        "UserName": "Pixkin",
                        "CredentialBlob": value,
                        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
                        "Comment": comment,
                    },
                    0,
                )
                return True
            except Exception as initial_exc:
                # 企业凭据可能按域策略漫游；不要在机器级凭据失败时
                # 隐式扩大 API Key 的传播范围，回退只使用当前会话。
                fallback_persist = getattr(
                    win32cred, "CRED_PERSIST_SESSION", 1
                )
                try:
                    win32cred.CredWrite(
                        {
                            "Type": win32cred.CRED_TYPE_GENERIC,
                            "TargetName": target,
                            "UserName": "Pixkin",
                            "CredentialBlob": value,
                            "Persist": fallback_persist,
                            "Comment": comment,
                        },
                        0,
                    )
                    return True
                except Exception:
                    LOGGER.warning("Windows 凭据保存失败: %s", initial_exc)
                    return False
        except Exception as exc:
            LOGGER.warning("Windows 凭据操作异常: %s", exc)
            return False

    @classmethod
    def get_api_key(cls) -> str:
        return cls._read(CHAT_TARGET) or cls._read(LEGACY_TARGET)

    @classmethod
    def set_api_key(cls, value: str) -> bool:
        if not value:
            chat_deleted = cls._write(
                CHAT_TARGET, "", "Pixkin 对话模型 API Key"
            )
            legacy_deleted = cls._write(
                LEGACY_TARGET, "", "Pixkin 旧版对话模型 API Key"
            )
            return chat_deleted and legacy_deleted
        saved = cls._write(
            CHAT_TARGET, value, "Pixkin 对话模型 API Key"
        )
        if saved and not cls._write(
            LEGACY_TARGET, "", "Pixkin 旧版对话模型 API Key"
        ):
            LOGGER.warning("新凭据已保存，但旧版凭据未能删除")
        return saved

    @classmethod
    def migrate_legacy_api_key(cls) -> bool:
        legacy = cls._read(LEGACY_TARGET)
        if not legacy:
            return True
        if not cls._write(
            CHAT_TARGET, legacy, "Pixkin 对话模型 API Key"
        ):
            return False
        if cls._write(
            LEGACY_TARGET, "", "Pixkin 旧版对话模型 API Key"
        ):
            return True
        cls._write(CHAT_TARGET, "", "Pixkin 对话模型 API Key")
        return False

    @classmethod
    def get_image_api_key(cls) -> str:
        return cls._read(IMAGE_TARGET)

    @classmethod
    def set_image_api_key(cls, value: str) -> bool:
        return cls._write(IMAGE_TARGET, value, "Pixkin 伙伴工坊图像模型 API Key")

    @classmethod
    def get_voice_api_key(cls) -> str:
        return cls._read(VOICE_TARGET)

    @classmethod
    def set_voice_api_key(cls, value: str) -> bool:
        return cls._write(VOICE_TARGET, value, "Pixkin 语音转写 API Key")
