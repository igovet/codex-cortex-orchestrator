"""Automatic local-provider lifecycle and real HTTP/WebSocket transport."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import tomllib

import pytest
from aiohttp import ClientSession, WSServerHandshakeError, WSMsgType, web

from plugins.cortex.scripts.cortex_runtime.provider import ProviderSettings, apply_provider_patch, restore_provider, managed_plugin_disabled
import plugins.cortex.scripts.cortex_runtime.provider as provider_module
from plugins.cortex.scripts.cortex_runtime.runtime.supervisor import GatewaySupervisor
from plugins.cortex.scripts.cortex_runtime.gateway.config import PolicySnapshot
from plugins.cortex.scripts.cortex_runtime.gateway.proxy import create_app


def settings(port=8799):
    return ProviderSettings(gateway_port=port, requires_openai_auth=True,
                            gateway_verified=True, supports_websockets=True,
                            remote_compaction_v2=None)


def config_file(home: Path, text: str) -> Path:
    home.chmod(0o700)
    path = home / "config.toml"
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_automatic_provider_is_idempotent_preserves_models_and_restores_edits(tmp_path):
    original = '# user choice\nmodel = "gpt-5.6-luna"\nmodel_reasoning_effort = "high"\n[features]\nremote_compaction_v2 = true\n'
    path = config_file(tmp_path, original)
    assert apply_provider_patch(tmp_path, settings(), managed=True)["status"] == "updated"
    data = tomllib.loads(path.read_text())
    assert data["model_provider"] == "cortex"
    assert data["features"]["remote_compaction_v2"] is True
    assert data["model_providers"]["cortex"]["base_url"] == "http://127.0.0.1:8799/backend-api/codex"
    assert data["model_providers"]["cortex"]["requires_openai_auth"] is True
    assert apply_provider_patch(tmp_path, settings(), managed=True)["status"] == "unchanged"
    assert apply_provider_patch(tmp_path, settings(8800), managed=True)["status"] == "updated"
    path.write_text(path.read_text().replace('model_reasoning_effort = "high"', 'model_reasoning_effort = "medium"') + '\n[projects."/example"]\ntrust_level = "trusted"\n')
    assert restore_provider(tmp_path)["status"] == "restored"
    restored = tomllib.loads(path.read_text())
    assert "model_provider" not in restored
    assert "cortex" not in restored.get("model_providers", {})
    assert restored["model_reasoning_effort"] == "medium"
    assert restored["projects"]["/example"]["trust_level"] == "trusted"
    assert (tmp_path / "config.toml.cortex-backup").read_text() == original
    assert restore_provider(tmp_path)["status"] == "unmanaged"
    # A subsequent activation remembers the current selection again.
    assert apply_provider_patch(tmp_path, settings(), managed=True)["status"] == "updated"


def test_automatic_provider_honors_later_user_override(tmp_path):
    path = config_file(tmp_path, 'model_provider = "openai"\n')
    apply_provider_patch(tmp_path, settings(), managed=True)
    path.write_text(path.read_text().replace('model_provider = "cortex"', 'model_provider = "custom"'))
    before = path.read_bytes()
    assert apply_provider_patch(tmp_path, settings(), managed=True)["status"] == "user_override"
    assert path.read_bytes() == before
    restore_provider(tmp_path)
    assert tomllib.loads(path.read_text())["model_provider"] == "custom"


@pytest.mark.parametrize("original", ['model_provider = "other"\n', '[model_providers.cortex]\nname = "user"\n', 'openai_base_url = "https://other.invalid"\n'])
def test_automatic_provider_never_replaces_custom_route(tmp_path, original):
    path = config_file(tmp_path, original)
    with pytest.raises(ValueError):
        apply_provider_patch(tmp_path, settings(), managed=True)
    assert path.read_text() == original
    assert not (tmp_path / "config.toml.cortex-route.json").exists()


def test_provider_validates_exact_listener_and_auth_receipt():
    with pytest.raises(ValueError, match="verified owned"):
        replace(settings(), gateway_verified=False).resolved_base_url()
    with pytest.raises(ValueError, match="verified loopback"):
        replace(settings(), base_url="https://chatgpt.com/backend-api/codex").resolved_base_url()
    assert replace(settings(), gateway_host="::1").resolved_base_url() == "http://[::1]:8799/backend-api/codex"


def test_provider_uses_macos_atomic_rename_swap(monkeypatch, tmp_path):
    calls = []

    class RenameSwap:
        def __call__(self, first, second, flags):
            calls.append((first, second, flags))
            return 0

    class LibC:
        renamex_np = RenameSwap()

    monkeypatch.setattr(provider_module.sys, "platform", "darwin")
    monkeypatch.setattr(provider_module.ctypes, "CDLL", lambda *_args, **_kwargs: LibC())
    provider_module._rename_exchange(tmp_path / "candidate", tmp_path / "config.toml")
    assert calls == [(
        os.fsencode(tmp_path / "candidate"),
        os.fsencode(tmp_path / "config.toml"),
        0x00000002,
    )]


def test_connect_never_selects_an_unready_gateway(tmp_path, monkeypatch):
    path = config_file(tmp_path, 'model_provider = "openai"\n')
    supervisor = GatewaySupervisor(codex_home=tmp_path)
    monkeypatch.setattr(supervisor, "ensure", lambda: {"status": "not_ready"})
    monkeypatch.setattr(supervisor.manager, "snapshot", lambda: policy(8787))
    with pytest.raises(RuntimeError, match="ready owned"):
        supervisor.connect()
    assert path.read_text() == 'model_provider = "openai"\n'


def test_disable_detects_only_the_recorded_marketplace_entry(tmp_path):
    path = config_file(tmp_path, '[plugins."cortex@cortex"]\nenabled = true\n')
    apply_provider_patch(tmp_path, settings(), managed=True)
    assert not managed_plugin_disabled(tmp_path)
    path.write_text(path.read_text().replace('enabled = true', 'enabled = false'))
    assert managed_plugin_disabled(tmp_path)
    restore_provider(tmp_path)
    assert tomllib.loads(path.read_text())["plugins"]["cortex@cortex"]["enabled"] is False


def test_interrupted_route_publication_can_resume(tmp_path, monkeypatch):
    path = config_file(tmp_path, 'model = "gpt-5.6-luna"')
    publish = provider_module._publish_config_cas
    def fail_config(target, temporary, observed):
        if target == path:
            raise OSError("simulated crash before config publication")
        return publish(target, temporary, observed)
    monkeypatch.setattr(provider_module, "_publish_config_cas", fail_config)
    with pytest.raises(OSError):
        apply_provider_patch(tmp_path, settings(), managed=True)
    assert path.read_text() == 'model = "gpt-5.6-luna"'
    monkeypatch.setattr(provider_module, "_publish_config_cas", publish)
    assert apply_provider_patch(tmp_path, settings(), managed=True)["status"] == "updated"
    assert tomllib.loads(path.read_text())["model"] == "gpt-5.6-luna"


def test_restore_rejects_a_concurrent_config_edit(tmp_path, monkeypatch):
    path = config_file(tmp_path, 'model = "gpt-5.6-luna"\n')
    apply_provider_patch(tmp_path, settings(), managed=True)
    publish = provider_module._publish_config_cas
    def competing_edit(target, temporary, observed):
        if target == path:
            path.write_bytes(path.read_bytes() + '\n[user]\nnote = "keep me"\n'.encode())
        return publish(target, temporary, observed)
    monkeypatch.setattr(provider_module, "_publish_config_cas", competing_edit)
    with pytest.raises(ValueError, match="changed during provider update"):
        restore_provider(tmp_path)
    assert tomllib.loads(path.read_text())["user"]["note"] == "keep me"
    assert (tmp_path / "config.toml.cortex-route.json").exists()


def policy(port):
    return PolicySnapshot(1, "now", True, True, "gpt-5.6-luna", "medium", True,
                          ("127.0.0.1", 8787), f"http://127.0.0.1:{port}/backend-api/codex")


@asynccontextmanager
async def serve(app):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield site._server.sockets[0].getsockname()[1]
    finally:
        await runner.cleanup()


def test_direct_websocket_preserves_auth_text_binary_and_compaction(caplog):
    async def scenario():
        seen = []
        handshakes = []

        async def endpoint(request):
            handshakes.append((request.raw_path, list(request.headers.items())))
            ws = web.WebSocketResponse(autoping=False)
            await ws.prepare(request)
            async for message in ws:
                seen.append(message.data)
                if message.type == WSMsgType.TEXT:
                    await ws.send_str(message.data)
                elif message.type == WSMsgType.BINARY:
                    await ws.send_bytes(message.data)
                elif message.type == WSMsgType.PING:
                    await ws.pong(message.data)
            return ws

        app = web.Application()
        app.router.add_get("/backend-api/codex/responses", endpoint)
        async with serve(app) as port:
            class Manager:
                def snapshot(self):
                    return policy(port)
            async with serve(create_app(Manager())) as proxy_port, ClientSession() as client:
                async with client.ws_connect(f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses?x=1",
                    headers=[("Authorization", "Bearer synthetic-secret"), ("ChatGPT-Account-Id", "synthetic-account"), ("X-Duplicate", "one"), ("X-Duplicate", "two")], autoping=False) as ws:
                    ordinary = '{ "type": "response.create", "model":"gpt-5.6-luna", "reasoning":{"effort":"high"}, "input":[] }'
                    await ws.send_str(ordinary)
                    assert (await ws.receive(timeout=3)).data == ordinary
                    compact = {"type":"response.create", "model":"gpt-5.6-luna", "reasoning":{"effort":"high","summary":"auto"}, "store":False, "stream":False, "input":[{"type":"compaction_trigger"}]}
                    await ws.send_json(compact)
                    routed = json.loads((await ws.receive(timeout=3)).data)
                    assert routed == {**compact, "reasoning":{"effort":"medium","summary":"auto"}}
                    await ws.send_bytes(b"opaque bytes")
                    assert (await ws.receive(timeout=3)).data == b"opaque bytes"
                    await ws.ping(b"alive")
                    assert (await ws.receive(timeout=3)).type == WSMsgType.PONG
                path, headers = handshakes[0]
                assert path.endswith("?x=1")
                assert ("Authorization", "Bearer synthetic-secret") in headers
                assert [value for key, value in headers if key == "X-Duplicate"] == ["one", "two"]
                assert not any(key.lower() == "sec-websocket-extensions" for key, _ in headers)
                assert seen[0] == ordinary

    with caplog.at_level(logging.INFO, logger="cortex.gateway"):
        asyncio.run(scenario())
    assert "websocket_compaction" in caplog.text
    assert "synthetic-secret" not in caplog.text
    assert "synthetic-account" not in caplog.text


def test_websocket_redirect_never_replays_credentials():
    async def scenario():
        redirected = []
        async def sink(request):
            redirected.append(True)
            return web.Response()
        second = web.Application()
        second.router.add_get("/steal", sink)
        async with serve(second) as destination:
            async def endpoint(request):
                raise web.HTTPFound(f"http://127.0.0.1:{destination}/steal")
            app = web.Application()
            app.router.add_get("/backend-api/codex/responses", endpoint)
            async with serve(app) as port:
                class Manager:
                    def snapshot(self):
                        return policy(port)
                async with serve(create_app(Manager())) as proxy_port, ClientSession() as client:
                    with pytest.raises(WSServerHandshakeError):
                        await client.ws_connect(f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses", headers={"Authorization":"Bearer synthetic"})
        assert redirected == []
    asyncio.run(scenario())
