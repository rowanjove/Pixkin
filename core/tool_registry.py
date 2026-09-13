import ast
import copy
import datetime
import json
import math
import multiprocessing as multiprocessing_module
import os
import pickle
import platform
import shutil
import subprocess
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Callable
from urllib.parse import urlparse

from core.mcp.stdio import McpStdioClient, McpTool

from core.services.tool_permission_service import (
    ToolPermissionLevel,
    ToolPermissionService,
)


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    source: str
    permission_level: ToolPermissionLevel
    side_effect: str
    timeout_seconds: float
    requires_confirmation: bool
    isolated: bool


@dataclass(frozen=True)
class ToolExecutionContext:
    session_id: str = ""
    character_id: str = ""
    plugin_id: str = ""
    user_confirmed: bool = False


class _ToolTimeout(RuntimeError):
    """Internal marker used by both process and thread executors."""


_TOOL_TIMEOUT_RESULT = object()
MAX_TOOL_RESULT_CHARS = 16 * 1024


def bound_tool_result(
    value: Any,
    *,
    limit: int = MAX_TOOL_RESULT_CHARS,
    label: str = "tool result",
) -> str:
    """Bound untrusted text before it can re-enter a model prompt."""
    text = str(value)
    if len(text) <= limit:
        return text
    marker = f"\n...[{label} truncated; original_chars={len(text)}]...\n"
    available = max(0, int(limit) - len(marker))
    head = available // 2
    tail = available - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def _scrub_sensitive_environment() -> None:
    """Keep credential-shaped variables out of isolated tool processes."""
    blocked_markers = (
        "API_KEY",
        "APIKEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "CREDENTIAL",
        "AUTHORIZATION",
    )
    for key in tuple(os.environ):
        if any(marker in key.upper() for marker in blocked_markers):
            os.environ.pop(key, None)


def _run_tool_callable(connection, handler, execution_args):
    """Run a picklable tool in a killable child process."""
    _scrub_sensitive_environment()
    try:
        result = (
            handler(**execution_args)
            if execution_args
            else handler()
        )
        connection.send(("ok", result))
    except BaseException as exc:  # pragma: no cover - exercised in child
        try:
            connection.send(("error", type(exc).__name__, str(exc)[:500]))
        except Exception:
            pass
    finally:
        connection.close()


