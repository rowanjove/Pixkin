import unittest

from core.pet_animator import PetState
from core.providers.chat.base import (
    ChatCompletionResult,
    ChatProvider,
    ChatProviderCapabilities,
    ChatProviderHealth,
)
from core.services.chat_service import ChatService
from core.services.emotion_service import (
    StreamingEmotionFilter,
    extract_emotions,
    parse_pet_state,
)


class FakeEmotionChatProvider(ChatProvider):
    def __init__(self, chunks):
        self.chunks = list(chunks)

    @property
    def capabilities(self):
        return ChatProviderCapabilities()

    def stream_completion(self, *, model, messages, tools, on_chunk, is_cancelled):
        for chunk in self.chunks:
            if is_cancelled():
                break
            on_chunk(chunk)
        return ChatCompletionResult("".join(self.chunks), [])

    def health_check(self, model):
        return ChatProviderHealth(True, model)

    def close(self):
        pass


class FakeToolRegistry:
    def get_tools_schema(self):
        return []

    def execute_tool(self, name, arguments):
        return ""


class EmotionServiceTests(unittest.TestCase):
    def test_parse_pet_state(self):
        self.assertEqual(parse_pet_state("happy"), PetState.HAPPY)
        self.assertEqual(parse_pet_state("NOD"), PetState.NOD)
        self.assertEqual(parse_pet_state("wave"), PetState.WAVE)
        self.assertEqual(parse_pet_state("thinking"), PetState.THINKING)
        self.assertEqual(parse_pet_state("non_existent_state"), None)

    def test_extract_emotions_static_text(self):
        text = "<state=happy/> 很高兴认识你！ <state=wave/> 再见啦！"
        clean_text, emotions = extract_emotions(text)
        self.assertEqual(clean_text, "很高兴认识你！ 再见啦！")
        self.assertEqual(emotions, [PetState.HAPPY, PetState.WAVE])

        # Bracket format
        text2 = "[state=nod] 我同意你的看法。"
        clean_text2, emotions2 = extract_emotions(text2)
        self.assertEqual(clean_text2, "我同意你的看法。")
        self.assertEqual(emotions2, [PetState.NOD])

    def test_streaming_emotion_filter(self):
        stream_filter = StreamingEmotionFilter()
        # Tag split across chunks
        chunk1, em1 = stream_filter.process_chunk("你好呀 <state=")
        self.assertEqual(chunk1, "你好呀 ")
        self.assertEqual(em1, [])

        chunk2, em2 = stream_filter.process_chunk("happy/> 主人！")
        self.assertEqual(chunk2, " 主人！")
        self.assertEqual(em2, [PetState.HAPPY])

        leftover = stream_filter.flush()
        self.assertEqual(leftover, "")

    def test_chat_service_with_on_emotion_callback(self):
        chunks = ["你好呀，", "<state=happy/>今天", "天气真好！<state=wave/>"]
        provider = FakeEmotionChatProvider(chunks)
        tools = FakeToolRegistry()

        service = ChatService(
            provider=provider,
            model="test-model",
            system_prompt="system",
            messages=[{"role": "user", "content": "hello"}],
            tool_registry=tools,
        )

        emitted_chunks = []
        emotions_detected = []

        result = service.run(
            on_chunk=lambda c: emitted_chunks.append(c),
            on_tool_executing=lambda t: None,
            is_cancelled=lambda: False,
            on_emotion=lambda e: emotions_detected.append(e),
        )

        self.assertEqual(result, "你好呀，今天天气真好！")
        self.assertEqual(emotions_detected, [PetState.HAPPY, PetState.WAVE])
        # Ensure no <state=...> leaked into emitted_chunks
        combined_emitted = "".join(emitted_chunks)
        self.assertNotIn("<state=", combined_emitted)
        self.assertIn("今天天气真好！", combined_emitted)


if __name__ == "__main__":
    unittest.main()
