"""Shared secret redaction for logs and local persistence boundaries."""

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit


SECRET_TEXT_PATTERN = re.compile(
    r"(?i)(?:"
    r"\b(?:sk|key)-[a-z0-9_-]{8,}\b|"
    r"\bBearer\s+(?!<redacted>)[a-z0-9._-]{8,}"
    r")"
)
SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
)
SENSITIVE_TOKEN_KEY_PATTERN = re.compile(r"(?:^|_)token(?:_|$)")
PRIVACY_NOTICE_VERSION = 1


def normalized_endpoint(value: Any) -> str:
    """Return a stable endpoint identity without credentials or fragments."""
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        hostname = (parsed.hostname or "").lower()
        scheme = parsed.scheme.lower()
        port = parsed.port
    except ValueError:
        return raw
    if not scheme or not hostname:
        return raw
    default_port = (
        (scheme == "https" and port == 443)
        or (scheme == "http" and port == 80)
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def privacy_scope_fingerprint(
    *,
    endpoint: Any,
    model: Any,
    data_scope: str,
) -> str:
    """Bind a privacy decision to its recipient and disclosed data scope."""
    payload = {
        "notice_version": PRIVACY_NOTICE_VERSION,
        "endpoint": normalized_endpoint(endpoint),
        "model": str(model or "").strip(),
        "data_scope": str(data_scope),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def redact_text(value: Any) -> str:
    return SECRET_TEXT_PATTERN.sub("<redacted-secret>", str(value))


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if any(marker in lowered for marker in SENSITIVE_KEY_MARKERS):
        return True
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    # “token”作为独立字段段表示凭据；input_tokens、max_tokens 等
    # 复数计数指标不属于秘密，必须原样保留。
    return bool(SENSITIVE_TOKEN_KEY_PATTERN.search(normalized))


def redact_value(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_key(key):
        return "<redacted-secret>"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(
                item_value,
                key=str(item_key),
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class SecretRedactionFilter(logging.Filter):
    """Redact common credential shapes before a handler formats a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.msg)
        record.args = redact_value(record.args)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        return True