class ToolRegistry:
    """Tool Calls (Function Calling) 注册与调度中心"""

    def __init__(
        self,
        permission_service: ToolPermissionService | None = None,
    ):
        self._tools_schema: List[Dict[str, Any]] = []
        self._tool_handlers: Dict[str, Callable] = {}
        self._tool_permissions = {}
        self._tool_metadata: Dict[str, ToolMetadata] = {}
        self.permission_service = (
            permission_service or ToolPermissionService()
        )
        self._pending_tools: set[str] = set()
        self._pending_lock = threading.RLock()
        self._register_default_tools()

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable,
        permission_level: ToolPermissionLevel = ToolPermissionLevel.READ_ONLY,
        side_effect: str = "",
        *,
        timeout_seconds: float = 30.0,
        requires_confirmation: bool | None = None,
        source: str = "builtin",
        isolated: bool | None = None,
    ):
        """注册一个新的工具"""
        clean_name = str(name or "").strip()
        if not clean_name or len(clean_name) > 128 or clean_name in self._tool_handlers:
            raise ValueError(f"duplicate or empty tool name: {name}")
        if not callable(handler):
            raise ValueError("tool handler must be callable")
        if not isinstance(parameters, dict):
            raise ValueError("tool parameters must be an object")
        if len(json.dumps(parameters, ensure_ascii=False, default=str)) > 64 * 1024:
            raise ValueError("tool schema exceeds 64 KiB")
        clean_description = str(description or "").strip()
        if len(clean_description) > 4_000:
            raise ValueError("tool description exceeds 4000 characters")
        permission_level = ToolPermissionLevel(permission_level)
        if isolated is None:
            try:
                pickle.dumps(handler)
                isolated = True
            except (pickle.PickleError, TypeError, AttributeError):
                if permission_level != ToolPermissionLevel.READ_ONLY:
                    raise ValueError(
                        "有副作用的工具必须使用可隔离的可序列化 handler"
                    )
                isolated = False
        elif isolated:
            try:
                pickle.dumps(handler)
            except (pickle.PickleError, TypeError, AttributeError) as exc:
                raise ValueError(
                    "隔离工具 handler 必须可序列化"
                ) from exc
        clean_side_effect = str(side_effect or "")[:500]
        schema = {
            "type": "function",
            "function": {
                "name": clean_name,
                "description": clean_description,
                "parameters": copy.deepcopy(parameters),
            }
        }
        self._tools_schema.append(schema)
        self._tool_handlers[clean_name] = handler
        self._tool_permissions[clean_name] = (permission_level, clean_side_effect)
        self._tool_metadata[clean_name] = ToolMetadata(
            name=clean_name,
            source=str(source or "builtin"),
            permission_level=permission_level,
            side_effect=clean_side_effect,
            timeout_seconds=max(0.1, float(timeout_seconds)),
            requires_confirmation=(
                permission_level != ToolPermissionLevel.READ_ONLY
                if requires_confirmation is None
                else bool(requires_confirmation)
            ),
            isolated=bool(isolated),
        )

    def get_tool_metadata(self, name: str) -> "ToolMetadata":
        try:
            return self._tool_metadata[name]
        except KeyError as exc:
            raise KeyError(f"Tool '{name}' not found.") from exc

    def register_mcp_tool(
        self,
        client: McpStdioClient,
        tool: McpTool,
        *,
        permission_level: ToolPermissionLevel = ToolPermissionLevel.EXTERNAL_ACTION,
        timeout_seconds: float = 30.0,
    ) -> None:
        """Adapt one MCP tool to the same guarded registry as built-ins."""
        self.register_tool(
            name=tool.namespaced_name,
            description=tool.description,
            parameters=dict(tool.input_schema),
            handler=lambda **args: client.call_tool(
                tool.name,
                args,
                timeout=timeout_seconds,
            ),
            permission_level=permission_level,
            side_effect=f"MCP server {tool.server_id} tool invocation",
            timeout_seconds=timeout_seconds,
            source=f"mcp:{tool.server_id}",
            isolated=False,
        )

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """获取所有工具的 OpenAI schema 声明"""
        return copy.deepcopy(self._tools_schema)

    def execute_tool(
        self,
        name: str,
        args: Dict[str, Any],
        *,
        context: ToolExecutionContext | None = None,
    ) -> str:
        """执行调用的工具并返回字符串结果"""
        if name not in self._tool_handlers:
            return f"Error: Tool '{name}' not found."
        if not isinstance(args, dict):
            return "Error: tool arguments must be an object."
        try:
            if len(json.dumps(args, ensure_ascii=False, default=str)) > 256 * 1024:
                return "Error: tool arguments exceed 256 KiB."
        except (TypeError, ValueError):
            return "Error: tool arguments are not serializable."
        try:
            execution_args = copy.deepcopy(args)
        except (TypeError, ValueError) as exc:
            return f"Error: tool arguments are not executable ({type(exc).__name__})."
        level, side_effect = self._tool_permissions[name]
        metadata = self._tool_metadata[name]
        with self._pending_lock:
            if name in self._pending_tools:
                return (
                    f"Error: Tool '{name}' is still running after a previous "
                    "timeout; do not retry yet."
                )
        request, decision = self.permission_service.authorize(
            name,
            level,
            execution_args,
            side_effect,
        )
        started = time.perf_counter()
        if not decision.allowed:
            self.permission_service.record(
                request,
                decision,
                result_status="denied",
                duration_ms=0,
                context=context,
            )
            return f"Permission denied for tool '{name}': {decision.reason}"
        try:
            handler = self._tool_handlers[name]
            if metadata.isolated:
                result = self._execute_isolated(
                    handler,
                    execution_args,
                    metadata.timeout_seconds,
                )
            else:
                result = self._execute_threaded(
                    name,
                    handler,
                    execution_args,
                    metadata.timeout_seconds,
                    request,
                    decision,
                    context,
                    started,
                )
                if result is _TOOL_TIMEOUT_RESULT:
                    return f"Error: Tool '{name}' timed out."
            self.permission_service.record(
                request,
                decision,
                result_status="success",
                duration_ms=round(
                    (time.perf_counter() - started) * 1000
                ),
                context=context,
            )
            return bound_tool_result(result)
        except _ToolTimeout:
            self.permission_service.record(
                request,
                decision,
                result_status="timeout",
                duration_ms=round((time.perf_counter() - started) * 1000),
                context=context,
            )
            return f"Error: Tool '{name}' timed out."
        except Exception as e:
            self.permission_service.record(
                request,
                decision,
                result_status="error",
                duration_ms=round(
                    (time.perf_counter() - started) * 1000
                ),
                context=context,
            )
            # Never send raw exception text to the model: provider/client
            # errors may contain local paths, URLs or credential-shaped data.
            return f"Error executing tool '{name}': {type(e).__name__}"

    def _execute_isolated(
        self,
        handler: Callable,
        execution_args: Dict[str, Any],
        timeout_seconds: float,
    ) -> Any:
        """Execute a serializable handler in a killable child process."""
        context = multiprocessing_module.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_run_tool_callable,
            args=(child, handler, execution_args),
            name="pixkin-tool-isolated",
        )
        process.daemon = True
        try:
            process.start()
            child.close()
            if not parent.poll(max(0.05, float(timeout_seconds))):
                process.terminate()
                process.join(timeout=1.0)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1.0)
                raise _ToolTimeout()
            payload = parent.recv()
            if not payload or payload[0] == "error":
                error_type = payload[1] if len(payload) > 1 else "ToolError"
                message = payload[2] if len(payload) > 2 else "tool failed"
                raise RuntimeError(f"{error_type}: {message}")
            return payload[1]
        finally:
            try:
                parent.close()
            except OSError:
                pass
            if process.pid is not None:
                process.join(timeout=1.0)
            process.close()

    def _execute_threaded(
        self,
        name: str,
        handler: Callable,
        execution_args: Dict[str, Any],
        timeout_seconds: float,
        request,
        decision,
        context,
        started: float,
    ) -> Any:
        """Run trusted legacy handlers with bounded grace and retry fencing."""
        executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="pixkin-tool",
        )
        future = (
            executor.submit(handler, **execution_args)
            if execution_args
            else executor.submit(handler)
        )
        try:
            try:
                return future.result(timeout=timeout_seconds)
            except FutureTimeout:
                # Python cannot kill an already-running thread. Give it a
                # short grace period so normal short-lived legacy handlers do
                # not outlive the returned timeout result. If it still runs,
                # fence retries until its completion callback fires.
                try:
                    future.result(timeout=1.0)
                    self.permission_service.record(
                        request,
                        decision,
                        result_status="timeout",
                        duration_ms=round((time.perf_counter() - started) * 1000),
                        context=context,
                    )
                    return _TOOL_TIMEOUT_RESULT
                except FutureTimeout:
                    with self._pending_lock:
                        self._pending_tools.add(name)

                    def clear_pending(_future) -> None:
                        with self._pending_lock:
                            self._pending_tools.discard(name)

                    future.add_done_callback(clear_pending)
                    self.permission_service.record(
                        request,
                        decision,
                        result_status="timeout_pending",
                        duration_ms=round((time.perf_counter() - started) * 1000),
                        context=context,
                    )
                    return _TOOL_TIMEOUT_RESULT
                except Exception:
                    self.permission_service.record(
                        request,
                        decision,
                        result_status="timeout",
                        duration_ms=round((time.perf_counter() - started) * 1000),
                        context=context,
                    )
                    return _TOOL_TIMEOUT_RESULT
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _register_default_tools(self):
        """注册内置工具"""
        self.register_tool(
            name="list_available_tools",
            description="列出 Pixkin 当前允许大模型调用的本地工具及其用途。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=self._tool_list_available_tools,
        )

        # 工具 1: 获取当前时间
        self.register_tool(
            name="get_current_time",
            description="获取当前的本地日期与时间（格式：YYYY-MM-DD HH:MM:SS 星期几）。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=self._tool_get_current_time
        )

        # 工具 2: 获取系统信息
        self.register_tool(
            name="get_system_info",
            description="获取当前运行环境的操作系统信息。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=self._tool_get_system_info
        )

        # 工具 3: 打开常见系统应用
        self.register_tool(
            name="open_application",
            description="在 Windows 系统上打开白名单内的常用应用，例如记事本、计算器、画图或文件资源管理器；执行前需要用户确认。",
            parameters={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "要打开的应用名称，如 notepad、calc、mspaint 或 explorer"
                    }
                },
                "required": ["app_name"]
            },
            handler=self._tool_open_application,
            permission_level=ToolPermissionLevel.EXTERNAL_ACTION,
            side_effect="启动本地白名单应用",
        )

        self.register_tool(
            name="calculate_expression",
            description="安全计算只包含数字、括号和常见算术运算符的表达式。",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "例如：(12.5 + 7.5) * 3 / 2"
                    }
                },
                "required": ["expression"]
            },
            handler=self._tool_calculate_expression,
        )

        self.register_tool(
            name="get_disk_usage",
            description="查看当前用户磁盘的容量、已用空间和剩余空间。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=self._tool_get_disk_usage,
        )

        self.register_tool(
            name="open_url",
            description="使用默认浏览器打开经过校验的 HTTP 或 HTTPS 网页；执行前需要用户确认。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "完整的 http:// 或 https:// 网页地址"
                    }
                },
                "required": ["url"]
            },
            handler=self._tool_open_url,
            permission_level=ToolPermissionLevel.EXTERNAL_ACTION,
            side_effect="在默认浏览器打开外部网页",
        )

    def _tool_list_available_tools(self) -> str:
        lines = []
        for schema in self._tools_schema:
            function = schema["function"]
            lines.append(f"- {function['name']}: {function['description']}")
        return "Pixkin 当前可用工具：\n" + "\n".join(lines)

    @staticmethod
    def _tool_get_current_time() -> str:
        now = datetime.datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday_str = weekdays[now.weekday()]
        return f"{now.strftime('%Y-%m-%d %H:%M:%S')} {weekday_str}"

    @staticmethod
    def _tool_get_system_info() -> str:
        return f"OS: {platform.system()} {platform.release()} (Architecture: {platform.architecture()[0]})"

    @staticmethod
    def _tool_open_application(app_name: str) -> str:
        app_key = app_name.lower().strip()
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        system32 = system_root / "System32"
        allowed_apps = {
            "notepad": "notepad.exe",
            "calc": "calc.exe",
            "calculator": "calc.exe",
            "mspaint": "mspaint.exe",
            "explorer": "explorer.exe",
        }
        if app_key not in allowed_apps:
            return f"未授权或不支持打开该应用: '{app_name}'。支持的应用有: {', '.join(allowed_apps.keys())}"

        exe_name = allowed_apps[app_key]
        if exe_name == "explorer.exe":
            target_path = system_root / "explorer.exe"
        else:
            target_path = system32 / exe_name

        cmd = str(target_path) if target_path.is_file() else exe_name
        try:
            subprocess.Popen(cmd)
            return f"已为您成功启动应用: {app_name}"
        except OSError as exc:
            return f"启动应用 '{app_name}' 失败: {exc}"

    @staticmethod
    def _tool_calculate_expression(expression: str) -> str:
        source = str(expression or "").strip()
        if not source or len(source) > 160:
            raise ValueError("算式不能为空，且长度不能超过 160 个字符")
        tree = ast.parse(source, mode="eval")
        binary = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b,
            ast.FloorDiv: lambda a, b: a // b,
            ast.Mod: lambda a, b: a % b,
            ast.Pow: lambda a, b: a ** b,
        }
        unary = {
            ast.UAdd: lambda value: value,
            ast.USub: lambda value: -value,
        }

        def evaluate(node):
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Constant):
                if isinstance(node.value, bool) or not isinstance(
                    node.value, (int, float)
                ):
                    raise ValueError("算式只能包含数字")
                return node.value
            if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
                return unary[type(node.op)](evaluate(node.operand))
            if isinstance(node, ast.BinOp) and type(node.op) in binary:
                left = evaluate(node.left)
                right = evaluate(node.right)
                if isinstance(node.op, ast.Pow) and abs(right) > 12:
                    raise ValueError("指数绝对值不能超过 12")
                return binary[type(node.op)](left, right)
            raise ValueError("算式包含不支持的内容")

        result = evaluate(tree)
        if isinstance(result, complex) or not math.isfinite(float(result)):
            raise ValueError("计算结果无效或超出范围")
        if abs(float(result)) > 1e100:
            raise ValueError("计算结果过大")
        return f"{source} = {result}"

    @staticmethod
    def _tool_get_disk_usage() -> str:
        root = Path.home().anchor or str(Path.home())
        total, used, free = shutil.disk_usage(root)
        gb = 1024 ** 3
        return (
            f"磁盘 {root}：总容量 {total / gb:.1f} GB，"
            f"已用 {used / gb:.1f} GB，剩余 {free / gb:.1f} GB"
        )

    @staticmethod
    def _tool_open_url(url: str) -> str:
        target = str(url or "").strip()
        if len(target) > 2048:
            raise ValueError("网址过长")
        try:
            parsed = urlparse(target)
        except ValueError as exc:
            raise ValueError("只允许打开有效的 HTTP 或 HTTPS 网页") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "只允许打开不含凭据的 HTTP 或 HTTPS 网页"
            )
        opened = webbrowser.open(target, new=2)
        if not opened:
            raise RuntimeError("默认浏览器未接受打开请求")
        return f"已使用默认浏览器打开：{target}"
