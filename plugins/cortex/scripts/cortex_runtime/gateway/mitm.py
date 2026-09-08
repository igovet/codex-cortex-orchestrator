"""Small owner-local HTTPS CONNECT MITM for the fixed Codex upstream.

The listener is deliberately narrow: only ``chatgpt.com:443`` is accepted,
the client sees a locally generated certificate, and one HTTP/1.1 request is
decoded only when it is the retired manual-compaction path. All other bytes
are relayed without interpretation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import struct
import shutil
import ssl
import subprocess
import tempfile
from urllib.parse import urlsplit

from .classifier import is_compaction_request
from .compression import DecodeError, decode_json, encode_content
from .diagnostics import safe_value
from .transform import TransformError, transform_legacy_compaction


LOGGER = logging.getLogger("cortex.gateway.mitm")
MAX_CHUNKED_BODY_BYTES = 8 * 1024 * 1024
MAX_WS_FRAME_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_WS_MESSAGE_BYTES = 32 * 1024 * 1024
WS_COMPACTION_TYPE = "response.create"


class MitmError(ValueError):
    pass


class _WsFrame:
    __slots__ = ("raw", "first", "opcode", "fin", "rsv1", "control", "payload", "mask")

    def __init__(self, raw: bytes, first: int, opcode: int, fin: bool, rsv1: bool,
                 control: bool, payload: bytes, mask: bytes | None):
        self.raw = raw
        self.first = first
        self.opcode = opcode
        self.fin = fin
        self.rsv1 = rsv1
        self.control = control
        self.payload = payload
        self.mask = mask


def _ws_encode(frame: _WsFrame, payload: bytes) -> bytes:
    """Rebuild one frame while retaining opcode, flags and masking semantics."""
    if len(payload) > MAX_WS_FRAME_PAYLOAD_BYTES:
        raise MitmError("websocket frame payload exceeds the size limit")
    first = frame.first
    mask = frame.mask
    length = len(payload)
    if length < 126:
        header = bytes((first, (0x80 if mask is not None else 0) | length))
    elif length <= 0xFFFF:
        header = bytes((first, (0x80 if mask is not None else 0) | 126)) + struct.pack(">H", length)
    else:
        header = bytes((first, (0x80 if mask is not None else 0) | 127)) + struct.pack(">Q", length)
    if mask is None:
        return header + payload
    encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return header + mask + encoded


def _ws_decode_wire(raw: bytes, *, expect_mask: bool) -> _WsFrame:
    """Decode one complete RFC 6455 frame for deterministic unit testing."""
    if len(raw) < 2:
        raise MitmError("incomplete websocket frame")
    first, second = raw[:2]
    masked = bool(second & 0x80)
    if masked != expect_mask:
        raise MitmError("unexpected websocket mask state")
    length = second & 0x7F
    offset = 2
    if length == 126:
        if len(raw) < offset + 2:
            raise MitmError("incomplete websocket frame length")
        length = struct.unpack(">H", raw[offset:offset + 2])[0]
        offset += 2
    elif length == 127:
        if len(raw) < offset + 8:
            raise MitmError("incomplete websocket frame length")
        length = struct.unpack(">Q", raw[offset:offset + 8])[0]
        offset += 8
        if length & (1 << 63):
            raise MitmError("invalid websocket frame length")
    if length > MAX_WS_FRAME_PAYLOAD_BYTES:
        raise MitmError("websocket frame payload exceeds the size limit")
    mask = raw[offset:offset + 4] if masked else None
    offset += 4 if masked else 0
    if len(raw) != offset + length:
        raise MitmError("websocket frame has trailing or missing bytes")
    wire_payload = raw[offset:]
    payload = wire_payload
    if mask is not None:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(wire_payload))
    opcode = first & 0x0F
    control = opcode >= 8
    if control and (not (first & 0x80) or length > 125):
        raise MitmError("invalid websocket control frame")
    return _WsFrame(raw, first, opcode, bool(first & 0x80), bool(first & 0x40), control, payload, mask)


def _ws_rewrite_target(body: object) -> tuple[dict[str, object], dict[str, object] | None] | None:
    """Return the direct response.create object eligible for compaction rewrite."""
    if not isinstance(body, dict):
        return None
    if body.get("type") == WS_COMPACTION_TYPE and is_compaction_request(body):
        return body, None
    nested = body.get("request")
    if isinstance(nested, dict) and nested.get("type") == WS_COMPACTION_TYPE and is_compaction_request(nested):
        return nested, body
    return None


def rewrite_ws_compaction(payload: bytes, policy: object) -> tuple[bytes, dict[str, str]] | None:
    """Rewrite one uncompressed text message only when it is a direct compaction request."""
    try:
        body = decode_json(payload)
    except DecodeError:
        return None
    target = _ws_rewrite_target(body)
    if target is None:
        return None
    request, wrapper = target
    # WebSocket V2 policy is deliberately limited to the two fields accepted
    # by this transport step.  The HTTP path retains its existing compaction
    # storage/stream normalization; WS keeps all other request fields bytes-
    # semantically unchanged until that policy is separately evidenced.
    transformed = dict(request)
    model = getattr(policy, "model", None)
    effort = getattr(policy, "effort", None)
    if not isinstance(model, str) or not isinstance(effort, str):
        raise TransformError("invalid_compaction_policy", "The active compaction policy is invalid.")
    transformed["model"] = model
    reasoning = request.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, dict):
        raise TransformError("invalid_compaction_reasoning", "The compaction request contains an unsupported reasoning value.")
    next_reasoning = dict(reasoning) if isinstance(reasoning, dict) else {}
    next_reasoning["effort"] = effort
    transformed["reasoning"] = next_reasoning
    if wrapper is None:
        output = transformed
    else:
        output = dict(wrapper)
        output["request"] = transformed
    encoded = json.dumps(output, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    reasoning = request.get("reasoning")
    original_effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    original_store = request.get("store")
    store_label = "missing" if "store" not in request else type(original_store).__name__.lower()
    return encoded, {
        "original_model": safe_value(request.get("model")),
        "routed_model": safe_value(transformed.get("model")),
        "original_reasoning_effort": safe_value(original_effort),
        "routed_reasoning_effort": safe_value(transformed["reasoning"]["effort"]),
        "original_store": store_label,
        "routed_store": store_label,
        "original_stream": type(request.get("stream")).__name__.lower() if "stream" in request else "missing",
        "routed_stream": type(transformed.get("stream")).__name__.lower() if "stream" in transformed else "missing",
    }


def _split_ws_payload(payload: bytes, frames: list[_WsFrame]) -> list[bytes]:
    if len(frames) == 1:
        return [payload]
    total = sum(len(frame.payload) for frame in frames)
    if total == 0:
        return [b"" for _ in frames]
    chunks: list[bytes] = []
    start = 0
    for index, frame in enumerate(frames[:-1]):
        target = round(len(payload) * len(frame.payload) / total)
        target = max(0, min(target, len(payload) - start))
        chunks.append(payload[start:start + target])
        start += target
    chunks.append(payload[start:])
    return chunks


def _without_ws_extensions(headers: list[bytes]) -> list[bytes]:
    return [line for line in headers if not line.lower().startswith(b"sec-websocket-extensions:")]


def _secure_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise MitmError("MITM certificate directory is not owner-only")


def ensure_certificate(codex_home: Path) -> tuple[Path, Path]:
    """Create an owner-only CA and chatgpt.com leaf with OpenSSL once."""
    directory = codex_home / "cortex" / "mitm"
    _secure_dir(directory)
    ca_key, ca_cert = directory / "ca.key", directory / "ca.pem"
    leaf_key, leaf_cert = directory / "chatgpt.key", directory / "chatgpt.pem"
    bundle = directory / "ca-bundle.pem"
    if all(path.is_file() for path in (ca_key, ca_cert, leaf_key, leaf_cert)):
        for path in (ca_key, ca_cert, leaf_key, leaf_cert):
            os.chmod(path, 0o600)
        if not bundle.is_file():
            system_bundle = ssl.get_default_verify_paths().cafile
            bundle.write_bytes((Path(system_bundle).read_bytes() if system_bundle else b"") + b"\n" + ca_cert.read_bytes())
            os.chmod(bundle, 0o600)
        return ca_cert, leaf_cert
    openssl = shutil.which("openssl")
    if not openssl:
        raise MitmError("OpenSSL is required to create the isolated MITM certificate")
    temporary = Path(tempfile.mkdtemp(prefix=".mitm-", dir=directory))
    try:
        key = temporary / "ca.key"
        cert = temporary / "ca.pem"
        leaf_key_tmp = temporary / "chatgpt.key"
        leaf_csr = temporary / "chatgpt.csr"
        leaf_cert_tmp = temporary / "chatgpt.pem"
        ext = temporary / "ext.cnf"
        ext.write_text("subjectAltName=DNS:chatgpt.com\nextendedKeyUsage=serverAuth\n", encoding="ascii")
        commands = [
            [openssl, "genrsa", "-out", str(key), "3072"],
            [openssl, "req", "-x509", "-new", "-nodes", "-key", str(key), "-sha256", "-days", "365", "-subj", "/CN=Cortex Local MITM CA", "-out", str(cert)],
            [openssl, "genrsa", "-out", str(leaf_key_tmp), "2048"],
            [openssl, "req", "-new", "-key", str(leaf_key_tmp), "-subj", "/CN=chatgpt.com", "-out", str(leaf_csr)],
            [openssl, "x509", "-req", "-in", str(leaf_csr), "-CA", str(cert), "-CAkey", str(key), "-CAcreateserial", "-out", str(leaf_cert_tmp), "-days", "365", "-sha256", "-extfile", str(ext)],
        ]
        for command in commands:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for source, destination in ((key, ca_key), (cert, ca_cert), (leaf_key_tmp, leaf_key), (leaf_cert_tmp, leaf_cert)):
            os.replace(source, destination)
            os.chmod(destination, 0o600)
        system_bundle = ssl.get_default_verify_paths().cafile
        bundle.write_bytes((Path(system_bundle).read_bytes() if system_bundle else b"") + b"\n" + ca_cert.read_bytes())
        os.chmod(bundle, 0o600)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return ca_cert, leaf_cert


class MitmProxy:
    def __init__(self, *, host: str, port: int, codex_home: Path, manager):
        self.host, self.port, self.codex_home, self.manager = host, port, codex_home, manager
        self.server: asyncio.AbstractServer | None = None
        self.ca_cert, self.leaf_cert = ensure_certificate(codex_home)
        self.leaf_key = self.leaf_cert.with_name("chatgpt.key")

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, self.host, self.port)
        self.port = int(self.server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            line = await reader.readline()
            if not line.startswith(b"CONNECT ") or not line.endswith(b" HTTP/1.1\r\n"):
                writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            authority = line[8:-11].decode("ascii", "strict")
            if authority != "chatgpt.com:443":
                writer.write(b"HTTP/1.1 421 Misdirected Request\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            while True:
                header = await reader.readline()
                if header in {b"\r\n", b"\n", b""}:
                    break
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            loop = asyncio.get_running_loop()
            transport = writer.transport
            protocol = transport.get_protocol()
            if protocol is None:
                return
            client_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            client_ssl.minimum_version = ssl.TLSVersion.TLSv1_2
            client_ssl.load_cert_chain(self.leaf_cert, self.leaf_key)
            transport = await loop.start_tls(transport, protocol, client_ssl, server_side=True)
            writer._transport = transport  # type: ignore[attr-defined]
            upstream_ssl = ssl.create_default_context()
            upstream_reader, upstream_writer = await asyncio.open_connection("chatgpt.com", 443, ssl=upstream_ssl, server_hostname="chatgpt.com")
            await self._relay_http(reader, writer, upstream_reader, upstream_writer)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ssl.SSLError, UnicodeError, ValueError):
            pass
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                with contextlib.suppress(Exception):
                    await upstream_writer.wait_closed()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _relay_http(self, reader, writer, upstream_reader, upstream_writer) -> None:
        request_line = await reader.readline()
        headers = []
        content_length = 0
        transfer_chunked = False
        while True:
            line = await reader.readline()
            if line in {b"\r\n", b"\n", b""}:
                break
            headers.append(line)
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())
            if line.lower().startswith(b"transfer-encoding:"):
                transfer_chunked = any(part.strip().lower() == b"chunked" for part in line.split(b":", 1)[1].split(b","))
        if transfer_chunked:
            wire_body, body = await self._read_chunked_body(reader)
        else:
            wire_body = body = await reader.readexactly(content_length)
        path = request_line.split(b" ", 2)[1].decode("ascii", "strict")
        outgoing_line, outgoing_body = request_line, wire_body
        websocket_upgrade = path.split("?", 1)[0] == "/backend-api/codex/responses" and any(
            line.lower().startswith(b"upgrade:") and b"websocket" in line.lower()
            for line in headers
        )
        # The upstream advertises permessage-deflate.  Remove that optional
        # extension so the narrow inspector can see JSON frames.  All other
        # request header pairs and bytes remain untouched.
        if websocket_upgrade:
            headers = _without_ws_extensions(headers)
        if path.split("?", 1)[0] == "/backend-api/codex/responses/compact":
            values = {line.split(b":", 1)[0].lower(): line.split(b":", 1)[1].strip() for line in headers if b":" in line}
            try:
                decoded = decode_json(body, values.get(b"content-encoding", b"").decode() or None)
                transformed = transform_legacy_compaction(decoded, self.manager.snapshot())
                outgoing_body = encode_content(json.dumps(transformed, separators=(",", ":")).encode(), values.get(b"content-encoding", b"").decode() or None)
                outgoing_line = request_line.replace(b"/backend-api/codex/responses/compact", b"/backend-api/codex/responses", 1)
                headers = [line for line in headers if not line.lower().startswith((b"content-length:", b"transfer-encoding:"))]
                headers.append(f"Content-Length: {len(outgoing_body)}\r\n".encode())
            except (DecodeError, TransformError, ValueError):
                pass
        LOGGER.info(
            "mitm request authority=chatgpt.com:443 upstream=chatgpt.com:443 method=%s path=%s",
            request_line.split(b" ", 2)[0].decode("ascii", "replace"),
            path.split("?", 1)[0],
        )
        upstream_writer.write(outgoing_line + b"".join(headers) + b"\r\n" + outgoing_body)
        await upstream_writer.drain()
        status_line = await upstream_reader.readline()
        if not status_line:
            return
        writer.write(status_line)
        status = status_line.split(b" ", 2)[1].decode("ascii", "replace") if len(status_line.split(b" ", 2)) > 1 else "?"
        LOGGER.info("mitm response upstream=chatgpt.com:443 status=%s path=%s", status, path.split("?", 1)[0])
        response_headers = []
        while True:
            header = await upstream_reader.readline()
            if status == "101" and websocket_upgrade and header.lower().startswith(b"sec-websocket-extensions:"):
                continue
            response_headers.append(header)
            if header in {b"\r\n", b"\n", b""}:
                break
        writer.write(b"".join(response_headers))
        await writer.drain()
        # The Responses transport upgrades to WebSocket (101).  Once the
        # handshake headers have crossed the tunnel, both directions must be
        # copied concurrently; a sequential HTTP-body relay would wait for a
        # peer close and leave Codex's live stream suspended forever.
        if status == "101" and websocket_upgrade:
            await asyncio.gather(
                self._copy_websocket_client(reader, upstream_writer, path),
                self._copy_stream(upstream_reader, writer),
            )
            return
        while True:
            chunk = await upstream_reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()

    async def _copy_stream(self, reader, writer) -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass

    async def _read_ws_frame(self, reader, *, expect_mask: bool) -> _WsFrame:
        header = await reader.readexactly(2)
        first, second = header
        masked = bool(second & 0x80)
        if masked != expect_mask:
            raise MitmError("unexpected websocket mask state")
        length = second & 0x7F
        extended = b""
        if length == 126:
            extended = await reader.readexactly(2)
            length = struct.unpack(">H", extended)[0]
        elif length == 127:
            extended = await reader.readexactly(8)
            length = struct.unpack(">Q", extended)[0]
            if length & (1 << 63):
                raise MitmError("invalid websocket frame length")
        if length > MAX_WS_FRAME_PAYLOAD_BYTES:
            raise MitmError("websocket frame payload exceeds the size limit")
        mask = await reader.readexactly(4) if masked else None
        wire_payload = await reader.readexactly(length)
        payload = wire_payload
        if mask is not None:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(wire_payload))
        opcode = first & 0x0F
        control = opcode >= 8
        if control and (not (first & 0x80) or length > 125):
            raise MitmError("invalid websocket control frame")
        raw = header + extended + (mask or b"") + wire_payload
        return _WsFrame(raw, first, opcode, bool(first & 0x80), bool(first & 0x40), control, payload, mask)

    async def _copy_websocket_client(self, reader, writer, path: str) -> None:
        """Inspect complete client messages, rewriting only direct compaction JSON."""
        pending: list[tuple[str, _WsFrame]] = []
        fragments: list[_WsFrame] = []
        message_opcode: int | None = None
        message_compressed = False
        message_reserved = False
        while True:
            frame = await self._read_ws_frame(reader, expect_mask=True)
            if frame.control:
                # Control frames interleaved in a fragmented message stay in
                # their original position and are flushed with that message.
                if fragments:
                    pending.append(("control", frame))
                else:
                    writer.write(frame.raw)
                    await writer.drain()
                continue
            if message_opcode is None:
                if frame.opcode not in (1, 2):
                    raise MitmError("invalid websocket data opcode")
                message_opcode = frame.opcode
                message_compressed = frame.rsv1
                message_reserved = bool(frame.first & 0x30)
            elif frame.opcode != 0:
                raise MitmError("invalid websocket continuation opcode")
            fragments.append(frame)
            pending.append(("data", frame))
            if sum(len(item.payload) for item in fragments) > MAX_WS_MESSAGE_BYTES:
                raise MitmError("websocket message exceeds the size limit")
            if not frame.fin:
                continue
            replacement: tuple[bytes, dict[str, str]] | None = None
            if message_opcode == 1 and not message_reserved:
                replacement = rewrite_ws_compaction(b"".join(item.payload for item in fragments), self.manager.snapshot())
            if replacement is None:
                if message_compressed:
                    LOGGER.info("mitm websocket path=%s outcome=passthrough reason=compressed", path.split("?", 1)[0])
                for kind, item in pending:
                    writer.write(item.raw)
            else:
                transformed, fields = replacement
                chunks = _split_ws_payload(transformed, fragments)
                data_index = 0
                for kind, item in pending:
                    if kind == "control":
                        writer.write(item.raw)
                    else:
                        writer.write(_ws_encode(item, chunks[data_index]))
                        data_index += 1
                LOGGER.info(
                    "mitm websocket_compaction path=%s fragments=%d original_model=%s routed_model=%s "
                    "original_reasoning_effort=%s routed_reasoning_effort=%s original_store=%s routed_store=%s "
                    "original_stream=%s routed_stream=%s",
                    path.split("?", 1)[0], len(fragments), fields["original_model"], fields["routed_model"],
                    fields["original_reasoning_effort"], fields["routed_reasoning_effort"],
                    fields["original_store"], fields["routed_store"], fields["original_stream"], fields["routed_stream"],
                )
            await writer.drain()
            pending.clear()
            fragments.clear()
            message_opcode = None
            message_compressed = False
            message_reserved = False

    async def _read_chunked_body(self, reader) -> tuple[bytes, bytes]:
        """Read one HTTP chunked body while retaining exact wire bytes."""
        wire = bytearray()
        decoded = bytearray()
        while True:
            line = await reader.readline()
            if not line.endswith(b"\r\n"):
                raise ValueError("invalid chunk framing")
            wire.extend(line)
            size_token = line[:-2].split(b";", 1)[0].strip()
            size = int(size_token, 16)
            if size < 0 or len(decoded) + size > MAX_CHUNKED_BODY_BYTES:
                raise ValueError("chunked body exceeds size limit")
            if size:
                chunk = await reader.readexactly(size + 2)
                if chunk[-2:] != b"\r\n":
                    raise ValueError("invalid chunk terminator")
                wire.extend(chunk)
                decoded.extend(chunk[:-2])
                continue
            while True:
                trailer = await reader.readline()
                if not trailer:
                    raise ValueError("invalid chunk trailer")
                wire.extend(trailer)
                if trailer == b"\r\n":
                    return bytes(wire), bytes(decoded)


import contextlib
