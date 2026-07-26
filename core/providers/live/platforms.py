"""HTTP-backed livestream providers and platform routing."""

from __future__ import annotations

import re
from http.cookies import SimpleCookie
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlparse

import requests

from core.providers.live.base import (
    LiveProvider,
    LiveProviderCapabilities,
    LiveProviderError,
    LiveProviderHealth,
    LiveRoom,
    LiveStatus,
)
from core.services.live_credential_service import LiveCredentialStore


DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class BaseLiveAdapter(LiveProvider):
    platform = ""
    api_version = 1
    credential_scope = "per_room"

    def __init__(self, credential_provider=None):
        self._credential_provider = (
            credential_provider or LiveCredentialStore.get_cookie
        )

    def _credential(self, room: LiveRoom) -> str:
        room_id = str(
            room.get("room_id") or room.get("room_url") or ""
        )
        return str(self._credential_provider(self.platform, room_id) or "")

    @property
    def capabilities(self) -> LiveProviderCapabilities:
        return LiveProviderCapabilities()

    def health_check(self) -> LiveProviderHealth:
        return LiveProviderHealth(
            healthy=True,
            message=f"{self.platform} 直播状态接口已配置",
        )

    def close(self) -> None:
        # Providers create a short-lived requests.Session for each probe.
        return None

    @staticmethod
    def _session(
        cookie: str = "", cookie_domain: str = ""
    ) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": DESKTOP_USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        if cookie and cookie_domain:
            parsed = SimpleCookie()
            try:
                parsed.load(cookie)
            except Exception:
                parsed = SimpleCookie()
            for name, morsel in parsed.items():
                session.cookies.set(
                    name, morsel.value, domain=cookie_domain, path="/"
                )
        return session


class BilibiliLiveAdapter(BaseLiveAdapter):
    platform = "bilibili"

    def check(self, room: LiveRoom) -> LiveStatus:
        room_id = self._room_id(
            str(room.get("room_id") or room.get("room_url") or "")
        )
        if not room_id:
            raise LiveProviderError("B 站房间号为空或格式不正确")

        with self._session(
            self._credential(room), ".bilibili.com"
        ) as session:
            response = session.get(
                "https://api.live.bilibili.com/room/v1/Room/get_info",
                params={"room_id": room_id, "from": "room"},
                timeout=(4, 8),
            )
            response.raise_for_status()
            payload = response.json()
            return self._parse_payload(payload, room)

    @staticmethod
    def _parse_payload(
        payload: Mapping[str, Any], room: LiveRoom
    ) -> LiveStatus:
        if payload.get("code") != 0 or not isinstance(
            payload.get("data"), dict
        ):
            raise LiveProviderError(
                str(payload.get("message") or "B 站接口返回异常")
            )
        data = payload["data"]
        return LiveStatus(
            is_live=int(data.get("live_status", 0)) == 1,
            title=str(data.get("title") or "正在直播"),
            anchor_name=str(room.get("anchor_name") or ""),
        )

    @staticmethod
    def _room_id(value: str) -> str:
        value = str(value or "").strip()
        if value.isdigit():
            return value
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or host != "live.bilibili.com":
            return ""
        match = re.fullmatch(r"/(\d+)/?", parsed.path)
        return match.group(1) if match else ""


