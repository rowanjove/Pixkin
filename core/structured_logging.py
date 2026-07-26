"""Versioned structured application events for local diagnostics."""

import json
import logging
from typing import Any

from core.privacy import redact_value
from core.version import VERSION


EVENT_SCHEMA_VERSION = 1


def log_event(
    logger: logging.Logger,
    level: int,
    *,
    component: str,
    operation: str,
    message: str,
    error_category: str = "",
    **fields: Any,
) -> None:
    payload = {
        "event_schema": EVENT_SCHEMA_VERSION,
        "app_version": VERSION,
        "component": str(component),
        "operation": str(operation),
        "error_category": str(error_category),
        "message": str(message),
        "fields": redact_value(fields),
    }
    logger.log(
        level,
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
