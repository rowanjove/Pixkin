"""MCP Streamable HTTP client with strict endpoint and response limits."""

from __future__ import annotations

import json
from dataclasses import dataclass
from collections.abc import Callable, Iterable
from typing import Any, Mapping, cast
from urllib.parse import urlsplit

import requests

from core.mcp.stdio import McpProtocolError, McpTool, SUPPORTED_PROTOCOL_VERSIONS
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
    PermissionState,
)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 512 * 1024


@dataclass
class McpStreamableHttpClient:
    endpoint: str
    server_id: str
    timeout_seconds: float = 30.0
    session: requests.Session | None = None
    permission_service: ContextPermissionService | None = None

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(str(self.endpoint or "").strip())
        except ValueError as exc:
            raise ValueError("invalid MCP HTTP endpoint") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid MCP HTTP endpoint")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("insecure MCP HTTP endpoint")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("MCP HTTP endpoint must not contain credentials or fragments")
        self.endpoint = str(self.endpoint).strip().rstrip("/")
        self.server_id = str(self.server_id or "").strip().lower()
        if not self.server_id:
            raise ValueError("MCP server_id cannot be empty")
        self.timeout_seconds = min(120.0, max(1.0, float(self.timeout_seconds)))
        self.session = self.session or requests.Session()
        # External HTTP MCP servers are default-deny outside the app too;
        # callers must explicitly grant MCP/execute before initialization.
        self.permission_service = self.permission_service or ContextPermissionService()
        self.protocol_version = ""
        self.server_capabilities: Mapping[str, Any] = {}
        self.session_id = ""
        self._next_id = 0

    @property
    def running(self) -> bool:
        return bool(self.protocol_version)

    def start(self, *, timeout: float | None = None) -> Mapping[str, Any]:
        permission_service = self.permission_service
        if permission_service is None:
            raise McpProtocolError("MCP 权限服务不可用")
        decision = permission_service.decide(
            PermissionResource.MCP,
            PermissionOperation.EXECUTE,
        )
        if permission_service.state(PermissionResource.MCP) is PermissionState.ASK:
            raise McpProtocolError("启动常驻 MCP 服务需要会话级 MCP 授权")
        if not decision.allowed:
            raise McpProtocolError(f"MCP 权限未允许：{decision.reason}")
        try:
            result = self._request(
                "initialize",
                {
                    "protocolVersion": SUPPORTED_PROTOCOL_VERSIONS[0],
                    "capabilities": {},
                    "clientInfo": {"name": "Pixkin", "version": "1.5.0"},
                },
                timeout=timeout,
            )
            version = str(result.get("protocolVersion") or "")
            if version not in SUPPORTED_PROTOCOL_VERSIONS:
                raise McpProtocolError(f"unsupported MCP protocol version: {version}")
            self.protocol_version = version
            capabilities = result.get("capabilities", {})
            self.server_capabilities = capabilities if isinstance(capabilities, Mapping) else {}
            self._notify("notifications/initialized", {})
            return result
        except Exception:
            # A failed initialized notification must not leave a half-open
            # session that subsequent callers could mistake for a live server.
            self.stop(force=True)
            raise

    def list_tools(self, *, timeout: float | None = None) -> tuple[McpTool, ...]:
        self._ensure_permission()
        result = self._request("tools/list", {}, timeout=timeout)
        raw_tools = result.get("tools", [])
        if not isinstance(raw_tools, list):
            raise McpProtocolError("MCP tools/list result is invalid")
        tools = []
        for raw in raw_tools:
            if not isinstance(raw, Mapping):
                continue
            name = str(raw.get("name") or "").strip()
            schema = raw.get("inputSchema", {})
            if name and isinstance(schema, Mapping):
                output = raw.get("outputSchema", {})
                tools.append(McpTool(
                    name=name,
                    description=str(raw.get("description") or name)[:2000],
                    input_schema=dict(schema),
                    output_schema=dict(output) if isinstance(output, Mapping) else {},
                    server_id=self.server_id,
                ))
        return tuple(tools)

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None, *, timeout: float | None = None) -> Mapping[str, Any]:
        self._ensure_permission()
        result = self._request(
            "tools/call",
            {"name": str(name), "arguments": dict(arguments or {})},
            timeout=timeout,
        )
        if not isinstance(result, Mapping):
            raise McpProtocolError("MCP tools/call result is invalid")
        return dict(result)

    def _ensure_permission(self) -> None:
        permission_service = self.permission_service
        if permission_service is None:
            raise McpProtocolError("MCP 权限服务不可用")
        decision = permission_service.decide(
            PermissionResource.MCP,
            PermissionOperation.EXECUTE,
            resolve_ask=False,
        )
        if not decision.allowed:
            raise McpProtocolError(f"MCP 权限已撤销：{decision.reason}")

    def stop(self, *, force: bool = False) -> None:
        if self.running and not force:
            try:
                self._notify("shutdown", {})
            except McpProtocolError:
                pass
        self.protocol_version = ""
        self.session_id = ""
        close = getattr(self.session, "close", None)
        if callable(close):
            close()

    close = stop

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "Pixkin-MCP/1.5",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _request(self, method: str, params: Mapping[str, Any], *, timeout: float | None) -> dict[str, Any]:
        if method != "initialize" and not self.running:
            raise McpProtocolError("MCP HTTP server is not initialized")
        session = self.session
        if session is None:
            raise McpProtocolError("MCP HTTP session is closed")
        self._next_id += 1
        request_id = self._next_id
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": dict(params),
        }
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise McpProtocolError("MCP HTTP request exceeds size limit")
        response = session.post(
            self.endpoint,
            headers=self._headers(),
            json=request,
            timeout=timeout or self.timeout_seconds,
            allow_redirects=False,
            stream=True,
        )
        try:
            if 300 <= int(response.status_code) < 400:
                raise McpProtocolError("MCP HTTP endpoint must not redirect")
            if int(response.status_code) >= 400:
                raise McpProtocolError(f"MCP HTTP status {response.status_code}")
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                self.session_id = str(session_id)[:256]
            content, streamed = self._bounded_response_content(response)
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if "text/event-stream" in content_type:
                try:
                    event_text = content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise McpProtocolError("invalid MCP SSE payload") from exc
                for line in event_text.splitlines():
                    if line.startswith("data:"):
                        try:
                            message = json.loads(line[5:].strip())
                        except json.JSONDecodeError as exc:
                            raise McpProtocolError("invalid MCP SSE payload") from exc
                        return self._result(message, request_id=request_id)
                raise McpProtocolError("MCP SSE response did not contain data")
            try:
                payload = (
                    json.loads(content.decode("utf-8"))
                    if streamed
                    else response.json()
                )
                return self._result(payload, request_id=request_id)
            except (UnicodeDecodeError, ValueError, TypeError) as exc:
                raise McpProtocolError("invalid MCP JSON response") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _result(message: Any, *, request_id: int) -> dict[str, Any]:
        if not isinstance(message, Mapping):
            raise McpProtocolError("MCP response must be an object")
        if message.get("id") != request_id:
            raise McpProtocolError("MCP response id does not match request")
        if "error" in message:
            raise McpProtocolError(str(message["error"]))
        result = message.get("result", {})
        if not isinstance(result, Mapping):
            raise McpProtocolError("MCP response result must be an object")
        return dict(result)

    def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        session = self.session
        if session is None:
            raise McpProtocolError("MCP HTTP session is closed")
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": dict(params),
        }
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise McpProtocolError("MCP HTTP request exceeds size limit")
        response = session.post(
            self.endpoint,
            headers=self._headers(),
            json=request,
            timeout=self.timeout_seconds,
            allow_redirects=False,
            stream=True,
        )
        try:
            if 300 <= int(response.status_code) < 400:
                raise McpProtocolError("MCP HTTP endpoint must not redirect")
            if int(response.status_code) >= 400:
                raise McpProtocolError(f"MCP notification status {response.status_code}")
            self._bounded_response_content(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _bounded_response_content(response) -> tuple[bytes, bool]:
        """Read an HTTP body incrementally and enforce the cap before parsing."""
        declared = response.headers.get("Content-Length")
        try:
            if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
                raise McpProtocolError("MCP HTTP response exceeds size limit")
        except (TypeError, ValueError) as exc:
            raise McpProtocolError("MCP HTTP response size is invalid") from exc
        iterator = getattr(response, "iter_content", None)
        if not callable(iterator):
            content = bytes(getattr(response, "content", b""))
            if len(content) > MAX_RESPONSE_BYTES:
                raise McpProtocolError("MCP HTTP response exceeds size limit")
            return content, False
        chunks: list[bytes] = []
        size = 0
        bounded_iterator = cast(
            Callable[..., Iterable[Any]],
            iterator,
        )
        for chunk in bounded_iterator(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise McpProtocolError("MCP HTTP response exceeds size limit")
            chunks.append(bytes(chunk))
        return b"".join(chunks), True


__all__ = ["McpStreamableHttpClient"]
