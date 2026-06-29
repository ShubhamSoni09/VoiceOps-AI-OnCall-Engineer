from __future__ import annotations

import re
from typing import Any


_SECRET_PATTERNS = [
    (re.compile(r"(https?://)(?:[^/@\s]+)@github\.com", re.IGNORECASE), r"\1[redacted]@github.com"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"), "[redacted]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[redacted]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{12,}\b"), "[redacted]"),
    (
        re.compile(
            r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret)\s*[:=]\s*"
            r"([\"']?)[^\s,\"'}\]]{8,}\2",
            re.IGNORECASE,
        ),
        r"\1=[redacted]",
    ),
]


def redact_sensitive_text(text: str) -> str:
    cleaned = text
    for pattern, replacement in _SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def redact_sensitive_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_sensitive_value(item) for key, item in value.items()}
    return value
