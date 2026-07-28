"""Tool authorization policy and privacy-preserving audit contracts."""

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


LOGGER = logging.getLogger("desktop_pet.tool_permissions")


class ToolPermissionLevel(IntEnum):
    READ_ONLY = 0
    EXTERNAL_ACTION = 1
    STATE_CHANGE = 2
    HIGH_RISK = 3


@dataclass(frozen=True)
class ToolPermissionRequest:
    tool_name: str
    level: ToolPermissionLevel
    side_effect: str
    arguments: Mapping[str, Any]
    authorization_fingerprint: str = ""


@dataclass(frozen=True)
class ToolPermissionDecision:
    allowed: bool
    authorization: str
    reason: str


@dataclass(frozen=True)
class ToolAuditEvent:
    timestamp: float
    tool_name: str
    level: ToolPermissionLevel
    arguments: Mapping[str, Any]
    authorization: str
    result_status: str
    duration_ms: int
    session_id: str = ""


class ToolPermissionService:
    """Default-deny authorization for tools with external side effects."""

    _SENSITIVE_MARKERS = (
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    )

    def __init__(
        self,
        confirm_external: Callable[[ToolPermissionRequest], bool] | None = None,
        *,
        clock: Callable[[], float] = time.time,
        audit_sink: Callable[[ToolAuditEvent], None] | None = None,
        session_id_provider: Callable[[], str] | None = None,
    ):
        self.confirm_external = confirm_external
        self.clock = clock
        self.audit_sink = audit_sink
        self.session_id_provider = session_id_provider
        self._events: list[ToolAuditEvent] = []

    def authorize(
        self,
        tool_name: str,
        level: ToolPermissionLevel,
        arguments: Mapping[str, Any],
        side_effect: str,
    ) -> tuple[ToolPermissionRequest, ToolPermissionDecision]:
        request = ToolPermissionRequest(
            tool_name=tool_name,
            level=level,
            side_effect=side_effect,
            arguments=self.sanitize_arguments(arguments),
            authorization_fingerprint=self.authorization_fingerprint(
                arguments
            ),
        )
        if level == ToolPermissionLevel.READ_ONLY:
            decision = ToolPermissionDecision(
                True,
                "automatic",
                "L0 只读工具允许自动执行",
            )
        elif level == ToolPermissionLevel.EXTERNAL_ACTION:
            confirmed = bool(
                self.confirm_external
                and self.confirm_external(request)
            )
            decision = ToolPermissionDecision(
                confirmed,
                "user_confirmed" if confirmed else "denied",
                (
                    "用户已确认本次外部动作"
                    if confirmed
                    else "外部动作需要逐次确认"
                ),
            )
        else:
            decision = ToolPermissionDecision(
                False,
                "denied",
                "L2/L3 工具默认禁用",
            )
        return request, decision

    def record(
        self,
        request: ToolPermissionRequest,
        decision: ToolPermissionDecision,
        *,
        result_status: str,
        duration_ms: int,
    ) -> ToolAuditEvent:
        event = ToolAuditEvent(
            timestamp=self.clock(),
            tool_name=request.tool_name,
            level=request.level,
            arguments=request.arguments,
            authorization=decision.authorization,
            result_status=result_status,
            duration_ms=max(0, int(duration_ms)),
            session_id=(
                str(self.session_id_provider() or "")
                if self.session_id_provider is not None
                else ""
            ),
        )
        self._events.append(event)
        if self.audit_sink is not None:
            try:
                self.audit_sink(event)
            except Exception as exc:
                LOGGER.warning(
                    "工具审计写入失败（%s）",
                    type(exc).__name__,
                )
        return event

    def events(self) -> tuple[ToolAuditEvent, ...]:
        return tuple(self._events)

    @staticmethod
    def authorization_fingerprint(
        arguments: Mapping[str, Any],
    ) -> str:
        """Bind a decision to the exact undisclosed argument payload."""
        payload = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def sanitize_arguments(
        cls,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            str(key): cls._sanitize_value(str(key), value)
            for key, value in arguments.items()
        }

    @classmethod
    def _sanitize_value(cls, key: str, value: Any) -> Any:
        lowered = key.lower()
        if any(marker in lowered for marker in cls._SENSITIVE_MARKERS):
            return "<redacted>"
        if isinstance(value, Mapping):
            return cls.sanitize_arguments(value)
        if isinstance(value, (list, tuple)):
            return [
                cls._sanitize_value(key, item)
                for item in value
            ]
        if isinstance(value, str):
            if lowered == "url":
                return cls._sanitize_url(value)
            return value[:200]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return json.dumps(str(value)[:200], ensure_ascii=False)

    @classmethod
    def _sanitize_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return "<invalid-url>"
        hostname = parsed.hostname or ""
        try:
            parsed_port = parsed.port
        except ValueError:
            return "<invalid-url>"
        port = f":{parsed_port}" if parsed_port else ""
        netloc = f"{hostname}{port}"
        query = urlencode(
            [
                (
                    key,
                    "<redacted>"
                    if any(
                        marker in key.lower()
                        for marker in cls._SENSITIVE_MARKERS
                    )
                    else item[:100],
                )
                for key, item in parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                )
            ]
        )
        return urlunsplit(
            (parsed.scheme, netloc, parsed.path[:200], query, "")
        )
