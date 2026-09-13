"""Explicit permission contracts for desktop context observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock, get_ident
from time import monotonic
from typing import Callable
from uuid import uuid4


class PermissionResource(str, Enum):
    WINDOW_METADATA = "window_metadata"
    SYSTEM_STATE = "system_state"
    CLIPBOARD = "clipboard"
    SCREEN = "screen"
    MICROPHONE = "microphone"
    CAMERA = "camera"
    FILESYSTEM = "filesystem"
    NETWORK = "network"
    EXTERNAL_ACTION = "external_action"
    PLUGIN = "plugin"
    MCP = "mcp"


class PermissionOperation(str, Enum):
    OBSERVE = "observe"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class PermissionState(str, Enum):
    DENY = "deny"
    ASK = "ask"
    ALLOW_SESSION = "allow_session"
    ALLOW_ALWAYS = "allow_always"


@dataclass(frozen=True)
class PermissionDecision:
    resource: PermissionResource
    operation: PermissionOperation
    state: PermissionState
    allowed: bool
    reason: str


class ContextPermissionService:
    """Default-deny permission store with session-only grants in memory."""

    # A GUI approval must be consumed by the operation it approved. The TTL
    # is a safety net for cancellation before a worker starts; normal paths
    # also call ``clear_pending`` explicitly.
    PENDING_GRANT_TTL_SECONDS = 30.0

    def __init__(
        self,
        *,
        ask_callback: Callable[[PermissionResource, PermissionOperation], bool]
        | None = None,
    ):
        self._persistent: dict[PermissionResource, PermissionState] = {}
        self._session: dict[PermissionResource, PermissionState] = {}
        # ASK approvals are kept as one-shot grants for a worker that is
        # already authorized by the GUI.  The public four-state model stays
        # unchanged and the grant is never persisted.
        self._pending_grants: dict[
            str,
            tuple[PermissionResource, PermissionOperation, float],
        ] = {}
        self._lock = RLock()
        self._ask_callback = ask_callback
        self._ask_callback_thread_id = (
            get_ident() if ask_callback is not None else None
        )

    def set_ask_callback(
        self,
        callback: Callable[[PermissionResource, PermissionOperation], bool]
        | None,
    ) -> None:
        """Install the UI approval hook used for ``ASK`` permissions.

        The service remains safe in headless/tests: with no callback an ASK
        decision is denied rather than silently upgraded to an allow.
        """
        with self._lock:
            self._ask_callback = callback
            self._ask_callback_thread_id = (
                get_ident() if callback is not None else None
            )
            self._pending_grants.clear()

    def set_state(
        self,
        resource: PermissionResource,
        state: PermissionState,
        *,
        session_only: bool = False,
    ) -> None:
        resource = PermissionResource(resource)
        state = PermissionState(state)
        with self._lock:
            target = self._session if session_only else self._persistent
            target[resource] = state
            if not session_only:
                self._session.pop(resource, None)
            for token, grant in tuple(self._pending_grants.items()):
                if grant[0] is resource:
                    self._pending_grants.pop(token, None)

    def state(self, resource: PermissionResource) -> PermissionState:
        resource = PermissionResource(resource)
        with self._lock:
            return self._session.get(
                resource,
                self._persistent.get(resource, PermissionState.DENY),
            )

    @staticmethod
    def new_handoff_token() -> str:
        """Create an opaque one-shot token for a GUI-to-worker handoff."""
        return uuid4().hex

    def decide(
        self,
        resource: PermissionResource,
        operation: PermissionOperation = PermissionOperation.OBSERVE,
        *,
        resolve_ask: bool = True,
        consume_pending: bool = False,
        handoff_token: str | None = None,
    ) -> PermissionDecision:
        """Return a decision, keeping UI ASK resolution on its owning thread.

        Background workers must pass ``resolve_ask=False``.  They can consume
        an approval made for their operation with ``consume_pending=True`` and
        the opaque ``handoff_token`` issued by the GUI; a resource/operation
        pair alone is never sufficient to borrow another request's approval.
        """
        resource = PermissionResource(resource)
        operation = PermissionOperation(operation)
        token = str(handoff_token or "").strip() or None
        state = self.state(resource)
        allowed = state in {
            PermissionState.ALLOW_SESSION,
            PermissionState.ALLOW_ALWAYS,
        }
        if state is PermissionState.ASK:
            with self._lock:
                callback = self._ask_callback
                callback_thread_id = self._ask_callback_thread_id
                self._prune_pending_locked()
                pending = bool(
                    token
                    and token in self._pending_grants
                    and self._pending_grants[token][:2]
                    == (resource, operation)
                )
            if not resolve_ask and consume_pending and pending and token:
                with self._lock:
                    self._prune_pending_locked()
                    grant = self._pending_grants.get(token)
                    if grant is not None and grant[:2] == (resource, operation):
                        self._pending_grants.pop(token, None)
                        allowed = True
            elif (
                resolve_ask
                and callback is not None
                and callback_thread_id == get_ident()
            ):
                try:
                    allowed = bool(callback(resource, operation))
                except Exception:
                    allowed = False
                if allowed:
                    with self._lock:
                        current_state = self._session.get(
                            resource,
                            self._persistent.get(
                                resource, PermissionState.DENY
                            ),
                        )
                        if current_state is PermissionState.ASK:
                            if token:
                                self._pending_grants[token] = (
                                    resource,
                                    operation,
                                    monotonic()
                                    + self.PENDING_GRANT_TTL_SECONDS,
                                )
                        else:
                            allowed = current_state in {
                                PermissionState.ALLOW_SESSION,
                                PermissionState.ALLOW_ALWAYS,
                            }
        reason = (
            "permission granted"
            if allowed
            else f"{resource.value} requires explicit permission ({state.value})"
        )
        return PermissionDecision(resource, operation, state, allowed, reason)

    def clear_session(self) -> None:
        with self._lock:
            self._session.clear()
            self._pending_grants.clear()

    def clear_pending(
        self,
        resource: PermissionResource | None = None,
        operation: PermissionOperation | None = None,
        *,
        handoff_token: str | None = None,
    ) -> None:
        """Cancel unconsumed ASK approvals for an aborted operation."""
        resource_value = (
            PermissionResource(resource) if resource is not None else None
        )
        operation_value = (
            PermissionOperation(operation) if operation is not None else None
        )
        token_value = str(handoff_token or "").strip() or None
        with self._lock:
            for token, grant in tuple(self._pending_grants.items()):
                if token_value is not None and token != token_value:
                    continue
                if resource_value is not None and grant[0] is not resource_value:
                    continue
                if operation_value is not None and grant[1] is not operation_value:
                    continue
                self._pending_grants.pop(token, None)

    def _prune_pending_locked(self) -> None:
        now = monotonic()
        for token, grant in tuple(self._pending_grants.items()):
            if grant[2] <= now:
                self._pending_grants.pop(token, None)
