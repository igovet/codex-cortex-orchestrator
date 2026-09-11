"""Loopback HTTP/SSE proxy for the fixed Codex backend namespace."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import ipaddress
import logging
import time
from urllib.parse import urlsplit

try:
    from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, TCPConnector, TraceConfig, WSMsgType, WSServerHandshakeError, web
except ImportError as exc:  # pragma: no cover - exercised by packaging checks
    raise RuntimeError("Cortex Model Gateway requires the aiohttp dependency") from exc

from .classifier import is_compaction_request
from .compression import DecodeError, decode_json, encode_content
from .config import ConfigError, ConfigManager, PolicySnapshot
from .defaults import (
    BODY_READ_TIMEOUT_SECONDS,
    MAX_COMPRESSED_BODY_BYTES,
    UPSTREAM_CONNECT_TIMEOUT_SECONDS,
    UPSTREAM_READ_TIMEOUT_SECONDS,
)
from .diagnostics import generated_error, log_outcome, request_id
from .headers import forwarded_header_items, response_header_items
from .transform import TransformError, transform_compaction, transform_legacy_compaction
from .mitm import rewrite_ws_compaction

LOGGER = logging.getLogger("cortex.gateway")
NAMESPACE = "/backend-api/codex"
V2_PATH = "/backend-api/codex/responses"
LEGACY_PATH = "/backend-api/codex/responses/compact"


def _valid_loopback_authority(value: str | None) -> bool:
    """Accept only explicit loopback host authorities with valid ports."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if value.startswith("["):
        closing = value.find("]")
        if closing <= 1:
            return False
        host = value[1:closing]
        suffix = value[closing + 1:]
        if suffix and (not suffix.startswith(":") or not _valid_port(suffix[1:])):
            return False
        try:
            return ipaddress.ip_address(host) == ipaddress.IPv6Address("::1")
        except ValueError:
            return False
    if value.count(":") == 0:
        host = value.lower()
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host) == ipaddress.IPv4Address("127.0.0.1")
        except ValueError:
            return False
    if value.count(":") != 1:
        return False
    host, port = value.rsplit(":", 1)
    if not _valid_port(port):
        return False
    host = host.lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host) == ipaddress.IPv4Address("127.0.0.1")
    except ValueError:
        return False


def _valid_port(value: str) -> bool:
    try:
        return bool(value) and 1 <= int(value) <= 65535 and str(int(value)) == value
    except (TypeError, ValueError):
        return False


