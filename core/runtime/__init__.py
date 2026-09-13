"""Provider-neutral runtime contracts used by Pixkin's composition root."""

from core.runtime.actions import (
    Action,
    ActionDispatcher,
    ActionResult,
    ActionType,
)
from core.runtime.events import (
    EventBus,
    EventDeliveryReport,
    EventEnvelope,
)
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionDecision,
    PermissionOperation,
    PermissionResource,
    PermissionState,
)
from core.runtime.service_container import ServiceContainer
from core.runtime.kernel import KernelState, PixkinKernel
from core.runtime.startup import StartupCoordinator, StartupError, StartupStep

__all__ = [
    "Action",
    "ActionDispatcher",
    "ActionResult",
    "ActionType",
    "ContextPermissionService",
    "EventBus",
    "EventDeliveryReport",
    "EventEnvelope",
    "PermissionDecision",
    "PermissionOperation",
    "PermissionResource",
    "PermissionState",
    "ServiceContainer",
    "KernelState",
    "PixkinKernel",
    "StartupCoordinator",
    "StartupError",
    "StartupStep",
]
