import json
import unittest
from pathlib import Path

from core.providers.live import (
    BilibiliLiveAdapter,
    DouyinLiveAdapter,
    LiveProviderError,
)


FIXTURES = Path(__file__).parent / "fixtures" / "live"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class LiveProviderContractTests(unittest.TestCase):
    def test_bilibili_recorded_live_and_offline_responses(self):
        room = {"platform": "bilibili", "room_id": "10001"}

        live = BilibiliLiveAdapter._parse_payload(
            load_fixture("bilibili_room_live.json"), room
        )
        offline = BilibiliLiveAdapter._parse_payload(
            load_fixture("bilibili_room_offline.json"), room
        )

        self.assertTrue(live.is_live)
        self.assertEqual(live.title, "一起画画")
        self.assertFalse(offline.is_live)

    def test_douyin_recorded_live_and_offline_responses(self):
        room = {"platform": "douyin", "room_id": "20002"}

        live = DouyinLiveAdapter._parse_payload(
            load_fixture("douyin_room_live.json"), room
        )
        offline = DouyinLiveAdapter._parse_payload(
            load_fixture("douyin_room_offline.json"), room
        )

        self.assertTrue(live.is_live)
        self.assertEqual(live.anchor_name, "示例主播")
        self.assertFalse(offline.is_live)

    def test_malformed_response_is_rejected_as_provider_error(self):
        with self.assertRaisesRegex(LiveProviderError, "缺少 data"):
            DouyinLiveAdapter._parse_payload({}, {})

    def test_room_urls_require_exact_https_platform_hosts(self):
        self.assertEqual(
            BilibiliLiveAdapter._room_id(
                "https://live.bilibili.com/12345"
            ),
            "12345",
        )
        self.assertEqual(
            BilibiliLiveAdapter._room_id(
                "https://evil.test/?live.bilibili.com/12345"
            ),
            "",
        )
        with self.assertRaises(LiveProviderError):
            DouyinLiveAdapter._validated_douyin_url(
                "http://live.douyin.com/123"
            )

    def test_plaintext_room_cookie_is_ignored_for_isolated_provider_credential(self):
        adapter = BilibiliLiveAdapter(
            credential_provider=lambda platform, room: (
                f"secure:{platform}:{room}"
            )
        )

        credential = adapter._credential(
            {
                "platform": "bilibili",
                "room_id": "123",
                "cookie": "plaintext-config-cookie",
            }
        )

        self.assertEqual(credential, "secure:bilibili:123")


if __name__ == "__main__":
    unittest.main()
