"""Lossless end-to-end header forwarding."""

from __future__ import annotations

def forwarded_headers(headers: object, *, transformed: bool = False, upstream_host: str | None = None) -> dict[str, str]:
    """Return a compatibility mapping for simple diagnostic callers."""
    return dict(forwarded_header_items(headers, transformed=transformed, upstream_host=upstream_host))


def forwarded_header_items(
    headers: object, *, transformed: bool = False, upstream_host: str | None = None
) -> list[tuple[str, str]]:
    """Return every supplied request header pair in order, including duplicates.

    This gateway is an explicit protocol tunnel. It does not apply the usual
    reverse-proxy filtering: ``Origin``, cookies, and hop-by-hop controls are
    all part of the caller's request contract. ``Host`` is the one transport
    authority field: when an upstream authority is supplied, every Host pair
    is set to that authority so TLS/SNI and HTTP virtual-host routing agree.
    The semantic request fields and all other header pairs remain lossless.
    Compatibility parameters are retained for callers that do not supply an
    upstream authority.
    """
    items = list(headers.items()) if hasattr(headers, "items") else list(headers)  # type: ignore[arg-type]
    result: list[tuple[str, str]] = []
    for key, value in items:
        key_text = str(key)
        result.append((key_text, str(upstream_host) if upstream_host and key_text.lower() == "host" else str(value)))
    return result


def response_headers(headers: object) -> dict[str, str]:
    items = list(headers.items()) if hasattr(headers, "items") else list(headers)  # type: ignore[arg-type]
    return {str(key): str(value) for key, value in items}


def response_header_items(headers: object) -> list[tuple[str, str]]:
    """Return response fields without filtering or collapsing repeated values."""
    items = list(headers.items()) if hasattr(headers, "items") else list(headers)  # type: ignore[arg-type]
    return [(str(key), str(value)) for key, value in items]
