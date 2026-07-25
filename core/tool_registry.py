import datetime
import platform
import subprocess
from typing import Dict, Any, List, Callable

class ToolRegistry:
    """Tool Calls (Function Calling) 注册与调度中心"""

    def __init__(self):
        self._tools_schema: List[Dict[str, Any]] = []
        self._tool_handlers: Dict[str, Callable] = {}
        self._register_default_tools()

    def register_tool(self, name: str, description: str, parameters: Dict[str, Any], handler: Callable):
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

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """获取所有工具的 OpenAI schema 声明"""
        return self._tools_schema

    def execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        """执行调用的工具并返回字符串结果"""
        if name not in self._tool_handlers:
            return f"Error: Tool '{name}' not found."
        try:
            handler = self._tool_handlers[name]
            result = handler(**args) if args else handler()
            return str(result)
        except Exception as e:
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
            description="在 Windows 系统上打开常用应用程序，例如 notepad(记事本), calc(计算器), mspaint(画图板), cmd(命令行)。",
            parameters={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "要打开的应用名称，如 notepad, calc, cmd 等"
                    }
                },
                "required": ["app_name"]
            },
            handler=self._tool_open_application
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
            "cmd": "cmd.exe",
            "explorer": "explorer.exe"
        }
        app_key = app_name.lower().strip()
        if app_key in allowed_apps:
            cmd = allowed_apps[app_key]
            subprocess.Popen(cmd)
            return f"已为您成功启动应用: {app_name}"
        else:
            return f"未授权或不支持打开该应用: '{app_name}'。支持的应用有: {', '.join(allowed_apps.keys())}"
