import unittest
from unittest.mock import patch

from core.services.tool_permission_service import (
    ToolPermissionLevel,
    ToolPermissionService,
)
from core.tool_registry import ToolRegistry


class ToolPermissionTests(unittest.TestCase):
    def test_l0_executes_automatically_and_is_audited(self):
        permissions = ToolPermissionService(clock=lambda: 10.0)
        registry = ToolRegistry(permissions)

        result = registry.execute_tool(
            "calculate_expression",
            {"expression": "2 + 3"},
        )

        self.assertEqual(result, "2 + 3 = 5")
        event = permissions.events()[-1]
        self.assertEqual(event.level, ToolPermissionLevel.READ_ONLY)
        self.assertEqual(event.authorization, "automatic")
        self.assertEqual(event.result_status, "success")

    def test_l1_is_denied_without_confirmation_callback(self):
        permissions = ToolPermissionService()
        registry = ToolRegistry(permissions)

        with patch(
            "core.tool_registry.webbrowser.open",
            return_value=True,
        ) as open_url:
            result = registry.execute_tool(
                "open_url",
                {"url": "https://example.com"},
            )

        self.assertIn("Permission denied", result)
        open_url.assert_not_called()
        self.assertEqual(permissions.events()[-1].result_status, "denied")

    def test_l1_confirmation_allows_exactly_one_external_action(self):
        requests = []
        permissions = ToolPermissionService(
            confirm_external=lambda request: (
                requests.append(request) or True
            )
        )
        registry = ToolRegistry(permissions)

        with patch.object(
            registry,
            "_execute_isolated",
            return_value="已使用默认浏览器打开：https://example.com/path",
        ) as execute_isolated:
            result = registry.execute_tool(
                "open_url",
                {"url": "https://example.com/path"},
            )

        self.assertIn("已使用默认浏览器打开", result)
        self.assertEqual(len(requests), 1)
        execute_isolated.assert_called_once()
        self.assertEqual(
            permissions.events()[-1].authorization,
            "user_confirmed",
        )

    def test_l2_and_l3_are_denied_even_with_external_confirmation(self):
        permissions = ToolPermissionService(
            confirm_external=lambda _request: True
        )
        for level in (
            ToolPermissionLevel.STATE_CHANGE,
            ToolPermissionLevel.HIGH_RISK,
        ):
            request, decision = permissions.authorize(
                "dangerous",
                level,
                {},
                "修改系统状态",
            )
            self.assertFalse(decision.allowed)
            self.assertEqual(request.level, level)

    def test_audit_arguments_redact_secrets_and_url_credentials(self):
        sanitized = ToolPermissionService.sanitize_arguments(
            {
                "api_key": "sk-secret",
                "url": (
                    "https://user:pass@example.com/path"
                    "?token=hidden&view=compact#private"
                ),
                "nested": {"password": "hidden"},
            }
        )

        self.assertEqual(sanitized["api_key"], "<redacted>")
        self.assertNotIn("user:pass", sanitized["url"])
        self.assertNotIn("#private", sanitized["url"])
        self.assertIn("token=%3Credacted%3E", sanitized["url"])
        self.assertEqual(
            sanitized["nested"]["password"],
            "<redacted>",
        )
        self.assertEqual(
            ToolPermissionService.sanitize_arguments(
                {"url": "https://example.com:not-a-port"}
            )["url"],
            "<invalid-url>",
        )

    def test_authorization_fingerprint_distinguishes_redacted_values(self):
        service = ToolPermissionService()
        first, _ = service.authorize(
            "open_url",
            ToolPermissionLevel.EXTERNAL_ACTION,
            {"url": "https://example.com/?token=one"},
            "打开网页",
        )
        second, _ = service.authorize(
            "open_url",
            ToolPermissionLevel.EXTERNAL_ACTION,
            {"url": "https://example.com/?token=two"},
            "打开网页",
        )

        self.assertEqual(first.arguments, second.arguments)
        self.assertNotEqual(
            first.authorization_fingerprint,
            second.authorization_fingerprint,
        )

    def test_cmd_is_not_in_application_allowlist(self):
        permissions = ToolPermissionService(
            confirm_external=lambda _request: True
        )
        registry = ToolRegistry(permissions)

        with patch("core.tool_registry.subprocess.Popen") as popen:
            result = registry.execute_tool(
                "open_application",
                {"app_name": "cmd"},
            )

        self.assertIn("未授权或不支持", result)
        popen.assert_not_called()
