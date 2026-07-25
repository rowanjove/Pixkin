import re
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Dict
from urllib.parse import urljoin, urlparse

import requests


DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class LiveStatus:
    is_live: bool
    title: str = ""
    anchor_name: str = ""


class LivePlatformError(RuntimeError):
    pass


class BaseLiveAdapter:
    platform = ""

    def check(self, room: Dict[str, str]) -> LiveStatus:
        raise NotImplementedError

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

    def check(self, room: Dict[str, str]) -> LiveStatus:
        room_id = self._room_id(room.get("room_id") or room.get("room_url", ""))
        if not room_id:
            raise LivePlatformError("B 站房间号为空或格式不正确")

        with self._session(
            room.get("cookie", ""), ".bilibili.com"
        ) as session:
            response = session.get(
                "https://api.live.bilibili.com/room/v1/Room/get_info",
                params={"room_id": room_id, "from": "room"},
                timeout=(4, 8),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
                raise LivePlatformError(payload.get("message") or "B 站接口返回异常")
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
        match = re.search(r"live\.bilibili\.com/(\d+)", value)
        return match.group(1) if match else ""


class DouyinLiveAdapter(BaseLiveAdapter):
    platform = "douyin"
    ALLOWED_HOST_SUFFIXES = ("douyin.com", "iesdouyin.com")

    def check(self, room: Dict[str, str]) -> LiveStatus:
        value = str(room.get("room_id") or room.get("room_url") or "").strip()
        if not value:
            raise LivePlatformError("抖音直播间地址为空")
        if urlparse(value).scheme.lower() in {"http", "https"}:
            room_url = self._validated_douyin_url(value)
        else:
            room_url = f"https://live.douyin.com/{value}"

        with self._session(
            room.get("cookie", ""), ".douyin.com"
        ) as session:
            # 短链先跟随跳转，随后访问首页与房间页以建立网页端 Cookie。
            if urlparse(room_url).hostname == "v.douyin.com":
                room_url = self._follow_safe_redirects(session, room_url)
            parsed = urlparse(room_url)
            web_rid = parsed.path.strip("/").split("/")[0]
            if not web_rid:
                raise LivePlatformError("无法从抖音地址中识别直播间 ID")

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
            room_list = payload.get("data", {}).get("data", [])
            if not room_list:
                return LiveStatus(False)
            data = room_list[0]
            stream = data.get("stream_url") or {}
            is_live = int(data.get("status", 0)) == 2 and bool(stream)
            owner = data.get("owner") or {}
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
        if parsed.scheme not in {"http", "https"} or not any(
            host == suffix or host.endswith("." + suffix)
            for suffix in cls.ALLOWED_HOST_SUFFIXES
        ):
            raise LivePlatformError("抖音直播间地址必须使用受信任的抖音域名")
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
                raise LivePlatformError("抖音短链跳转缺少目标地址")
            current = cls._validated_douyin_url(
                urljoin(current, location)
            )
        raise LivePlatformError("抖音短链跳转次数过多")


ADAPTERS = {
    "bilibili": BilibiliLiveAdapter(),
    "douyin": DouyinLiveAdapter(),
}


def check_live_room(room: Dict[str, str]) -> LiveStatus:
    platform = str(room.get("platform", "")).lower()
    adapter = ADAPTERS.get(platform)
    if not adapter:
        raise LivePlatformError(f"暂不支持平台：{platform}")
    return adapter.check(room)