class DouyinLiveAdapter(BaseLiveAdapter):
    platform = "douyin"
    ALLOWED_HOST_SUFFIXES = ("douyin.com", "iesdouyin.com")

    def check(self, room: LiveRoom) -> LiveStatus:
        value = str(room.get("room_id") or room.get("room_url") or "").strip()
        if not value:
            raise LiveProviderError("抖音直播间地址为空")
        if urlparse(value).scheme.lower() in {"http", "https"}:
            room_url = self._validated_douyin_url(value)
        else:
            room_url = f"https://live.douyin.com/{value}"

        with self._session(
            self._credential(room), ".douyin.com"
        ) as session:
            if urlparse(room_url).hostname == "v.douyin.com":
                room_url = self._follow_safe_redirects(session, room_url)
            parsed = urlparse(room_url)
            web_rid = parsed.path.strip("/").split("/")[0]
            if not web_rid:
                raise LiveProviderError("无法从抖音地址中识别直播间 ID")

            session.headers["Referer"] = f"https://live.douyin.com/{web_rid}"
            session.get("https://live.douyin.com/", timeout=(4, 8))
            session.get(
                f"https://live.douyin.com/{web_rid}",
                timeout=(4, 8),
            )
            response = session.get(
                "https://live.douyin.com/webcast/room/web/enter/",
                params={
                    "aid": "6383",
                    "app_name": "douyin_web",
                    "live_id": "1",
                    "device_platform": "web",
                    "language": "zh-CN",
                    "enter_from": "web_live",
                    "cookie_enabled": "true",
                    "screen_width": "1920",
                    "screen_height": "1080",
                    "browser_language": "zh-CN",
                    "browser_platform": "Win32",
                    "browser_name": "Chrome",
                    "browser_version": "124.0.0.0",
                    "web_rid": web_rid,
                },
                timeout=(4, 10),
            )
            response.raise_for_status()
            payload = response.json()
            return self._parse_payload(payload, room)

    @staticmethod
    def _parse_payload(
        payload: Mapping[str, Any], room: LiveRoom
    ) -> LiveStatus:
        outer_data = payload.get("data")
        if not isinstance(outer_data, dict):
            raise LiveProviderError("抖音接口响应缺少 data 对象")
        room_list = outer_data.get("data", [])
        if not isinstance(room_list, list):
            raise LiveProviderError("抖音接口响应的房间列表格式不正确")
        if not room_list:
            return LiveStatus(False)
        data = room_list[0]
        if not isinstance(data, dict):
            raise LiveProviderError("抖音接口响应的房间信息格式不正确")
        stream = data.get("stream_url") or {}
        is_live = int(data.get("status", 0)) == 2 and bool(stream)
        owner = data.get("owner") or {}
        if not isinstance(owner, dict):
            owner = {}
        return LiveStatus(
            is_live=is_live,
            title=str(data.get("title") or "正在直播"),
            anchor_name=str(
                room.get("anchor_name") or owner.get("nickname") or ""
            ),
        )

    @classmethod
    def _validated_douyin_url(cls, value: str) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not any(
            host == suffix or host.endswith("." + suffix)
            for suffix in cls.ALLOWED_HOST_SUFFIXES
        ):
            raise LiveProviderError(
                "抖音直播间地址必须使用受信任的抖音域名"
            )
        return value

    @classmethod
    def _follow_safe_redirects(
        cls, session: requests.Session, value: str
    ) -> str:
        current = cls._validated_douyin_url(value)
        for _ in range(5):
            response = session.get(
                current, timeout=(4, 8), allow_redirects=False
            )
            if not response.is_redirect and not response.is_permanent_redirect:
                return cls._validated_douyin_url(response.url or current)
            location = response.headers.get("Location")
            if not location:
                raise LiveProviderError("抖音短链跳转缺少目标地址")
            current = cls._validated_douyin_url(urljoin(current, location))
        raise LiveProviderError("抖音短链跳转次数过多")


class LiveProviderRouter:
    def __init__(self, providers: Iterable[LiveProvider]):
        self._providers = {
            provider.platform.lower(): provider for provider in providers
        }

    def check(self, room: LiveRoom) -> LiveStatus:
        platform = str(room.get("platform") or "").lower()
        provider = self._providers.get(platform)
        if provider is None:
            raise LiveProviderError(f"暂不支持平台：{platform}")
        return provider.check(room)

    def close(self) -> None:
        for provider in self._providers.values():
            provider.close()


ADAPTERS = {
    "bilibili": BilibiliLiveAdapter(),
    "douyin": DouyinLiveAdapter(),
}
DEFAULT_LIVE_PROVIDER_ROUTER = LiveProviderRouter(ADAPTERS.values())


def check_live_room(room: LiveRoom) -> LiveStatus:
    return DEFAULT_LIVE_PROVIDER_ROUTER.check(room)
