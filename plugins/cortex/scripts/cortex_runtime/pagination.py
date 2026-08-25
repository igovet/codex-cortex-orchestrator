"""One opaque fixed-page cursor codec for every public Cortex read."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any


CURSOR_VERSION = 1
CURSOR_PREFIX = "c11p."
CURSOR_PATTERN = r"^c11p\.[A-Za-z0-9_-]{16,512}$"
CURSOR_MAX_CHARS = 517
_SIGNATURE_BYTES = 32
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


def scope_digest(value: object) -> str:
    """Return the canonical content/selector binding used by public cursors."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_binding(selector: str, audience: str, digest: str) -> None:
    if _LABEL_PATTERN.fullmatch(selector) is None:
        raise ValueError("cursor selector is invalid")
    if _LABEL_PATTERN.fullmatch(audience) is None:
        raise ValueError("cursor audience is invalid")
    if _DIGEST_PATTERN.fullmatch(digest) is None:
        raise ValueError("cursor digest binding is invalid")


def encode_cursor(
    secret: bytes,
    *,
    selector: str,
    audience: str,
    digest: str,
    offset: int,
) -> str:
    """Sign one versioned cursor bound to selector, audience, digest, and offset."""
    _validate_binding(selector, audience, digest)
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("cursor HMAC key is invalid")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("cursor offset is invalid")
    payload = json.dumps(
        {"v": CURSOR_VERSION, "s": selector, "a": audience, "d": digest, "o": offset},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")
    cursor = CURSOR_PREFIX + token
    if len(cursor) > CURSOR_MAX_CHARS or re.fullmatch(CURSOR_PATTERN, cursor) is None:
        raise ValueError("cursor exceeds its safe transport shape")
    return cursor


def decode_cursor(
    cursor: object,
    secret: bytes,
    *,
    selector: str,
    audience: str,
    digest: str,
) -> int:
    """Verify one exact public cursor and return its server-owned offset."""
    _validate_binding(selector, audience, digest)
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("cursor HMAC key is invalid")
    if not isinstance(cursor, str) or re.fullmatch(CURSOR_PATTERN, cursor) is None:
        raise ValueError("cursor is invalid")
    token = cursor[len(CURSOR_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        if len(raw) <= _SIGNATURE_BYTES:
            raise ValueError("cursor is invalid")
        payload, supplied = raw[:-_SIGNATURE_BYTES], raw[-_SIGNATURE_BYTES:]
        expected = hmac.new(secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("cursor signature is invalid")
        decoded: Any = json.loads(payload.decode("utf-8"))
    except (TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    expected_payload: Mapping[str, object] = {
        "v": CURSOR_VERSION, "s": selector, "a": audience, "d": digest,
    }
    if not isinstance(decoded, dict) or any(decoded.get(key) != value for key, value in expected_payload.items()):
        raise ValueError("cursor does not belong to this exact read selector, audience, or content version")
    if set(decoded) != {"v", "s", "a", "d", "o"}:
        raise ValueError("cursor payload is invalid")
    offset = decoded.get("o")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("cursor offset is invalid")
    return offset


def page_utf8_text(value: str, byte_offset: int, *, maximum_bytes: int) -> tuple[str, int, bool]:
    """Return one exact UTF-8-safe page using server-owned byte offsets."""
    if not isinstance(value, str):
        raise TypeError("paged text must be a string")
    if isinstance(byte_offset, bool) or not isinstance(byte_offset, int) or byte_offset < 0:
        raise ValueError("page byte offset is invalid")
    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or maximum_bytes < 4:
        raise ValueError("page byte limit is invalid")
    raw = value.encode("utf-8")
    if byte_offset > len(raw):
        raise ValueError("page byte offset is outside the text")
    if byte_offset < len(raw) and raw[byte_offset] & 0xC0 == 0x80:
        raise ValueError("page byte offset splits a UTF-8 scalar")
    end = min(len(raw), byte_offset + maximum_bytes)
    while end < len(raw) and raw[end] & 0xC0 == 0x80:
        end -= 1
    content = raw[byte_offset:end].decode("utf-8")
    return content, end, end == len(raw)


__all__ = [
    "CURSOR_MAX_CHARS", "CURSOR_PATTERN", "CURSOR_PREFIX", "CURSOR_VERSION",
    "decode_cursor", "encode_cursor", "page_utf8_text", "scope_digest",
]
