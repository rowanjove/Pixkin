"""Chat presets, capability preflight, and transparent local estimates."""

from __future__ import annotations

from dataclasses import dataclass

from core.providers.chat.base import ChatProviderCapabilities


class ChatCapabilityError(ValueError):
    pass


PRESET_PROMPTS = {
    "fast": "\n\n回答预设：优先快速、简洁地给出可执行结论。",
    "balanced": "",
    "deep": "\n\n回答预设：先充分检查约束和证据，再给出完整结论。",
}


def preset_prompt(name: str) -> str:
    return PRESET_PROMPTS.get(str(name), PRESET_PROMPTS["balanced"])


def require_chat_capabilities(
    capabilities: ChatProviderCapabilities,
    *,
    has_tools: bool,
) -> None:
    if not capabilities.streaming:
        raise ChatCapabilityError("当前模型接口不支持流式输出，任务未发送")
    if has_tools and not capabilities.tool_calls:
        raise ChatCapabilityError("当前模型接口不支持工具调用，任务未发送")


@dataclass(frozen=True)
class ChatEstimate:
    latency_ms: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float

    def metadata(self) -> dict:
        return {
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": round(self.estimated_cost, 6),
            "estimated": True,
        }


def estimate_chat(
    *,
    latency_seconds: float,
    input_text: str,
    output_text: str,
    input_cost_per_million: float,
    output_cost_per_million: float,
) -> ChatEstimate:
    input_tokens = max(1, round(len(str(input_text)) / 4))
    output_tokens = max(1, round(len(str(output_text)) / 4))
    cost = (
        input_tokens * max(0.0, float(input_cost_per_million))
        + output_tokens * max(0.0, float(output_cost_per_million))
    ) / 1_000_000
    return ChatEstimate(
        latency_ms=max(0, round(float(latency_seconds) * 1000)),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=cost,
    )
