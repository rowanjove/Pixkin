import unittest

from core.mcp import McpProtocolError, McpStreamableHttpClient
from core.runtime.permissions import (
    PermissionResource,
    PermissionState,
)


class FakeResponse:
    def __init__(self, payload, *, content_type="application/json", status=200, session_id="s1"):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id
        self._payload = payload
        self.content = str(payload).encode()
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        request = kwargs["json"]
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            return FakeResponse({"id": request_id, "result": {"protocolVersion": "2025-11-25", "capabilities": {}}})
        if method == "notifications/initialized":
            return FakeResponse({}, status=202, session_id=None)
        if method == "tools/list":
            return FakeResponse({"id": request_id, "result": {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}})
        return FakeResponse({"id": request_id, "result": {"content": [{"type": "text", "text": "ok"}]}})

    def close(self):
        self.closed = True


class FailingNotifySession(FakeSession):
    def post(self, url, **kwargs):
        request = kwargs["json"]
        if request.get("method") == "notifications/initialized":
            self.calls.append((url, kwargs))
            return FakeResponse({}, status=500, session_id=None)
        return super().post(url, **kwargs)


class McpHttpTests(unittest.TestCase):
    def test_streamable_http_lifecycle_and_tool_mapping(self):
        session = FakeSession()
        client = McpStreamableHttpClient("http://127.0.0.1:8765/mcp", "local", session=session)
        client.permission_service.set_state(
            PermissionResource.MCP,
            PermissionState.ALLOW_SESSION,
            session_only=True,
        )
        client.start()
        tools = client.list_tools()
        self.assertEqual(tools[0].namespaced_name, "mcp.local.echo")
        self.assertEqual(client.call_tool("echo", {"x": 1})["content"][0]["text"], "ok")
        self.assertTrue(any(call[1]["headers"].get("Mcp-Session-Id") == "s1" for call in session.calls[2:]))
        client.stop()
        self.assertFalse(client.running)

    def test_public_http_and_credentials_are_rejected(self):
        with self.assertRaises(ValueError):
            McpStreamableHttpClient("http://example.com/mcp", "remote")
        with self.assertRaises(ValueError):
            McpStreamableHttpClient("https://user:pass@example.com/mcp", "remote")

    def test_failed_initialized_notification_closes_half_open_session(self):
        session = FailingNotifySession()
        client = McpStreamableHttpClient("http://127.0.0.1:8765/mcp", "local", session=session)
        client.permission_service.set_state(
            PermissionResource.MCP,
            PermissionState.ALLOW_SESSION,
            session_only=True,
        )
        with self.assertRaises(McpProtocolError):
            client.start()
        self.assertFalse(client.running)
        self.assertTrue(session.closed)

    def test_tool_calls_are_blocked_after_permission_revocation(self):
        session = FakeSession()
        client = McpStreamableHttpClient("http://127.0.0.1:8765/mcp", "local", session=session)
        client.permission_service.set_state(
            PermissionResource.MCP,
            PermissionState.ALLOW_SESSION,
            session_only=True,
        )
        client.start()
        client.permission_service.set_state(
            PermissionResource.MCP,
            PermissionState.DENY,
        )
        with self.assertRaisesRegex(McpProtocolError, "已撤销"):
            client.call_tool("echo")
        self.assertEqual(
            [call[1]["json"]["method"] for call in session.calls],
            ["initialize", "notifications/initialized"],
        )
        client.stop(force=True)


if __name__ == "__main__":
    unittest.main()
