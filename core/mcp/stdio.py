"""MCP stdio transport client using only the standard library.

This module implements the transport/lifecycle subset needed to discover and
invoke tools. It deliberately returns raw tool results to a caller that must
still apply Pixkin's ToolPermissionService before execution.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
    PermissionState,
)


LOGGER = logging.getLogger("desktop_pet.mcp.stdio")
MAX_MESSAGE_BYTES = 512 * 1024
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
)


class McpProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    server_id: str
    output_schema: Mapping[str, Any] = field(default_factory=dict)

    @property
    def namespaced_name(self) -> str:
        return f"mcp.{self.server_id}.{self.name}"

    def to_pixkin_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.namespaced_name,
                "description": self.description,
                "parameters": dict(self.input_schema),
            },
        }


class McpStdioClient:
    """One MCP server process and one serialized JSON-RPC request stream."""

    def __init__(
        self,
        command: list[str] | tuple[str, ...],
        *,
        server_id: str,
        cwd: str | None = None,
        protocol_versions: tuple[str, ...] = SUPPORTED_PROTOCOL_VERSIONS,
        permission_service: ContextPermissionService | None = None,
    ):
        if not command or any(not str(item).strip() for item in command):
            raise ValueError("MCP stdio command cannot be empty")
        self.command = tuple(str(item) for item in command)
        self.server_id = str(server_id or "").strip().lower()
        if not self.server_id:
            raise ValueError("MCP server_id cannot be empty")
        self.cwd = cwd
        self.protocol_versions = tuple(protocol_versions)
        # MCP starts an external process and must be default-deny even when
        # embedded outside the desktop application.  Callers opt in by
        # granting MCP/execute on this service (session-only or persistent).
        self.permission_service = permission_service or ContextPermissionService()
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        # A peer can send unsolicited/error responses; cap buffered messages
        # so a hostile or wedged server cannot grow this queue without bound.
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
        self._write_lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._next_id = 0
        self.protocol_version = ""
        self.server_capabilities: Mapping[str, Any] = {}
        self._job = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, *, timeout: float = 5.0) -> Mapping[str, Any]:
        if self.running:
            raise McpProtocolError("MCP server already running")
        if self.permission_service.state(PermissionResource.MCP) is PermissionState.ASK:
            raise McpProtocolError("启动常驻 MCP 服务需要会话级 MCP 授权")
        decision = self.permission_service.decide(
            PermissionResource.MCP,
            PermissionOperation.EXECUTE,
        )
        if not decision.allowed:
            raise McpProtocolError(f"MCP 权限未允许：{decision.reason}")
        try:
            creation_flags = 0
            if os.name == "nt":
                creation_flags = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            self._process = subprocess.Popen(
                list(self.command),
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=creation_flags,
            )
            self._attach_process_group()
        except OSError as exc:
            raise McpProtocolError("MCP server cannot start") from exc
        self._stop_event.clear()
        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"pixkin-mcp-{self.server_id}",
            daemon=True,
        )
        self._reader.start()
        try:
            result = self._request(
                "initialize",
                {
                    "protocolVersion": self.protocol_versions[0],
                    "capabilities": {},
                    "clientInfo": {"name": "Pixkin", "version": "1.5.0"},
                },
                timeout=timeout,
            )
        except Exception:
            self.stop(force=True)
            raise
        try:
            version = str(result.get("protocolVersion") or "")
            if version not in self.protocol_versions:
                raise McpProtocolError(
                    f"unsupported MCP protocol version: {version}"
                )
            self.protocol_version = version
            capabilities = result.get("capabilities", {})
            self.server_capabilities = (
                capabilities if isinstance(capabilities, Mapping) else {}
            )
            self._notify("notifications/initialized", {})
            return result
        except Exception:
            self.stop(force=True)
            raise

    def list_tools(self, *, timeout: float = 10.0) -> tuple[McpTool, ...]:
        self._ensure_permission()
        result = self._request("tools/list", {}, timeout=timeout)
        raw_tools = result.get("tools", [])
        if not isinstance(raw_tools, list):
            raise McpProtocolError("MCP tools/list result is invalid")
        tools: list[McpTool] = []
        for raw in raw_tools:
            if not isinstance(raw, Mapping):
                continue
            name = str(raw.get("name") or "").strip()
            schema = raw.get("inputSchema", {})
            if not name or not isinstance(schema, Mapping):
                continue
            description = str(raw.get("description") or raw.get("title") or name)
            output = raw.get("outputSchema", {})
            tools.append(
                McpTool(
                    name=name,
                    description=description[:2000],
                    input_schema=dict(schema),
                    output_schema=dict(output) if isinstance(output, Mapping) else {},
                    server_id=self.server_id,
                )
            )
        return tuple(tools)

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None, *, timeout: float = 30.0) -> Mapping[str, Any]:
        if not str(name or "").strip():
            raise ValueError("MCP tool name cannot be empty")
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
        decision = self.permission_service.decide(
            PermissionResource.MCP,
            PermissionOperation.EXECUTE,
            resolve_ask=False,
        )
        if not decision.allowed:
            raise McpProtocolError(f"MCP 权限已撤销：{decision.reason}")

    def stop(self, *, force: bool = False) -> None:
        process = self._process
        if process is None:
            return
        if not force and process.poll() is None:
            try:
                self._request("shutdown", {}, timeout=1.0)
            except McpProtocolError:
                pass
        self._stop_event.set()
        job = self._job
        if force and job is not None:
            try:
                win32job: Any = __import__("win32job")
                win32job.TerminateJobObject(job, 1)
            except Exception:
                LOGGER.debug("终止 MCP Job Object 失败", exc_info=True)
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._close_process_group()
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.0)
        self._process = None
        self._reader = None

    def _attach_process_group(self) -> None:
        """Contain MCP descendants so timeout/shutdown cannot orphan them."""
        process = self._process
        if process is None:
            return
        if os.name == "nt":
            try:
                win32job: Any = __import__("win32job")

                job = win32job.CreateJobObject(None, "")
                info = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation
                )
                info["BasicLimitInformation"]["LimitFlags"] |= (
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                )
                win32job.SetInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation, info
                )
                process_handle = getattr(process, "_handle", None)
                if process_handle is not None:
                    win32job.AssignProcessToJobObject(job, process_handle)
                    self._job = job
            except Exception:
                LOGGER.debug("绑定 MCP Job Object 失败或不受支持", exc_info=True)

    def _close_process_group(self) -> None:
        job = self._job
        self._job = None
        if job is not None:
            try:
                job.Close()
            except Exception:
                LOGGER.debug("关闭 MCP Job Object 失败", exc_info=True)

    def _request(self, method: str, params: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        if not self.running:
            raise McpProtocolError("MCP server is not running")
        process = self._process
        assert process is not None and process.stdin is not None
        with self._call_lock:
            self._next_id += 1
            request_id = self._next_id
            encoded = (
                json.dumps(
                    {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)},
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            if len(encoded) > MAX_MESSAGE_BYTES:
                raise McpProtocolError("MCP request exceeds message limit")
            try:
                with self._write_lock:
                    process.stdin.write(encoded)
                    process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpProtocolError("MCP stdio write failed") from exc
            deadline = time.monotonic() + max(0.05, float(timeout))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.stop(force=True)
                    raise McpProtocolError(f"MCP request timeout: {method}")
                try:
                    response = self._responses.get(timeout=remaining)
                except queue.Empty as exc:
                    self.stop(force=True)
                    raise McpProtocolError(f"MCP request timeout: {method}") from exc
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise McpProtocolError(str(response["error"]))
                result = response.get("result", {})
                if not isinstance(result, dict):
                    raise McpProtocolError("MCP response result must be an object")
                return result

    def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise McpProtocolError("MCP server is not running")
        encoded = (
            json.dumps({"jsonrpc": "2.0", "method": method, "params": dict(params)}, ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise McpProtocolError("MCP request exceeds message limit")
        with self._write_lock:
            process.stdin.write(encoded)
            process.stdin.flush()

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while not self._stop_event.is_set():
            try:
                # Bound the read itself; checking ``len(line)`` after an
                # unbounded readline would already have allocated attacker
                # controlled memory.
                line = process.stdout.readline(MAX_MESSAGE_BYTES + 1)
            except OSError:
                return
            if not line:
                return
            if len(line) > MAX_MESSAGE_BYTES:
                LOGGER.warning("MCP message too large server=%s", self.server_id)
                self._stop_event.set()
                return
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                LOGGER.warning("invalid MCP message server=%s", self.server_id)
                continue
            if isinstance(message, dict) and "id" in message:
                try:
                    self._responses.put_nowait(message)
                except queue.Full:
                    LOGGER.warning(
                        "MCP response queue full server=%s", self.server_id
                    )
                    self._stop_event.set()
                    return
