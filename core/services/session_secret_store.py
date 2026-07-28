"""Process-local API keys that are cleared when Pixkin exits."""


class SessionSecretStore:
    """Keep optional chat and image credentials in memory only."""

    def __init__(self):
        self._chat_api_key = ""
        self._image_api_key = ""
        self._voice_api_key = ""

    def get_chat_api_key(self) -> str:
        return self._chat_api_key

    def set_chat_api_key(self, value: str) -> None:
        self._chat_api_key = str(value or "")

    def get_image_api_key(self) -> str:
        return self._image_api_key

    def set_image_api_key(self, value: str) -> None:
        self._image_api_key = str(value or "")

    def get_voice_api_key(self) -> str:
        return self._voice_api_key

    def set_voice_api_key(self, value: str) -> None:
        self._voice_api_key = str(value or "")

    def clear(self) -> None:
        self._chat_api_key = ""
        self._image_api_key = ""
        self._voice_api_key = ""
