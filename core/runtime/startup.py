"""Testable startup sequencing independent from Qt widgets."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable


LOGGER = logging.getLogger("desktop_pet.runtime.startup")


class StartupError(RuntimeError):
    pass


@dataclass(frozen=True)
class StartupStep:
    name: str
    run: Callable[[], object]


class StartupCoordinator:
    """Run named startup steps with explicit cancellation and diagnostics."""

    def __init__(self, steps: Iterable[StartupStep]):
        self.steps = tuple(steps)
        self.completed: list[str] = []
        self._aborted = False

    def abort(self) -> None:
        self._aborted = True

    def mark(self, name: str) -> str:
        """Record a step completed by an external composition root.

        The Qt application still contains legacy setup code that cannot be
        atomically re-indented into ``run``. This hook lets the composition
        root use the same named lifecycle ledger while preserving recovery
        dialogs and early-return paths.
        """
        if self._aborted:
            raise StartupError("startup aborted")
        clean = str(name or "").strip()
        if not clean:
            raise StartupError("invalid startup step")
        self.completed.append(clean)
        LOGGER.info("startup step complete: %s", clean)
        return clean

    def run(self) -> tuple[str, ...]:
        self.completed.clear()
        for step in self.steps:
            if self._aborted:
                raise StartupError("startup aborted")
            if not str(step.name).strip() or not callable(step.run):
                raise StartupError("invalid startup step")
            LOGGER.info("startup step begin: %s", step.name)
            try:
                step.run()
            except Exception as exc:
                LOGGER.error("startup step failed: %s (%s)", step.name, type(exc).__name__)
                raise StartupError(f"startup step failed: {step.name}") from exc
            self.completed.append(step.name)
            LOGGER.info("startup step complete: %s", step.name)
        return tuple(self.completed)


__all__ = ["StartupCoordinator", "StartupError", "StartupStep"]
