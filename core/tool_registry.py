import ast
import copy
import datetime
import math
import platform
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Dict, Any, List, Callable
from urllib.parse import urlparse

from core.services.tool_permission_service import (
    ToolPermissionLevel,
    ToolPermissionService,
)


class ToolRegistry:
    """Tool Calls (Function Calling) 注册与调度中心"""

    def __init__(
        self,
        permission_service: ToolPermissionService | None = None,
    ):
        self._tools_schema: List[Dict[str, Any]] = []
        self._tool_handlers: Dict[str, Callable] = {}
        self._tool_permissions = {}
        self.permission_service = (
            permission_service or ToolPermissionService()
        )
        self._register_default_tools()

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable,
        permission_level: ToolPermissionLevel = ToolPermissionLevel.READ_ONLY,
        side_effect: str = "",
    ):
        """注册一个新的工具"""
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters
            }
        }
        self._tools_schema.append(schema)
        self._tool_handlers[name] = handler
        self._tool_permissions[name] = (permission_level, side_effect)

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """获取所有工具的 OpenAI schema 声明"""
        return self._tools_schema

    def execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        """执行调用的工具并返回字符串结果"""
        if name not in self._tool_handlers:
            return f"Error: Tool '{name}' not found."
        execution_args = copy.deepcopy(args)
        level, side_effect = self._tool_permissions[name]
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
            )
            return f"Permission denied for tool '{name}': {decision.reason}"
        try:
            handler = self._tool_handlers[name]
            result = (
                handler(**execution_args)
                if execution_args
                else handler()
            )
            self.permission_service.record(
                request,
                decision,
                result_status="success",
                duration_ms=round(
                    (time.perf_counter() - started) * 1000
                ),
            )
            return str(result)
        except Exception as e:
            self.permission_service.record(
                request,
                decision,
                result_status="error",
                duration_ms=round(
                    (time.perf_counter() - started) * 1000
                ),
            )
            return f"Error executing tool '{name}': {str(e)}"

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

    def _tool_open_application(self, app_name: str) -> str:
        allowed_apps = {
            "notepad": "notepad.exe",
            "calc": "calc.exe",
            "calculator": "calc.exe",
            "mspaint": "mspaint.exe",
            "explorer": "explorer.exe"
        }
        app_key = app_name.lower().strip()
        if app_key in allowed_apps:
            cmd = allowed_apps[app_key]
            subprocess.Popen(cmd)
            return f"已为您成功启动应用: {app_name}"
        else:
            return f"未授权或不支持打开该应用: '{app_name}'。支持的应用有: {', '.join(allowed_apps.keys())}"

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
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("只允许打开有效的 HTTP 或 HTTPS 网页")
        opened = webbrowser.open(target, new=2)
        if not opened:
            raise RuntimeError("默认浏览器未接受打开请求")
        return f"已使用默认浏览器打开：{target}"
