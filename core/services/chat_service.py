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
from core.pet_animator import PetState
from core.providers.chat.base import ChatMessage, ChatProvider
from core.services.emotion_service import StreamingEmotionFilter, extract_emotions
from core.tool_registry import (
    MAX_TOOL_RESULT_CHARS,
    ToolExecutionContext,
    ToolRegistry,
    bound_tool_result,
)


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

    MAX_REQUEST_CHARS = 96 * 1024
    MAX_MESSAGE_CONTENT_CHARS = 24 * 1024
    MAX_TOOL_SCHEMA_CHARS = 32 * 1024

    def __init__(
        self,
        *,
        provider: ChatProvider,
        model: str,
        system_prompt: str,
        messages: List[ChatMessage],
        tool_registry: ToolRegistry,
        max_tool_rounds: int = 4,
        execution_context: ToolExecutionContext | None = None,
    ):
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt
        self.messages = [dict(message) for message in messages]
        self.tool_registry = tool_registry
        self.max_tool_rounds = max(1, int(max_tool_rounds))
        self.execution_context = execution_context

    def run(
        self,
        *,
        on_chunk: Callable[[str], None],
        on_tool_executing: Callable[[str], None],
        is_cancelled: Callable[[], bool],
        on_emotion: Optional[Callable[[PetState], None]] = None,
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

        emotion_filter = StreamingEmotionFilter()

        def filtered_on_chunk(chunk: str) -> None:
            if on_emotion is not None:
                clean_chunk, emotions = emotion_filter.process_chunk(chunk)
                for em in emotions:
                    on_emotion(em)
                if clean_chunk:
                    on_chunk(clean_chunk)
            else:
                on_chunk(chunk)

        tools_size = self._json_size(tools)
        if tools_size > self.MAX_TOOL_SCHEMA_CHARS:
            raise RuntimeError("工具定义超过安全大小上限，已停止本次请求")

        for _ in range(self.max_tool_rounds):
            if is_cancelled():
                return None
            request_messages = self._bounded_messages(full_messages)
            if self._json_size(request_messages) + tools_size > self.MAX_REQUEST_CHARS:
                raise RuntimeError("模型请求上下文超过安全大小上限，已停止本次请求")
            completion = self.provider.stream_completion(
                model=self.model,
                messages=request_messages,
                tools=tools,
                on_chunk=filtered_on_chunk,
                is_cancelled=is_cancelled,
            )
            if is_cancelled():
                return None
            if on_emotion is not None:
                leftover = emotion_filter.flush()
                if leftover:
                    on_chunk(leftover)

            clean_text, _ = extract_emotions(completion.text)
            if not completion.tool_calls:
                return clean_text

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
                on_tool_executing(function_name)
                try:
                    function_args = self._tool_arguments(function)
                except ValueError as exc:
                    # Do not silently turn malformed model output into `{}`:
                    # that can accidentally invoke a no-argument or
                    # permissive tool. Return a bounded protocol error.
                    tool_result = f"Error: invalid tool arguments ({exc.args[0]})."
                else:
                    if self.execution_context is None:
                        # Keep the small registry protocol backwards
                        # compatible for embedders that implement only
                        # ``execute_tool``.
                        tool_result = self.tool_registry.execute_tool(
                            function_name,
                            function_args,
                        )
                    else:
                        tool_result = self.tool_registry.execute_tool(
                            function_name,
                            function_args,
                            context=self.execution_context,
                        )
                full_messages.append(
                    {
                        "tool_call_id": tool_call["id"],
                        "role": "tool",
                        "name": function_name,
                        "content": bound_tool_result(
                            tool_result,
                            limit=MAX_TOOL_RESULT_CHARS,
                        ),
                    }
                )

        raise RuntimeError("工具调用次数过多，已停止本次请求")

    @staticmethod
    def _tool_arguments(function: Dict[str, Any]) -> Dict[str, Any]:
        try:
            value = json.loads(str(function.get("arguments") or "{}"))
        except (TypeError, json.JSONDecodeError):
            raise ValueError("malformed_json") from None
        if not isinstance(value, dict):
            raise ValueError("object_required")
        return value

    def close(self) -> None:
        self.provider.close()

    @classmethod
    def _bounded_messages(cls, messages: List[ChatMessage]) -> List[ChatMessage]:
        """Bound every message while preserving the tool-call message order."""
        bounded: List[ChatMessage] = []
        for message in messages:
            item = dict(message)
            content = item.get("content")
            if isinstance(content, str) and len(content) > cls.MAX_MESSAGE_CONTENT_CHARS:
                item["content"] = bound_tool_result(
                    content,
                    limit=cls.MAX_MESSAGE_CONTENT_CHARS,
                    label="message",
                )
            bounded.append(item)
        return bounded

    @staticmethod
    def _json_size(value: Any) -> int:
        try:
            return len(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            )
        except (TypeError, ValueError):
            return 2**31
