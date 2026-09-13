"""Application lifecycle coordinator kept independent from Qt widgets."""

from __future__ import annotations

import logging
from enum import Enum

from core.runtime.service_container import ServiceContainer


LOGGER = logging.getLogger("desktop_pet.runtime.kernel")


class KernelState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"


class PixkinKernel:
    """Start and stop registered services exactly once and in reverse order."""

    def __init__(self, services: ServiceContainer):
        self.services = services
        self.state = KernelState.CREATED

    def start(self) -> None:
        if self.state is KernelState.RUNNING:
            return
        if self.state is KernelState.STOPPED:
            raise RuntimeError("kernel cannot restart after stop")
        self.services.start()
        self.state = KernelState.RUNNING
        LOGGER.info("Pixkin runtime kernel started")

    def stop(self) -> None:
        if self.state is not KernelState.RUNNING:
            return
        self.services.stop()
        self.state = KernelState.STOPPED
        LOGGER.info("Pixkin runtime kernel stopped")
