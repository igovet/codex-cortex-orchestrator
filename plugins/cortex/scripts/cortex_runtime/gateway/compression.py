"""Bounded request decoding for identity, gzip and optional zstd."""

from __future__ import annotations

import json
from typing import Any
import zlib

from .defaults import MAX_BODY_BYTES, MAX_COMPRESSED_BODY_BYTES, MAX_JSON_DEPTH


class DecodeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DecodeError("duplicate_json_key", "The request contains duplicate JSON keys.")
        result[key] = value
    return result


def _check_depth(value: object, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise DecodeError("json_depth_limit", "The request JSON nesting is too deep.")
    if isinstance(value, dict):
        for child in value.values():
            _check_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_depth(child, depth + 1)


def decode_content(body: bytes, encoding: str | None) -> bytes:
    if len(body) > MAX_COMPRESSED_BODY_BYTES:
        raise DecodeError("request_body_too_large", "The request body exceeds the compressed size limit.")
    value = (encoding or "identity").strip().lower()
    if value in ("", "identity"):
        decoded = body
    elif value == "gzip":
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        parts: list[bytes] = []
        total = 0
        try:
            for offset in range(0, len(body), 64 * 1024):
                pending = body[offset:offset + 64 * 1024]
                while pending:
                    piece = decoder.decompress(pending, MAX_BODY_BYTES - total + 1)
                    total += len(piece)
                    if total > MAX_BODY_BYTES:
                        raise DecodeError("request_body_too_large", "The decoded request body exceeds the size limit.")
                    parts.append(piece)
                    pending = decoder.unconsumed_tail
                    if not pending:
                        break
            tail = decoder.flush(MAX_BODY_BYTES - total + 1)
            total += len(tail)
            if total > MAX_BODY_BYTES:
                raise DecodeError("request_body_too_large", "The decoded request body exceeds the size limit.")
            parts.append(tail)
            if not decoder.eof or decoder.unused_data:
                raise DecodeError("invalid_content_encoding", "The gzip request body is invalid.")
            decoded = b"".join(parts)
        except DecodeError:
            raise
        except (OSError, EOFError, zlib.error) as exc:
            raise DecodeError("invalid_content_encoding", "The gzip request body is invalid.") from exc
    elif value == "zstd":
        try:
            import zstandard  # type: ignore
        except ImportError as exc:
            raise DecodeError("unsupported_content_encoding", "The zstd request encoding is unavailable.") from exc
        try:
            # Do not use ``ZstdDecompressor.decompress`` here.  Its one-shot
            # path may materialize a large known-size frame before applying
            # max_output_size, and it silently accepts concatenated frames.
            params = zstandard.get_frame_parameters(body)
            if params.window_size > MAX_BODY_BYTES:
                raise DecodeError("request_body_too_large", "The decoded request body exceeds the size limit.")
            content_size = params.content_size
            if content_size not in (zstandard.CONTENTSIZE_UNKNOWN, zstandard.CONTENTSIZE_ERROR) and content_size > MAX_BODY_BYTES:
                raise DecodeError("request_body_too_large", "The decoded request body exceeds the size limit.")
            if content_size not in (zstandard.CONTENTSIZE_UNKNOWN, zstandard.CONTENTSIZE_ERROR):
                # A known-size frame is already proven to fit, so the object
                # API is safe here and exposes unused_data for exact trailing
                # frame rejection (including an empty first frame).
                decoder = zstandard.ZstdDecompressor().decompressobj(read_across_frames=False)
                decoded = decoder.decompress(body) + decoder.flush()
                if not decoder.eof or decoder.unused_data:
                    raise DecodeError("invalid_content_encoding", "The zstd request body is invalid.")
            else:
                # The streaming reader applies the output bound while
                # decoding frames whose advertised content size is unknown.
                reader = zstandard.ZstdDecompressor().stream_reader(
                    body, read_size=64 * 1024, read_across_frames=False
                )
                try:
                    decoded = reader.read(MAX_BODY_BYTES + 1)
                    if len(decoded) > MAX_BODY_BYTES:
                        raise DecodeError("request_body_too_large", "The decoded request body exceeds the size limit.")
                    # A second read must be EOF.  This rejects concatenated
                    # frames and trailing bytes rather than silently discarding
                    # them.
                    if reader.read(1):
                        raise DecodeError("invalid_content_encoding", "The zstd request body is invalid.")
                finally:
                    reader.close()
        except DecodeError:
            raise
        except Exception as exc:  # library has several version-specific exception types
            raise DecodeError("invalid_content_encoding", "The zstd request body is invalid.") from exc
    else:
        raise DecodeError("unsupported_content_encoding", "The request content encoding is unsupported.")
    if len(decoded) > MAX_BODY_BYTES:
        raise DecodeError("request_body_too_large", "The decoded request body exceeds the size limit.")
    return decoded


def decode_json(body: bytes, encoding: str | None = None) -> object:
    decoded = decode_content(body, encoding)
    try:
        value = json.loads(decoded.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except DecodeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecodeError("invalid_json", "The request body is not valid JSON.") from exc
    _check_depth(value)
    return value


def encode_content(body: bytes, encoding: str | None) -> bytes:
    """Encode a rewritten body using the caller's original representation."""
    if len(body) > MAX_BODY_BYTES:
        raise DecodeError("request_body_too_large", "The decoded request body exceeds the size limit.")
    value = (encoding or "identity").strip().lower()
    if value in ("", "identity"):
        return body
    if value == "gzip":
        compressor = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
        encoded = compressor.compress(body) + compressor.flush()
    elif value == "zstd":
        try:
            import zstandard  # type: ignore
        except ImportError as exc:
            raise DecodeError("unsupported_content_encoding", "The zstd request encoding is unavailable.") from exc
        encoded = zstandard.ZstdCompressor().compress(body)
    else:
        raise DecodeError("unsupported_content_encoding", "The request content encoding is unsupported.")
    if len(encoded) > MAX_COMPRESSED_BODY_BYTES:
        raise DecodeError("request_body_too_large", "The request body exceeds the compressed size limit.")
    return encoded
