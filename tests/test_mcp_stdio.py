import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from core.mcp.stdio import McpStdioClient
from core.mcp.stdio import McpProtocolError
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionResource,
    PermissionState,
)


MCP_CODE = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        req = json.loads(line)
        method = req.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": req["params"]["arguments"].get("value", "")}], "isError": False}
        elif method == "shutdown":
            print(json.dumps({"jsonrpc":"2.0", "id":req["id"], "result":{}}), flush=True)
            break
        else:
            if "id" not in req:
                continue
            result = {}
        if "id" in req:
            print(json.dumps({"jsonrpc":"2.0", "id":req["id"], "result":result}), flush=True)
    """
)


class McpStdioTests(unittest.TestCase):
    def test_permission_gate_blocks_process_start(self):
        client = McpStdioClient(
            [sys.executable, "-c", "pass"],
            server_id="blocked",
            permission_service=ContextPermissionService(),
        )
        with self.assertRaises(McpProtocolError):
            client.start()

    def test_initialize_list_and_call(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "server.py"
            script.write_text(MCP_CODE, encoding="utf-8")
            client = McpStdioClient(
                [sys.executable, "-u", str(script)],
                server_id="demo",
            )
            client.permission_service.set_state(
                PermissionResource.MCP,
                PermissionState.ALLOW_SESSION,
                session_only=True,
            )
            client.start()
            tools = client.list_tools()
            self.assertEqual(tools[0].namespaced_name, "mcp.demo.echo")
            self.assertEqual(client.call_tool("echo", {"value": "ok"})["content"][0]["text"], "ok")
            client.stop()
            self.assertFalse(client.running)

    def test_force_stop_cleans_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "server.py"
            script.write_text(MCP_CODE, encoding="utf-8")
            client = McpStdioClient(
                [sys.executable, "-u", str(script)],
                server_id="demo_force",
            )
            client.permission_service.set_state(
                PermissionResource.MCP,
                PermissionState.ALLOW_SESSION,
                session_only=True,
            )
            client.start()
            self.assertTrue(client.running)
            client.stop(force=True)
            self.assertFalse(client.running)
            self.assertIsNone(client._job)

    def test_tool_calls_are_blocked_after_permission_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "server.py"
            script.write_text(MCP_CODE, encoding="utf-8")
            permissions = ContextPermissionService()
            client = McpStdioClient(
                [sys.executable, "-u", str(script)],
                server_id="revoked",
                permission_service=permissions,
            )
            permissions.set_state(
                PermissionResource.MCP,
                PermissionState.ALLOW_SESSION,
                session_only=True,
            )
            client.start()
            permissions.set_state(PermissionResource.MCP, PermissionState.DENY)
            with self.assertRaisesRegex(McpProtocolError, "已撤销"):
                client.call_tool("echo")
            client.stop(force=True)


if __name__ == "__main__":
    unittest.main()
