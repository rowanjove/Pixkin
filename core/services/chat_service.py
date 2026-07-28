"""Provider-neutral orchestration for chat messages and tool calls."""

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from core.chat_history_store import ChatHistoryStore
from core.privacy import redact_text, redact_value
from core.services.chat_experience_service import (
    require_chat_capabilities,
)
from core.providers.chat.base import ChatMessage, ChatProvider
from core.tool_registry import ToolRegistry


LOGGER = logging.getLogger("desktop_pet.chat_service")


@dataclass(frozen=True)
class ChatSessionSnapshot:
    session_id: str
    context: List[ChatMessage]
    transcript: List[dict]


class ChatSessionService:
    """Own active chat session, bounded context, and history persistence."""

    def __init__(
        self,
        store: ChatHistoryStore,
        *,
        max_messages: int = 40,
        char_budget: int = 24000,
    ):
        self.store = store
        self.max_messages = max(1, int(max_messages))
        self.char_budget = max(1, int(char_budget))
        self.character_id = ""
        self.character_name = ""
        self.session_id = ""
        self._context: List[ChatMessage] = []
        self.retention_days = -1
        self.persist_history = True

    def set_retention(self, days: int) -> int:
        """Apply local history retention; -1 keeps history permanently."""
        was_persisting = self.persist_history
        self.retention_days = int(days)
        self.persist_history = self.retention_days != 0
        if self.retention_days == 0:
            self._context = []
            return self.store.clear_all()
        if not was_persisting and self.character_id:
            self.session_id = self.store.get_or_create_active_session(
                self.character_id,
                self.character_name,
            )
        if self.retention_days > 0:
            return self.store.prune_older_than(self.retention_days)
        return 0

    def activate_character(
        self,
        character_id: str,
        character_name: str,
    ) -> ChatSessionSnapshot:
        self.character_id = str(character_id or "default")
        self.character_name = str(character_name or "角色")
        self.session_id = self.store.get_or_create_active_session(
            self.character_id,
            self.character_name,
        )
        return self.reload()

    def reload(self) -> ChatSessionSnapshot:
        if not self.character_id:
            raise RuntimeError("尚未激活聊天角色")
        self.session_id = self.store.get_or_create_active_session(
            self.character_id,
            self.character_name,
        )
        self._context = self.trim_context(
            self.store.context_messages(self.session_id)
        )
        return self.snapshot()

    def start_new_session(self) -> ChatSessionSnapshot:
        if not self.character_id:
            raise RuntimeError("尚未激活聊天角色")
        self.session_id = self.store.create_session(
            self.character_id,
            self.character_name,
        )
        self._context = []
        return self.snapshot()

    def snapshot(self) -> ChatSessionSnapshot:
        transcript = (
            self.store.session_messages(self.session_id)
            if self.session_id
            else []
        )
        return ChatSessionSnapshot(
            self.session_id,
            self.context(),
            transcript,
        )

    def context(self) -> List[ChatMessage]:
        return [dict(message) for message in self._context]

    def add_message(
        self,
        role: str,
        content: str,
        metadata=None,
        created_at=None,
        *,
        session_id: Optional[str] = None,
    ) -> Optional[dict]:
        target_session = session_id or self.session_id
        if not target_session:
            return None

        if (
            target_session == self.session_id
            and role in {"user", "assistant"}
        ):
            self._context.append({"role": role, "content": str(content)})
            self._context = self.trim_context(self._context)
        try:
            if not self.persist_history:
                return {
                    "id": None,
                    "session_id": target_session,
                    "role": str(role),
                    "content": str(content),
                    "metadata": (
                        metadata if isinstance(metadata, dict) else {}
                    ),
                    "created_at": created_at,
                }
            return self.store.add_message(
                target_session,
                role,
                redact_text(content),
                redact_value(
                    metadata if isinstance(metadata, dict) else {}
                ),
                created_at,
            )
        except Exception as exc:
            LOGGER.warning("聊天历史写入失败: %s", exc)
            return None

    def trim_context(
        self,
        messages: List[ChatMessage],
    ) -> List[ChatMessage]:
        context = [dict(message) for message in messages]
        if not context:
            return context
        latest = context[-1]
        latest_content = str(latest.get("content") or "")
        if len(latest_content) > self.char_budget:
            latest["content"] = latest_content[-self.char_budget:]

        total_chars = sum(
            len(str(message.get("content") or ""))
            for message in context
        )
        while len(context) > 1 and (
            len(context) > self.max_messages
            or total_chars > self.char_budget
        ):
            removed = context.pop(0)
            total_chars -= len(str(removed.get("content") or ""))
        while context and context[0].get("role") != "user":
            context.pop(0)
        return context


class ChatService:
    """Own chat policy while leaving thread lifecycle to the caller."""

    def __init__(
        self,
        *,
        provider: ChatProvider,
        model: str,
        system_prompt: str,
        messages: List[ChatMessage],
        tool_registry: ToolRegistry,
        max_tool_rounds: int = 4,
    ):
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt
        self.messages = [dict(message) for message in messages]
        self.tool_registry = tool_registry
        self.max_tool_rounds = max(1, int(max_tool_rounds))

    def run(
        self,
        *,
        on_chunk: Callable[[str], None],
        on_tool_executing: Callable[[str], None],
        is_cancelled: Callable[[], bool],
    ) -> Optional[str]:
        full_messages: List[ChatMessage] = [
            {"role": "system", "content": self.system_prompt}
        ]
        full_messages.extend(self.messages)
        tools = self.tool_registry.get_tools_schema()
        require_chat_capabilities(
            self.provider.capabilities,
            has_tools=bool(tools),
        )

        for _ in range(self.max_tool_rounds):
            if is_cancelled():
                return None
            completion = self.provider.stream_completion(
                model=self.model,
                messages=full_messages,
                tools=tools,
                on_chunk=on_chunk,
                is_cancelled=is_cancelled,
            )
            if is_cancelled():
                return None
            if not completion.tool_calls:
                return completion.text

            full_messages.append(
                {
                    "role": "assistant",
                    "content": completion.text or None,
                    "tool_calls": completion.tool_calls,
                }
            )
            for tool_call in completion.tool_calls:
                function = tool_call["function"]
                function_name = str(function["name"])
                function_args = self._tool_arguments(function)
                on_tool_executing(function_name)
                tool_result = self.tool_registry.execute_tool(
                    function_name,
                    function_args,
                )
                full_messages.append(
                    {
                        "tool_call_id": tool_call["id"],
                        "role": "tool",
                        "name": function_name,
                        "content": tool_result,
                    }
                )

        raise RuntimeError("工具调用次数过多，已停止本次请求")

    @staticmethod
    def _tool_arguments(function: Dict[str, Any]) -> Dict[str, Any]:
        try:
            value = json.loads(str(function.get("arguments") or "{}"))
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def close(self) -> None:
        self.provider.close()
