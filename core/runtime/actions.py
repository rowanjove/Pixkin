"""Action contracts and guarded dispatch for runtime side effects."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
)


LOGGER = logging.getLogger("desktop_pet.runtime.actions")


class ActionType(str, Enum):
    SPEAK = "speak"
    ANIMATE = "animate"
    NOTIFY = "notify"
    TOOL = "tool"
    OPEN_WINDOW = "open_window"
    IGNORE = "ignore"


@dataclass(frozen=True)
class Action:
    type: ActionType
    payload: Mapping[str, Any] = field(default_factory=dict)
    source: str = "runtime"
    permission_resource: PermissionResource | None = None
    permission_operation: PermissionOperation = PermissionOperation.EXECUTE
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    status: str
    message: str = ""


class ActionDispatcher:
    """Dispatch registered actions through one permission-aware gateway."""

    # These action classes cause an observable external effect even when a
    # producer forgot to attach an explicit resource.  Defaulting them to the
    # external-action gate prevents a malformed/plugin-produced Action from
    # bypassing the permission service.
    _IMPLICIT_RESOURCES = {
        ActionType.NOTIFY: PermissionResource.EXTERNAL_ACTION,
        ActionType.TOOL: PermissionResource.EXTERNAL_ACTION,
        ActionType.OPEN_WINDOW: PermissionResource.EXTERNAL_ACTION,
    }

    def __init__(self, permission_service: ContextPermissionService | None = None):
        self.permission_service = permission_service or ContextPermissionService()
        self._handlers: dict[ActionType, Callable[[Action], Any]] = {}
        self._lock = threading.RLock()

    def register(self, action_type: ActionType, handler: Callable[[Action], Any]) -> None:
        if not callable(handler):
            raise ValueError("action handler must be callable")
        with self._lock:
            self._handlers[ActionType(action_type)] = handler

    def dispatch(self, action: Action) -> ActionResult:
        if not isinstance(action, Action):
            raise TypeError("dispatch expects Action")
        resource = action.permission_resource or self._IMPLICIT_RESOURCES.get(
            action.type
        )
        if resource is not None:
            decision = self.permission_service.decide(
                resource,
                action.permission_operation,
            )
            if not decision.allowed:
                return ActionResult(action.action_id, "denied", decision.reason)
        with self._lock:
            handler = self._handlers.get(action.type)
        if handler is None:
            return ActionResult(action.action_id, "ignored", "no handler registered")
        try:
            result = handler(action)
            return ActionResult(action.action_id, "success", str(result or ""))
        except Exception as exc:
            LOGGER.exception("action handler failed type=%s", action.type.value)
            return ActionResult(action.action_id, "error", type(exc).__name__)
