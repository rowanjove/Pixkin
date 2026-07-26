import tempfile
import unittest
from pathlib import Path

from core.chat_history_store import ChatHistoryStore
from core.providers.chat.base import (
    ChatCompletionResult,
    ChatProvider,
    ChatProviderCapabilities,
    ChatProviderHealth,
)
from core.services.chat_experience_service import ChatCapabilityError
from core.services.chat_service import ChatService, ChatSessionService


class FakeChatProvider(ChatProvider):
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.closed = False

    @property
    def capabilities(self):
        return ChatProviderCapabilities()

    def stream_completion(
        self,
        *,
        model,
        messages,
        tools,
        on_chunk,
        is_cancelled,
    ):
        self.requests.append(
            {
                "model": model,
                "messages": [dict(item) for item in messages],
                "tools": tools,
            }
        )
        response = self.responses.pop(0)
        if response.text:
            on_chunk(response.text)
        return response

    def health_check(self, model):
        return ChatProviderHealth(True, model)

    def close(self):
        self.closed = True


class FakeToolRegistry:
    def __init__(self):
        self.executed = []

    def get_tools_schema(self):
        return [{"type": "function", "function": {"name": "clock"}}]

    def execute_tool(self, name, arguments):
        self.executed.append((name, arguments))
        return "12:00"


class NoToolChatProvider(FakeChatProvider):
    @property
    def capabilities(self):
        return ChatProviderCapabilities(tool_calls=False)


class ChatServiceTests(unittest.TestCase):
    def test_tool_loop_and_message_policy_are_provider_neutral(self):
        provider = FakeChatProvider(
            [
                ChatCompletionResult(
                    "",
                    [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "clock",
                                "arguments": '{"zone": "Asia/Hong_Kong"}',
                            },
                        }
                    ],
                ),
                ChatCompletionResult("现在是 12:00", []),
            ]
        )
        tools = FakeToolRegistry()
        service = ChatService(
            provider=provider,
            model="chat-model",
            system_prompt="你是桌面伙伴",
            messages=[{"role": "user", "content": "几点了"}],
            tool_registry=tools,
        )
        chunks = []
        executing = []

        response = service.run(
            on_chunk=chunks.append,
            on_tool_executing=executing.append,
            is_cancelled=lambda: False,
        )

        self.assertEqual(response, "现在是 12:00")
        self.assertEqual(chunks, ["现在是 12:00"])
        self.assertEqual(executing, ["clock"])
        self.assertEqual(
            tools.executed,
            [("clock", {"zone": "Asia/Hong_Kong"})],
        )
        self.assertEqual(
            provider.requests[0]["messages"],
            [
                {"role": "system", "content": "你是桌面伙伴"},
                {"role": "user", "content": "几点了"},
            ],
        )
        self.assertEqual(
            provider.requests[1]["messages"][-1],
            {
                "tool_call_id": "call-1",
                "role": "tool",
                "name": "clock",
                "content": "12:00",
            },
        )

    def test_cancelled_service_does_not_call_provider(self):
        provider = FakeChatProvider([])
        service = ChatService(
            provider=provider,
            model="chat-model",
            system_prompt="system",
            messages=[],
            tool_registry=FakeToolRegistry(),
        )

        response = service.run(
            on_chunk=lambda _chunk: None,
            on_tool_executing=lambda _name: None,
            is_cancelled=lambda: True,
        )

        self.assertIsNone(response)
        self.assertEqual(provider.requests, [])

    def test_invalid_tool_arguments_are_replaced_with_empty_object(self):
        provider = FakeChatProvider(
            [
                ChatCompletionResult(
                    "",
                    [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "clock",
                                "arguments": "not-json",
                            },
                        }
                    ],
                ),
                ChatCompletionResult("done", []),
            ]
        )
        tools = FakeToolRegistry()
        service = ChatService(
            provider=provider,
            model="chat-model",
            system_prompt="system",
            messages=[],
            tool_registry=tools,
        )

        service.run(
            on_chunk=lambda _chunk: None,
            on_tool_executing=lambda _name: None,
            is_cancelled=lambda: False,
        )

        self.assertEqual(tools.executed, [("clock", {})])

    def test_unsupported_tool_capability_blocks_before_provider_request(self):
        provider = NoToolChatProvider(
            [ChatCompletionResult("should-not-run", [])]
        )
        service = ChatService(
            provider=provider,
            model="chat-model",
            system_prompt="system",
            messages=[{"role": "user", "content": "use a tool"}],
            tool_registry=FakeToolRegistry(),
        )

        with self.assertRaises(ChatCapabilityError):
            service.run(
                on_chunk=lambda _chunk: None,
                on_tool_executing=lambda _name: None,
                is_cancelled=lambda: False,
            )

        self.assertEqual(provider.requests, [])


class ChatSessionServiceTests(unittest.TestCase):
    def test_context_is_bounded_and_starts_with_user(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ChatSessionService(
                ChatHistoryStore(Path(directory) / "history.sqlite3")
            )
            service.activate_character("shanshan", "山山")
            for index in range(60):
                service.add_message(
                    "user",
                    f"question-{index}" * 300,
                )
                service.add_message(
                    "assistant",
                    f"answer-{index}" * 300,
                )

            context = service.context()
            self.assertLessEqual(len(context), 40)
            self.assertEqual(context[0]["role"], "user")
            self.assertLessEqual(
                sum(len(str(item["content"])) for item in context),
                24000,
            )

    def test_character_sessions_restore_independent_context(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ChatSessionService(
                ChatHistoryStore(Path(directory) / "history.sqlite3")
            )
            service.activate_character("shanshan", "山山")
            service.add_message("user", "只和山山说的话")
            service.activate_character("linlin", "凛凛")
            self.assertEqual(service.context(), [])
            service.add_message("user", "只和凛凛说的话")

            snapshot = service.activate_character("shanshan", "山山")

            self.assertEqual(
                snapshot.context,
                [{"role": "user", "content": "只和山山说的话"}],
            )
            self.assertEqual(
                snapshot.transcript[-1]["content"],
                "只和山山说的话",
            )

    def test_non_conversation_roles_are_persisted_but_not_in_context(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ChatSessionService(
                ChatHistoryStore(Path(directory) / "history.sqlite3")
            )
            service.activate_character("shanshan", "山山")

            service.add_message("system", "正在使用工具")
            service.add_message("error", "请求失败")

            self.assertEqual(service.context(), [])
            self.assertEqual(
                [item["role"] for item in service.snapshot().transcript],
                ["system", "error"],
            )


if __name__ == "__main__":
    unittest.main()