class GatewayProxy:
    def __init__(self, manager: ConfigManager, *, session: ClientSession | None = None, identity: dict[str, object] | None = None):
        self.manager = manager
        self._session = session
        self._owns_session = session is None
        self._active = 0
        self._draining = False
        self.identity = identity or {}

    @property
    def active_requests(self) -> int:
        return self._active

    async def start(self) -> None:
        if self._session is None:
            trace = TraceConfig()

            async def reject_redirect(*_args):
                # ws_connect uses the HTTP client's redirect machinery. Stop
                # before a second request can carry authentication elsewhere.
                raise ConnectionError("upstream redirects are not supported")

            trace.on_request_redirect.append(reject_redirect)
            self._session = ClientSession(
                # Do not impose a fixed connection/slot ceiling. Request
                # admission is governed by the existing transport, policy,
                # body and cancellation checks below, not a proxy semaphore.
                connector=TCPConnector(limit=0, enable_cleanup_closed=True),
                timeout=ClientTimeout(total=None, sock_connect=UPSTREAM_CONNECT_TIMEOUT_SECONDS, sock_read=UPSTREAM_READ_TIMEOUT_SECONDS),
                auto_decompress=False,
                # aiohttp otherwise adds ``Accept-Encoding`` to requests that
                # did not contain it.  That can make a raw upstream response
                # (notably the model catalogue) arrive in an encoding the
                # Codex client did not request and then fail JSON decoding.
                # skip_auto_headers suppresses only the implicit header; an
                # explicit caller-supplied value remains in ``headers``.
                skip_auto_headers={"Accept-Encoding"},
                cookie_jar=DummyCookieJar(),
                trace_configs=[trace],
            )

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def begin_drain(self, deadline: float = 30.0) -> None:
        self._draining = True
        end = asyncio.get_running_loop().time() + deadline
        while self._active and asyncio.get_running_loop().time() < end:
            await asyncio.sleep(0.05)

    def _error_response(self, error: DecodeError | TransformError | ConfigError, status: int = 400) -> web.Response:
        code = getattr(error, "code", "invalid_configuration" if isinstance(error, ConfigError) else "gateway_error")
        message = getattr(error, "message", str(error))
        status, body, headers = generated_error(code, message, status=status)
        return web.Response(status=status, body=body, headers=headers)

    def _health(self, snapshot: PolicySnapshot) -> web.Response:
        payload = {
            "status": "draining" if self._draining else "ok" if snapshot.gateway_enabled else "disabled",
            "service": "cortex-model-gateway",
            **self.identity,
            **snapshot.public_status(),
        }
        return web.json_response(payload)

    def _log_request(self, request: web.Request, *, status: int | None, outcome: str, started: float) -> None:
        log_outcome(
            LOGGER,
            request_id=request.get("gateway_request_id"),
            request_kind=request.get("gateway_request_kind", "gateway"),
            original_model=request.get("gateway_original_model"),
            routed_model=request.get("gateway_routed_model"),
            original_reasoning_effort=request.get("gateway_original_effort"),
            routed_reasoning_effort=request.get("gateway_routed_effort"),
            config_revision=request.get("gateway_config_revision"),
            http_status=status,
            transport_outcome=outcome,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
        )

    async def _wait_client_disconnect(self, request: web.Request) -> None:
        transport = request.transport
        if transport is None:
            return
        while not transport.is_closing():
            await asyncio.sleep(0.02)

    async def _cancel_upstream(self, task: asyncio.Task | None) -> None:
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _target(self, request: web.Request, snapshot: PolicySnapshot) -> str:
        raw_path = request.raw_path.split("?", 1)[0]
        if not raw_path or raw_path != request.path or not raw_path.startswith(NAMESPACE):
            raise ValueError("unsupported_backend_namespace")
        if raw_path != NAMESPACE and not raw_path.startswith(NAMESPACE + "/"):
            raise ValueError("unsupported_backend_namespace")
        if any(
            char == "\\" or ord(char) < 0x20 or char == "%"
            for char in raw_path
        ):
            raise ValueError("ambiguous_backend_path")
        suffix = raw_path[len(NAMESPACE):]
        segments = [] if not suffix else suffix[1:].split("/")
        if any(not segment or segment in {".", ".."} for segment in segments):
            raise ValueError("ambiguous_backend_path")
        safe_path = NAMESPACE if not segments else NAMESPACE + "/" + "/".join(segments)
        parsed = urlsplit(snapshot.upstream)
        query = request.raw_path.partition("?")[2]
        target = f"{parsed.scheme}://{parsed.netloc}{safe_path}" + (f"?{query}" if query else "")
        final = urlsplit(target)
        local_test_upstream = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (parsed.scheme != "https" and not local_test_upstream) or final.scheme != parsed.scheme or final.netloc != parsed.netloc or not (final.path == NAMESPACE or final.path.startswith(NAMESPACE + "/")):
            raise ValueError("unsupported_backend_namespace")
        return target

    async def handle(self, request: web.Request) -> web.StreamResponse:
        started = time.monotonic()
        outcome = "completed"
        status_code: int | None = None
        request["gateway_request_id"] = request_id()
        request["gateway_request_kind"] = "health" if request.path == "/health" else "ordinary"
        if not _valid_loopback_authority(request.headers.get("Host")):
            status, body, headers = generated_error("invalid_host", "The gateway accepts loopback Host headers only.", status=421)
            outcome = "rejected_host"
            response = web.Response(status=status, body=body, headers=headers)
            status_code = status
            self._log_request(request, status=status, outcome=outcome, started=started)
            return response
        if request.path == "/health" and request.method == "GET":
            try:
                snapshot = self.manager.snapshot()
                request["gateway_config_revision"] = snapshot.revision
                response = self._health(snapshot)
                status_code = response.status
                self._log_request(request, status=status_code, outcome="completed", started=started)
                return response
            except ConfigError as exc:
                response = self._error_response(exc, 503)
                status_code = response.status
                outcome = "health_unavailable"
                self._log_request(request, status=status_code, outcome=outcome, started=started)
                return response
        if self._draining:
            status, body, headers = generated_error("gateway_draining", "The gateway is draining and accepts no new requests.", status=503)
            response = web.Response(status=status, body=body, headers=headers)
            status_code = status
            outcome = "rejected_draining"
            self._log_request(request, status=status, outcome=outcome, started=started)
            return response
        # Track in-flight work for controlled drain only. There is no
        # admission limit: every request that passes the checks above proceeds
        # through the ordinary forwarding path.
        self._active += 1
        try:
            try:
                response = await self._proxy_request(request)
                status_code = response.status
                outcome = str(request.get("gateway_outcome", outcome))
                return response
            except asyncio.CancelledError:
                # aiohttp cancels handlers when the peer disconnects.  The
                # proxy closes/cancels its upstream in _proxy_request; avoid
                # an expected disconnect traceback in the server log.
                outcome = str(request.get("gateway_outcome", "client_disconnected"))
                status_code = 499
                return web.Response(status=499, body=b"")
            except (ConnectionResetError, BrokenPipeError):
                outcome = "client_disconnected"
                status_code = 499
                return web.Response(status=499, body=b"")
            except Exception:
                outcome = "gateway_error"
                status_code = 500
                status, body, headers = generated_error("gateway_error", "The gateway could not complete the request.", status=500)
                return web.Response(status=status, body=body, headers=headers)
        finally:
            self._active = max(0, self._active - 1)
            self._log_request(request, status=status_code, outcome=outcome, started=started)

    async def _proxy_request(self, request: web.Request) -> web.StreamResponse:
        try:
            snapshot = self.manager.snapshot()
            request["gateway_config_revision"] = snapshot.revision
            if not snapshot.gateway_enabled:
                request["gateway_outcome"] = "gateway_disabled"
                status, body, headers = generated_error(
                    "gateway_disabled", "The model gateway is disabled.", status=503
                )
                return web.Response(status=status, body=body, headers=headers)
            target = self._target(request, snapshot)
        except (ConfigError, ValueError) as exc:
            if isinstance(exc, ConfigError):
                request["gateway_outcome"] = "configuration_rejected"
                return self._error_response(exc, 503)
            request["gateway_outcome"] = "namespace_rejected"
            status, body, headers = generated_error(str(exc), "The requested backend namespace is not supported.", status=404)
            return web.Response(status=status, body=body, headers=headers)

        if request.headers.get("Upgrade", "").lower() == "websocket":
            if request.method != "GET" or request.path != V2_PATH:
                return web.Response(status=404)
            return await self._proxy_websocket(request, snapshot, target)

        legacy_compaction = request.path == LEGACY_PATH
        if legacy_compaction:
            # A legacy path is a manual compaction request when its body can
            # be decoded. Opaque legacy wire formats remain explicitly
            # unclassified and are forwarded byte-for-byte.
            request["gateway_request_kind"] = "legacy"

        transformed = False
        upstream_target = target
        original_model = None
        original_effort = None
        outgoing = b""
        try:
            if request.can_read_body:
                if request.content_length is not None and request.content_length > MAX_COMPRESSED_BODY_BYTES:
                    raise DecodeError("request_body_too_large", "The request body exceeds the compressed size limit.")
                try:
                    async with asyncio.timeout(BODY_READ_TIMEOUT_SECONDS):
                        outgoing = await request.read()
                except TimeoutError as exc:
                    raise DecodeError("request_body_timeout", "The request body read timed out.") from exc
            decoded = None
            should_decode = (
                snapshot.router_enabled
                and request.method.upper() == "POST"
                and request.path in {V2_PATH, LEGACY_PATH}
            )
            if should_decode:
                try:
                    decoded = decode_json(outgoing, request.headers.get("Content-Encoding"))
                except DecodeError as exc:
                    # Invalid JSON is still valid opaque ordinary/legacy wire
                    # traffic. Other encoding/size failures remain explicit.
                    if exc.code != "invalid_json":
                        raise
                    decoded = None
            body = decoded
            if isinstance(body, dict):
                original_model = body.get("model")
                reasoning = body.get("reasoning")
                original_effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
                request["gateway_original_model"] = original_model
                request["gateway_original_effort"] = original_effort
                if legacy_compaction:
                    request["gateway_request_kind"] = "manual_compaction"
            should_classify = (
                snapshot.router_enabled
                and snapshot.compaction_enabled
                and request.method.upper() == "POST"
                and request.path in {V2_PATH, LEGACY_PATH}
            )
            if should_classify:
                if legacy_compaction and isinstance(body, dict):
                    body = transform_legacy_compaction(body, snapshot)
                    outgoing = encode_content(
                        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                        request.headers.get("Content-Encoding"),
                    )
                    transformed = True
                    # Production no longer exposes the legacy compact path.
                    # Preserve opaque legacy traffic, but route recognized
                    # JSON compaction through the supported V2 endpoint.
                    parsed_target = urlsplit(target)
                    upstream_target = parsed_target._replace(path=V2_PATH).geturl()
                elif not legacy_compaction and is_compaction_request(body):
                    request["gateway_request_kind"] = "auto_compaction"
                    body = transform_compaction(body, snapshot)
                    outgoing = encode_content(
                        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                        request.headers.get("Content-Encoding"),
                    )
                    transformed = True
            request["gateway_routed_model"] = snapshot.model if transformed else original_model
            request["gateway_routed_effort"] = snapshot.effort if transformed else original_effort
        except (DecodeError, TransformError) as exc:
            request["gateway_outcome"] = "decode_rejected"
            return self._error_response(exc, 400)

        # Body reads can be deliberately slow.  Re-read the immutable policy
        # generation after consuming the body so a disable/reload that arrived
        # during that interval cannot forward under the stale enabled snapshot.
        try:
            current = self.manager.snapshot()
        except ConfigError as exc:
            request["gateway_outcome"] = "configuration_rejected"
            return self._error_response(exc, 503)
        if not current.gateway_enabled:
            request["gateway_outcome"] = "gateway_disabled"
            status, body, headers = generated_error(
                "gateway_disabled", "The model gateway is disabled.", status=503
            )
            return web.Response(status=status, body=body, headers=headers)
        if current.revision != snapshot.revision:
            request["gateway_outcome"] = "configuration_changed"
            status, body, headers = generated_error(
                "configuration_changed", "The gateway policy changed while the request was being read.", status=503
            )
            return web.Response(status=status, body=body, headers=headers)

        await self.start()
        assert self._session is not None
        upstream_authority = urlsplit(snapshot.upstream).netloc
        headers = forwarded_header_items(
            request.headers,
            transformed=transformed,
            upstream_host=upstream_authority,
        )
        if transformed:
            # Compaction is the sole body rewrite permitted by the tunnel
            # contract. Preserve the Content-Length field itself while
            # correcting its representation value for the rewritten bytes;
            # leaving the caller's length would make aiohttp reject the
            # request or truncate it upstream.
            headers = [
                (key, str(len(outgoing)) if key.lower() == "content-length" else value)
                for key, value in headers
            ]
        upstream_task: asyncio.Task | None = None
        disconnect_task: asyncio.Task | None = asyncio.create_task(self._wait_client_disconnect(request))
        try:
            try:
                # ``start`` may yield while a reload signal is handled.  Keep
                # the final network handoff behind the same generation check.
                try:
                    current = self.manager.snapshot()
                except ConfigError as exc:
                    request["gateway_outcome"] = "configuration_rejected"
                    return self._error_response(exc, 503)
                if not current.gateway_enabled:
                    request["gateway_outcome"] = "gateway_disabled"
                    status, body, response_headers_ = generated_error(
                        "gateway_disabled", "The model gateway is disabled.", status=503
                    )
                    return web.Response(status=status, body=body, headers=response_headers_)
                if current.revision != snapshot.revision:
                    request["gateway_outcome"] = "configuration_changed"
                    status, body, response_headers_ = generated_error(
                        "configuration_changed", "The gateway policy changed while the request was being read.", status=503
                    )
                    return web.Response(status=status, body=body, headers=response_headers_)
                upstream_task = asyncio.create_task(self._session.request(
                    request.method,
                    upstream_target,
                    headers=headers,
                    data=outgoing,
                    allow_redirects=False,
                ))
                done, _ = await asyncio.wait({upstream_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED)
                if disconnect_task in done and upstream_task not in done:
                    request["gateway_outcome"] = "client_disconnected_before_headers"
                    await self._cancel_upstream(upstream_task)
                    raise ConnectionResetError("client disconnected before upstream headers")
                upstream = await upstream_task
            except asyncio.CancelledError:
                request["gateway_outcome"] = "client_disconnected_before_headers"
                await self._cancel_upstream(upstream_task)
                raise
            except ConnectionResetError:
                request["gateway_outcome"] = "client_disconnected_before_headers"
                raise
            except ConnectionError:
                request["gateway_outcome"] = "upstream_unavailable"
                status, body, response_headers_ = generated_error("upstream_unavailable", "The configured upstream is unavailable.", status=502)
                return web.Response(status=status, body=body, headers=response_headers_)
            except Exception:
                request["gateway_outcome"] = "upstream_unavailable"
                status, body, response_headers_ = generated_error("upstream_unavailable", "The configured upstream is unavailable.", status=502)
                return web.Response(status=status, body=body, headers=response_headers_)
            async with upstream:
                response = web.StreamResponse(status=upstream.status)
                for key, value in response_header_items(upstream.headers):
                    response.headers.add(key, value)
                try:
                    await response.prepare(request)
                except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                    request["gateway_outcome"] = "client_disconnected_before_headers"
                    upstream.close()
                    raise
                try:
                    iterator = upstream.content.iter_chunked(64 * 1024).__aiter__()
                    while True:
                        read_task = asyncio.create_task(iterator.__anext__())
                        done, _ = await asyncio.wait({read_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED)
                        if disconnect_task in done:
                            if read_task not in done:
                                read_task.cancel()
                            with suppress(BaseException):
                                await read_task
                            raise ConnectionResetError("client disconnected during stream")
                        try:
                            chunk = await read_task
                        except StopAsyncIteration:
                            break
                        write_task = asyncio.create_task(response.write(chunk))
                        done, _ = await asyncio.wait({write_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED)
                        if disconnect_task in done:
                            if write_task not in done:
                                write_task.cancel()
                            with suppress(BaseException):
                                await write_task
                            raise ConnectionResetError("client disconnected during stream")
                        await write_task
                except asyncio.CancelledError:
                    request["gateway_outcome"] = "client_disconnected_during_stream"
                    upstream.close()
                    raise
                except (ConnectionResetError, BrokenPipeError):
                    request["gateway_outcome"] = "client_disconnected_during_stream"
                    upstream.close()
                    return response
                finally:
                    try:
                        await response.write_eof()
                    except (ConnectionResetError, BrokenPipeError, RuntimeError):
                        pass
                request["gateway_outcome"] = "legacy_passthrough" if legacy_compaction and not transformed else "completed"
                return response
        finally:
            if upstream_task is not None and not upstream_task.done():
                await self._cancel_upstream(upstream_task)
            if disconnect_task is not None and not disconnect_task.done():
                disconnect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await disconnect_task

    async def _proxy_websocket(self, request: web.Request, snapshot: PolicySnapshot, target: str) -> web.StreamResponse:
        await self.start()
        assert self._session is not None
        # Each leg has its own RFC 6455 handshake. Authentication and semantic
        # headers pass through; keys, extensions and framing belong to the leg.
        handshake = {"host", "connection", "upgrade", "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol", "content-length"}
        headers = [(key, value) for key, value in request.headers.items() if key.lower() not in handshake]
        protocols = tuple(part.strip() for part in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if part.strip())
        try:
            upstream = await self._session.ws_connect(
                target, headers=headers, protocols=protocols, compress=0,
                autoping=False, autoclose=False, max_msg_size=32 * 1024 * 1024,
            )
        except WSServerHandshakeError as exc:
            request["gateway_outcome"] = "websocket_handshake_rejected"
            status, body, response_headers_ = generated_error("websocket_handshake_rejected", "The upstream rejected the WebSocket handshake.", status=exc.status)
            return web.Response(status=status, body=body, headers=response_headers_)
        except Exception:
            request["gateway_outcome"] = "upstream_unavailable"
            return web.Response(status=502)

        downstream = web.WebSocketResponse(
            protocols=(upstream.protocol,) if upstream.protocol else (),
            compress=False, autoping=False, autoclose=False, max_msg_size=32 * 1024 * 1024,
        )
        for key, value in upstream._response.headers.items():
            if key.lower() not in handshake | {"sec-websocket-accept", "content-encoding", "transfer-encoding"}:
                downstream.headers.add(key, value)
        pumps: list[asyncio.Task] = []
        try:
            if not self.manager.snapshot().gateway_enabled:
                return web.Response(status=503)
            await downstream.prepare(request)

            async def relay(source, destination, *, client: bool) -> None:
                while True:
                    message = await source.receive()
                    if message.type == WSMsgType.TEXT:
                        outgoing = message.data
                        if client:
                            policy = self.manager.snapshot()
                            if not policy.gateway_enabled:
                                await downstream.close(code=1012, message=b"Gateway disabled")
                                return
                            metadata = {}
                            kind = "websocket_ordinary"
                            if policy.router_enabled and policy.compaction_enabled:
                                rewritten = rewrite_ws_compaction(outgoing.encode("utf-8"), policy)
                                if rewritten is not None:
                                    outgoing = rewritten[0].decode("utf-8")
                                    metadata = rewritten[1]
                                    kind = "websocket_compaction"
                            if not metadata:
                                try:
                                    body = json.loads(outgoing)
                                    if isinstance(body, dict):
                                        reasoning = body.get("reasoning")
                                        effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
                                        metadata = {"original_model": body.get("model"), "routed_model": body.get("model"), "original_reasoning_effort": effort, "routed_reasoning_effort": effort}
                                except (ValueError, RecursionError):
                                    pass
                            await destination.send_str(outgoing)
                            log_outcome(LOGGER, request_id=request_id(), request_kind=kind,
                                        config_revision=policy.revision, http_status=101,
                                        transport_outcome="forwarded", duration_ms=0, **metadata)
                        else:
                            await destination.send_str(outgoing)
                    elif message.type == WSMsgType.BINARY:
                        if client and not self.manager.snapshot().gateway_enabled:
                            await downstream.close(code=1012)
                            return
                        await destination.send_bytes(message.data)
                    elif message.type == WSMsgType.PING:
                        await destination.ping(message.data)
                    elif message.type == WSMsgType.PONG:
                        await destination.pong(message.data)
                    elif message.type == WSMsgType.CLOSE:
                        await destination.close(code=message.data or 1000, message=(message.extra or "").encode())
                        return
                    elif message.type in {WSMsgType.CLOSED, WSMsgType.CLOSING}:
                        return
                    elif message.type == WSMsgType.ERROR:
                        raise ConnectionError("WebSocket transport failed")

            pumps = [asyncio.create_task(relay(downstream, upstream, client=True)),
                     asyncio.create_task(relay(upstream, downstream, client=False))]
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for pump in done:
                pump.result()
            request["gateway_outcome"] = "websocket_closed"
        except (TransformError, ConfigError):
            request["gateway_outcome"] = "websocket_policy_rejected"
            await downstream.close(code=1008, message=b"Gateway policy rejected the request")
        except asyncio.CancelledError:
            request["gateway_outcome"] = "client_disconnected"
            raise
        except Exception:
            request["gateway_outcome"] = "websocket_transport_error"
            if downstream.prepared:
                await downstream.close(code=1011, message=b"Gateway transport failed")
        finally:
            for pump in pumps:
                if not pump.done():
                    pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            await upstream.close()
            if downstream.prepared:
                await downstream.close()
        return downstream


GATEWAY_PROXY_KEY = web.AppKey("gateway_proxy", GatewayProxy)


def create_app(manager: ConfigManager, *, identity: dict[str, object] | None = None) -> web.Application:
    proxy = GatewayProxy(manager, identity=identity)
    @web.middleware
    async def bounded_body_middleware(request: web.Request, handler):
        try:
            return await handler(request)
        except web.HTTPRequestEntityTooLarge:
            status, body, headers = generated_error("request_body_too_large", "The request body exceeds the compressed size limit.", status=413)
            return web.Response(status=status, body=body, headers=headers)

    app = web.Application(
        client_max_size=MAX_COMPRESSED_BODY_BYTES,
        middlewares=[bounded_body_middleware],
        handler_args={"auto_decompress": False, "handler_cancellation": True},
    )
    app[GATEWAY_PROXY_KEY] = proxy
    app.router.add_route("*", "/health", proxy.handle)
    app.router.add_route("*", "/{tail:.*}", proxy.handle)

    async def startup(application: web.Application) -> None:
        await proxy.start()

    async def cleanup(application: web.Application) -> None:
        await proxy.begin_drain()
        await proxy.close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app
