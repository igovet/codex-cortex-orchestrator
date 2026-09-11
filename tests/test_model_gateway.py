"""Focused tests for the pure Cortex Model Gateway contract."""

from __future__ import annotations

import json
from pathlib import Path
import asyncio
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import logging
import os
import subprocess
import sys
import tomllib

import pytest
from aiohttp import ClientSession, web
from multidict import CIMultiDict

from plugins.cortex.scripts.cortex_runtime.gateway.classifier import is_compaction_request
from plugins.cortex.scripts.cortex_runtime.gateway.compression import DecodeError, decode_content, decode_json, encode_content
from plugins.cortex.scripts.cortex_runtime.gateway.config import ConfigError, ConfigLoader, ConfigManager, write_default_config
from plugins.cortex.scripts.cortex_runtime.gateway.headers import forwarded_header_items, forwarded_headers
from plugins.cortex.scripts.cortex_runtime.gateway.transform import TransformError, transform_compaction
from plugins.cortex.scripts.cortex_runtime.gateway.config import PolicySnapshot
from plugins.cortex.scripts.cortex_runtime.gateway.proxy import create_app
from plugins.cortex.scripts.cortex_runtime.gateway.proxy import GATEWAY_PROXY_KEY, GatewayProxy, _valid_loopback_authority
from plugins.cortex.scripts.cortex_runtime.gateway.defaults import MAX_BODY_BYTES
from plugins.cortex.scripts.cortex_runtime.runtime.state import RuntimePaths
from plugins.cortex.scripts.cortex_runtime.runtime.state import GatewayState, dependency_identity, payload_digest as runtime_payload_digest, state_matches_process
from plugins.cortex.scripts.cortex_runtime.provider import ProviderSettings, apply_provider_patch, provider_patch
import plugins.cortex.scripts.cortex_runtime.provider as provider_module
from plugins.cortex.scripts.cortex_runtime.gateway.diagnostics import log_outcome, REQUIRED_OUTCOME_FIELDS
from plugins.cortex.scripts.cortex_runtime.gateway.mitm import (
    MitmProxy,
    MitmError,
    _WsFrame,
    _split_ws_payload,
    _without_ws_extensions,
    _ws_decode_wire,
    _ws_encode,
    rewrite_ws_compaction,
)
from plugins.cortex.scripts.cortex_runtime.runtime.supervisor import GatewaySupervisor
import plugins.cortex.scripts.cortex_runtime.runtime.supervisor as supervisor_module
import plugins.cortex.scripts.cortex_runtime.marketplace_bootstrap as marketplace_bootstrap
from plugins.cortex.scripts.cortex_runtime.runtime.process import _gateway_environment
from plugins.cortex.scripts import cortex_runtime_ctl as runtime_ctl_module
from plugins.cortex.scripts.cortex_runtime_ctl import main as runtime_ctl_main
from cortex_package import PLUGIN, dependency_lock_entries, payload_digest as package_payload_digest, private_dir


def test_classifier_is_strict_and_pure() -> None:
    assert is_compaction_request({"input": [{"type": "compaction_trigger"}]})
    assert not is_compaction_request({"input": [{"type": "message", "text": "compaction_trigger"}]})
    assert not is_compaction_request({"input": [{"nested": {"type": "compaction_trigger"}}]})
    assert not is_compaction_request({"input": [{"type": "context_management"}]})


def _masked_ws_text(payload: bytes, *, fin: bool = True, opcode: int = 1, mask: bytes = b"abcd") -> bytes:
    first = (0x80 if fin else 0) | opcode
    encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    length = len(payload)
    if length < 126:
        return bytes((first, 0x80 | length)) + mask + encoded
    if length <= 0xFFFF:
        return bytes((first, 0x80 | 126)) + length.to_bytes(2, "big") + mask + encoded
    return bytes((first, 0x80 | 127)) + length.to_bytes(8, "big") + mask + encoded


def test_ws_rewrite_changes_only_model_and_effort() -> None:
    class Policy:
        model = "gpt-5.6-luna"
        effort = "medium"

    body = {
        "type": "response.create",
        "model": "gpt-5.6-sol",
        "reasoning": {"effort": "high", "summary": "auto", "extra": {"x": 1}},
        "store": True,
        "stream": False,
        "input": [{"role": "user", "content": "x"}, {"type": "compaction_trigger"}],
        "metadata": {"keep": True},
    }
    result = rewrite_ws_compaction(json.dumps(body, separators=(",", ":")).encode(), Policy())
    assert result is not None
    encoded, fields = result
    changed = json.loads(encoded)
    assert changed["model"] == "gpt-5.6-luna"
    assert changed["reasoning"] == {"effort": "medium", "summary": "auto", "extra": {"x": 1}}
    assert changed["store"] is True and changed["stream"] is False
    assert changed["input"] == body["input"] and changed["metadata"] == body["metadata"]
    assert fields["original_model"] == "gpt-5.6-sol"
    assert fields["routed_model"] == "gpt-5.6-luna"
    assert fields["original_reasoning_effort"] == "high"
    assert fields["routed_reasoning_effort"] == "medium"
    assert fields["original_store"] == "bool" and fields["routed_store"] == "bool"


def test_ws_auto_compaction_matches_codex_remote_v2_request_shape() -> None:
    """Exercise the request shape emitted by Codex's remote V2 compactor.

    The installed Codex binary identifies ``core/src/compact_remote_v2.rs`` and
    ``codex-api/src/endpoint/responses_websocket.rs`` and exposes the
    ``ResponsesWsRequestResponseCreate`` fields ``type``, ``input``,
    ``reasoning``, ``store`` and ``stream``.  The exact Rust threshold branch is
    not shipped as source, so this fixture deliberately proves the stable wire
    contract rather than claiming branch-level provenance.
    """
    class Policy:
        model = "gpt-5.6-luna"
        effort = "medium"

    codex_auto_request = {
        "type": "response.create",
        "model": "gpt-5.6-sol",
        "instructions": "retain the current task",
        "input": [{"type": "compaction_trigger"}],
        "reasoning": {"effort": "high", "summary": "auto"},
        "store": False,
        "stream": True,
        "parallel_tool_calls": False,
        "previous_response_id": "resp_fixture",
    }
    rewritten = rewrite_ws_compaction(
        json.dumps(codex_auto_request, separators=(",", ":")).encode(), Policy()
    )
    assert rewritten is not None
    encoded, fields = rewritten
    routed = json.loads(encoded)
    assert routed["type"] == "response.create"
    assert routed["input"] == [{"type": "compaction_trigger"}]
    assert routed["model"] == "gpt-5.6-luna"
    assert routed["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert routed["store"] is False and routed["stream"] is True
    assert routed["parallel_tool_calls"] is False
    assert routed["previous_response_id"] == "resp_fixture"
    assert fields["original_store"] == "bool" and fields["routed_store"] == "bool"

    # A user-visible compaction label or an ordinary response.create is not a
    # trigger.  This is the safe distinction available at the gateway boundary.
    assert rewrite_ws_compaction(
        json.dumps({"type": "response.create", "input": [{"type": "context_compaction"}]}).encode(), Policy()
    ) is None


def test_ws_rewrite_rejects_ordinary_and_preserves_wire_frame() -> None:
    class Policy:
        model = "gpt-5.6-luna"
        effort = "medium"

    ordinary = b'{"type":"response.create","model":"gpt-5.6-sol","input":[{"type":"message"}]}'
    frame = _ws_decode_wire(_masked_ws_text(ordinary), expect_mask=True)
    assert rewrite_ws_compaction(frame.payload, Policy()) is None
    assert _ws_encode(frame, frame.payload) == frame.raw
    with pytest.raises(MitmError):
        _ws_decode_wire(_masked_ws_text(ordinary) + b"x", expect_mask=True)


def test_ws_fragment_split_retains_frame_count_and_masking() -> None:
    first = _ws_decode_wire(_masked_ws_text(b"{\"type\":\"response.create\",", fin=False), expect_mask=True)
    second = _ws_decode_wire(_masked_ws_text(b"\"input\":[{\"type\":\"compaction_trigger\"}]}", fin=True, opcode=0), expect_mask=True)
    replacement = b"x" * (len(first.payload) + len(second.payload) + 7)
    chunks = _split_ws_payload(replacement, [first, second])
    rebuilt = [_ws_encode(frame, chunk) for frame, chunk in zip((first, second), chunks)]
    decoded = [_ws_decode_wire(value, expect_mask=True) for value in rebuilt]
    assert len(decoded) == 2 and decoded[0].opcode == 1 and decoded[1].opcode == 0
    assert decoded[0].fin is False and decoded[1].fin is True
    assert b"".join(value.payload for value in decoded) == replacement


def test_ws_extension_offer_is_the_only_handshake_header_removed() -> None:
    headers = [b"Origin: https://caller.invalid\r\n", b"Cookie: a=b\r\n", b"Sec-WebSocket-Extensions: permessage-deflate\r\n", b"Cookie: c=d\r\n"]
    assert _without_ws_extensions(headers) == [headers[0], headers[1], headers[3]]


def test_ws_copy_rewrites_compaction_and_logs_only_bounded_fields(caplog: pytest.LogCaptureFixture) -> None:
    class Policy:
        model = "gpt-5.6-luna"
        effort = "medium"

    class Manager:
        def snapshot(self):
            return Policy()

    class Reader:
        def __init__(self, value: bytes):
            self.value = value

        async def readexactly(self, count: int) -> bytes:
            if len(self.value) < count:
                raise asyncio.IncompleteReadError(self.value, count)
            value, self.value = self.value[:count], self.value[count:]
            return value

    class Writer:
        def __init__(self):
            self.values: list[bytes] = []

        def write(self, value: bytes) -> None:
            self.values.append(value)

        async def drain(self) -> None:
            return None

    body = json.dumps({
        "type": "response.create", "model": "gpt-5.6-sol",
        "reasoning": {"effort": "high"}, "store": True, "stream": False,
        "input": [{"type": "compaction_trigger"}], "secret": "never-log",
    }, separators=(",", ":")).encode()
    reader = Reader(_masked_ws_text(body))
    writer = Writer()
    proxy = MitmProxy.__new__(MitmProxy)
    proxy.manager = Manager()
    with caplog.at_level(logging.INFO, logger="cortex.gateway.mitm"), pytest.raises(asyncio.IncompleteReadError):
        asyncio.run(proxy._copy_websocket_client(reader, writer, "/backend-api/codex/responses"))
    output = _ws_decode_wire(b"".join(writer.values), expect_mask=True)
    changed = json.loads(output.payload)
    assert changed["model"] == "gpt-5.6-luna"
    assert changed["reasoning"]["effort"] == "medium"
    assert changed["store"] is True and changed["stream"] is False
    assert "never-log" not in caplog.text
    assert "original_model=gpt-5.6-sol" in caplog.text
    assert "routed_reasoning_effort=medium" in caplog.text
    assert "original_store=bool routed_store=bool" in caplog.text


def test_transform_changes_only_model_and_effort() -> None:
    class Policy:
        model = "gpt-5.6-luna"
        effort = "medium"

    body = {
        "model": "gpt-5.6-sol",
        "reasoning": {"effort": "high", "summary": "auto", "extra": {"x": 1}},
        "input": [{"role": "user", "content": "x"}, {"type": "compaction_trigger"}],
        "stream": True,
        "metadata": {"keep": True},
    }
    changed = transform_compaction(body, Policy())
    assert changed["model"] == "gpt-5.6-luna"
    assert changed["reasoning"] == {"effort": "medium", "summary": "auto", "extra": {"x": 1}}
    changed["metadata"]["keep"] = False
    assert body["metadata"]["keep"] is True


@pytest.mark.parametrize("reasoning", ["high", 3, [], True])
def test_transform_rejects_invalid_reasoning(reasoning: object) -> None:
    class Policy:
        model = "gpt-5.6-luna"
        effort = "medium"

    with pytest.raises(TransformError) as exc:
        transform_compaction({"reasoning": reasoning, "input": [{"type": "compaction_trigger"}]}, Policy())
    assert exc.value.code == "invalid_compaction_reasoning"


def test_transform_defaults_or_preserves_reasoning_fields() -> None:
    class Policy:
        model = "gpt-5.6-terra"
        effort = "high"

    item = {"type": "compaction_trigger"}
    assert transform_compaction({"input": [item]}, Policy())["reasoning"] == {"effort": "high"}
    assert transform_compaction({"reasoning": None, "input": [item]}, Policy())["reasoning"] == {"effort": "high"}


@pytest.mark.parametrize("store", [None, True, "true", 1])
def test_transform_compaction_forces_store_false(store: object) -> None:
    class Policy:
        model = "gpt-5.6-luna"
        effort = "medium"

    body = {"input": [{"type": "compaction_trigger"}]}
    if store is not None:
        body["store"] = store
    assert transform_compaction(body, Policy())["store"] is False


def test_decode_rejects_duplicates_and_unknown_encoding() -> None:
    with pytest.raises(DecodeError) as duplicate:
        decode_json(b'{"model":"a","model":"b"}')
    assert duplicate.value.code == "duplicate_json_key"
    with pytest.raises(DecodeError) as encoding:
        decode_json(b"{}", "br")
    assert encoding.value.code == "unsupported_content_encoding"


def test_gzip_expansion_is_rejected_incrementally() -> None:
    compressed = gzip.compress(b"x" * (MAX_BODY_BYTES + 1))
    with pytest.raises(DecodeError) as exc:
        decode_json(compressed, "gzip")
    assert exc.value.code == "request_body_too_large"


def test_rewritten_body_keeps_the_original_content_encoding() -> None:
    plain = b'{"model":"gpt-5.6-luna","input":[]}'
    for encoding in ("identity", "gzip", "zstd"):
        if encoding == "zstd":
            pytest.importorskip("zstandard")
        encoded = encode_content(plain, encoding)
        assert decode_content(encoded, encoding) == plain


def test_zstd_frames_are_bounded_and_exact() -> None:
    zstandard = pytest.importorskip("zstandard")
    compressor = zstandard.ZstdCompressor()
    frame = compressor.compress(b'{"ok":true}')
    assert decode_content(frame, "zstd") == b'{"ok":true}'
    with pytest.raises(DecodeError) as trailing:
        decode_content(frame + b"trailing", "zstd")
    assert trailing.value.code == "invalid_content_encoding"
    with pytest.raises(DecodeError) as concatenated:
        decode_content(frame + compressor.compress(b"second"), "zstd")
    assert concatenated.value.code == "invalid_content_encoding"
    with pytest.raises(DecodeError) as malformed:
        decode_content(b"not-zstd", "zstd")
    assert malformed.value.code == "invalid_content_encoding"

    unknown = zstandard.ZstdCompressor(write_content_size=False).compress(b"x" * (MAX_BODY_BYTES + 1))
    with pytest.raises(DecodeError) as unknown_size:
        decode_content(unknown, "zstd")
    assert unknown_size.value.code == "request_body_too_large"

    parameters = zstandard.ZstdCompressionParameters.from_level(3, window_log=25)
    large_window = zstandard.ZstdCompressor(compression_params=parameters).compress(b"x" * (17 * 1024 * 1024))
    with pytest.raises(DecodeError) as window:
        decode_content(large_window, "zstd")
    assert window.value.code == "request_body_too_large"


def test_namespace_target_is_exact_and_cannot_traverse() -> None:
    class Request:
        def __init__(self, path: str, query: str = ""):
            self.raw_path = path + (("?" + query) if query else "")
            self.path = path
            self.query_string = query

    class Snapshot:
        upstream = "https://chatgpt.com/backend-api/codex"

    proxy = GatewayProxy.__new__(GatewayProxy)
    accepted = proxy._target(Request("/backend-api/codex/responses", "a=1"), Snapshot())
    assert accepted == "https://chatgpt.com/backend-api/codex/responses?a=1"
    for path in (
        "/backend-api/codexevil",
        "/backend-api/codex/../admin",
        "/backend-api/codex/./admin",
        "/backend-api/codex//admin",
        "/backend-api/codex/%2e%2e/admin",
        "/backend-api/codex\\admin",
        "//chatgpt.com/backend-api/codex/responses",
    ):
        with pytest.raises(ValueError):
            proxy._target(Request(path), Snapshot())


def test_config_defaults_are_independent_and_reload_atomically(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    path = write_default_config(home)
    text = path.read_text()
    text = text.replace('model = "gpt-5.6-luna"\n', "")
    path.write_text(text)
    manager = ConfigManager(ConfigLoader(home))
    first = manager.snapshot()
    assert first.model == "gpt-5.6-luna"
    assert first.effort == "medium"
    path.write_text(text.replace('reasoning_effort = "medium"', 'reasoning_effort = "high"'))
    second = manager.snapshot()
    assert second.revision == first.revision + 1
    assert second.model == first.model
    assert second.effort == "high"
    path.write_text(text.replace('reasoning_effort = "medium"', 'reasoning_effort = "invalid"'))
    stale = manager.snapshot()
    assert stale.revision == second.revision
    assert stale.model == second.model
    assert stale.effort == second.effort
    assert stale.config_status == "stale"


def test_configure_preserves_existing_valid_bytes_and_binding_changes_are_pending(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    path = write_default_config(home)
    original = path.read_text()
    path.write_text("# user comment\n" + original.replace('port = 8787', 'port = 8788'))
    assert write_default_config(home).read_text() == "# user comment\n" + original.replace('port = 8787', 'port = 8788')
    manager = ConfigManager(ConfigLoader(home))
    first = manager.snapshot()
    path.write_text(path.read_text().replace('port = 8788', 'port = 8789'))
    changed = manager.snapshot()
    assert changed.restart_required is True
    assert changed.port == first.port
    assert changed.pending_listener == ("127.0.0.1", 8789)


def test_config_snapshot_observation_does_not_cache_enabled_policy_under_new_disabled_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "codex"
    path = write_default_config(home)
    enabled_read = ConfigLoader(home).read
    disabled = path.read_text().replace("enabled = true", "enabled = false", 1)
    replaced = False

    def interleaved_read():
        nonlocal replaced
        data, fingerprint = enabled_read()
        if not replaced:
            replaced = True
            path.write_text(disabled)
        return data, fingerprint

    loader = ConfigLoader(home)
    monkeypatch.setattr(loader, "read", interleaved_read)
    manager = ConfigManager(loader)
    first = manager.snapshot()
    assert first.gateway_enabled is True
    second = manager.snapshot()
    assert second.gateway_enabled is False


@pytest.mark.parametrize("unsafe_kind", ["directory", "broken_symlink"])
def test_existing_unsafe_gateway_config_path_fails_closed(tmp_path: Path, unsafe_kind: str) -> None:
    home = tmp_path / "codex"
    write_default_config(home)
    path = home / "cortex" / "config.toml"
    path.unlink()
    if unsafe_kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(home / "cortex" / "missing-config.toml")
    with pytest.raises(ConfigError, match="unsafe"):
        ConfigLoader(home).is_configured()


def test_gateway_config_rejects_group_or_world_permissions(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    path = write_default_config(home)
    path.chmod(0o666)
    with pytest.raises(ConfigError, match="unsafe"):
        ConfigManager(ConfigLoader(home)).snapshot()


def test_gateway_control_directory_rejects_group_or_world_permissions(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    path = write_default_config(home)
    (home / "cortex").chmod(0o777)
    with pytest.raises(ConfigError, match="unsafe"):
        ConfigManager(ConfigLoader(home)).snapshot()
    path.chmod(0o600)


def test_private_dir_tightens_existing_isolated_directory_mode(tmp_path: Path) -> None:
    target = tmp_path / "candidate-root"
    target.mkdir(mode=0o755)
    private_dir(target)
    assert target.stat().st_mode & 0o777 == 0o700


def test_gateway_environment_overrides_ambient_pythonpath(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORTEX_DEPENDENCY_DIR", str(tmp_path / "deps"))
    monkeypatch.setenv("PYTHONPATH", "/ambient/untrusted:/another/untrusted")
    monkeypatch.setenv("CORTEX_TEST_SECRET", "must-not-propagate")
    env = _gateway_environment(tmp_path / "home")
    assert env["PYTHONPATH"] == str(tmp_path / "deps")
    assert env["CORTEX_DEPENDENCY_DIR"] == str(tmp_path / "deps")
    assert "CORTEX_TEST_SECRET" not in env


def test_dev_launcher_does_not_append_ambient_pythonpath() -> None:
    launcher = Path("scripts/cortex-dev").read_text()
    assert 'export PYTHONPATH=""' in launcher
    assert 'export PYTHONPATH="${dependency_dir}"' in launcher
    assert 'PYTHONPATH="${script_dir}/../plugins/cortex/scripts:${dependency_dir}"' in launcher
    assert '${PYTHONPATH:+:${PYTHONPATH}}' not in launcher


def test_runtime_paths_tighten_existing_directory_modes(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    (home / "cortex" / "runtime").mkdir(parents=True, mode=0o755)
    (home / "cortex").chmod(0o755)
    home.chmod(0o755)
    RuntimePaths(home).ensure()
    assert home.stat().st_mode & 0o777 == 0o700
    assert (home / "cortex").stat().st_mode & 0o777 == 0o700
    assert (home / "cortex" / "runtime").stat().st_mode & 0o777 == 0o700


def test_default_config_bootstrap_is_create_only_under_concurrency(tmp_path: Path) -> None:
    home = tmp_path / "absent-home"
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _index: write_default_config(home), range(8)))
    assert {path.read_bytes() for path in paths} == {paths[0].read_bytes()}
    original = paths[0].read_bytes()
    paths[0].write_bytes(b"# retained\n" + original)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: write_default_config(home), range(8)))
    assert paths[0].read_bytes() == b"# retained\n" + original


def test_runtime_home_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        RuntimePaths(alias).ensure()


def test_runtime_state_rejects_symlink_and_hardlink_targets(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    paths = RuntimePaths(home)
    paths.ensure()
    outside = tmp_path / "outside"
    outside.write_text("sentinel")
    state = GatewayState.new(
        pid=1,
        host="127.0.0.1",
        port=8787,
        plugin_version="x",
        package_digest="x",
        upstream="x",
        policy_revision=1,
        executable="/no/such/entrypoint",
    )
    paths.state.symlink_to(outside)
    with pytest.raises(OSError):
        state.write(paths)
    paths.state.unlink()
    paths.pid.hardlink_to(outside)
    with pytest.raises(OSError):
        state.write(paths)


def test_runtime_payload_digest_matches_package_stamper() -> None:
    assert runtime_payload_digest(PLUGIN) == package_payload_digest(PLUGIN)


def test_runtime_identity_rejects_gateway_path_as_unused_python_argv_token(tmp_path: Path) -> None:
    entrypoint = (PLUGIN / "scripts" / "cortex_gateway.py").resolve()
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)", str(entrypoint)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        state = GatewayState.new(
            pid=process.pid,
            host="127.0.0.1",
            port=8787,
            plugin_version="x",
            package_digest="x",
            upstream="x",
            policy_revision=1,
            executable=str(entrypoint),
        )
        assert state_matches_process(state) is False
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_provider_patch_uses_configured_port_and_does_not_claim_unverified_gate() -> None:
    patch = provider_patch(ProviderSettings(gateway_host="127.0.0.1", gateway_port=8799))
    assert "http://127.0.0.1:8799/backend-api/codex" in patch
    assert "[features]" in patch
    assert patch.index("[features]") < patch.index("[model_providers.cortex]")
    assert "remote_compaction_v2 = false" in patch
    assert "[model_providers.cortex]\nremote_compaction_v2" not in patch
    with pytest.raises(ValueError):
        provider_patch(ProviderSettings(gateway_port=8799, remote_compaction_v2=True))


def test_provider_patch_applies_atomically_with_backup_and_preserves_comments(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("# preserve\nmodel = \"gpt-5.6-luna\"\n\n[features]\nother = true\n")
    config.chmod(0o600)
    result = apply_provider_patch(tmp_path, ProviderSettings(gateway_port=8799))
    updated = config.read_text()
    assert result["status"] == "updated"
    assert "# preserve" in updated and 'model = "gpt-5.6-luna"' in updated
    assert "[features]" in updated and "remote_compaction_v2 = false" in updated
    assert "[model_providers.cortex]" in updated
    assert (tmp_path / "config.toml.cortex-backup").read_text().startswith("# preserve")


def test_provider_backup_publish_is_create_only_when_another_writer_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model = "original"\n')
    config.chmod(0o600)
    winner = b"# concurrent recovery point\nmodel = \"winner\"\n"
    real_link = supervisor_module.os.link

    def competing_link(source, destination, *, follow_symlinks=True):
        Path(destination).write_bytes(winner)
        Path(destination).chmod(0o600)
        raise FileExistsError(destination)

    monkeypatch.setattr("plugins.cortex.scripts.cortex_runtime.provider.os.link", competing_link)
    result = apply_provider_patch(tmp_path, ProviderSettings(gateway_port=8799))
    assert result["status"] == "updated"
    assert (tmp_path / "config.toml.cortex-backup").read_bytes() == winner
    # Keep the real-link binding referenced so this test documents that only
    # the provider module's publication primitive is being raced.
    assert real_link is not None


def test_provider_patch_fails_closed_on_unrelated_concurrent_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model = "original"\n')
    config.chmod(0o600)
    initial = config.read_bytes()
    original_assert = provider_module._assert_config_unchanged
    edited = False

    def edit_before_cas(path: Path, expected):
        nonlocal edited
        if not edited:
            edited = True
            path.write_bytes(initial + b"concurrent_setting = true\n")
            path.chmod(0o600)
        return original_assert(path, expected)

    monkeypatch.setattr(provider_module, "_assert_config_unchanged", edit_before_cas)
    with pytest.raises(ValueError, match="changed during provider update"):
        apply_provider_patch(tmp_path, ProviderSettings(gateway_port=8799))
    assert config.read_bytes() == initial + b"concurrent_setting = true\n"
    assert (tmp_path / "config.toml.cortex-backup").read_bytes() == initial


def test_provider_patch_uses_effective_codex_home_when_omitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolated = tmp_path / "isolated"
    stable = tmp_path / "stable"
    stable.mkdir()
    stable_config = stable / "config.toml"
    stable_config.write_text('model_provider = "stable"\n')
    monkeypatch.setenv("CODEX_HOME", str(isolated))
    def connect(supervisor):
        return apply_provider_patch(supervisor.codex_home, ProviderSettings())
    monkeypatch.setattr(runtime_ctl_module.GatewaySupervisor, "connect", connect)
    assert runtime_ctl_main(["configure", "--provider"]) == 0
    assert (isolated / "config.toml").is_file()
    assert (isolated / "cortex" / "config.toml").is_file()
    assert stable_config.read_text() == 'model_provider = "stable"\n'


def test_provider_patch_preserves_inline_table_header_comments(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "old"\n\n'
        '[features] # keep feature header\n'
        'other = true\n\n'
        '[model_providers.cortex] # keep provider header\n'
        'name = "old"\n'
    )
    config.chmod(0o600)
    result = apply_provider_patch(tmp_path, ProviderSettings(gateway_port=8799))
    updated = config.read_text()
    assert result["status"] == "updated"
    assert updated.count("[features]") == 1
    assert updated.count("[model_providers.cortex]") == 1
    assert "[features] # keep feature header" in updated
    assert "[model_providers.cortex] # keep provider header" in updated
    assert "remote_compaction_v2" in tomllib.loads(updated)["features"]


@pytest.mark.parametrize(
    "header",
    [
        '["features"] # quoted feature header',
        '[model_providers."cortex"] # quoted provider header',
    ],
)
def test_provider_patch_handles_quoted_headers_and_array_table_boundaries(tmp_path: Path, header: str) -> None:
    config = tmp_path / "config.toml"
    if header.startswith("[model_providers"):
        config.write_text(
            'model_provider = "old"\n'
            f"{header}\n"
            'name = "old"\n'
            '[[plugins]]\n'
            'name = "unrelated"\n'
        )
    else:
        config.write_text(
            'model_provider = "old"\n'
            f"{header}\n"
            'other = true\n'
            '[[plugins]]\n'
            'name = "unrelated"\n'
        )
    config.chmod(0o600)
    apply_provider_patch(tmp_path, ProviderSettings(gateway_port=8799))
    updated = config.read_text()
    parsed = tomllib.loads(updated)
    assert parsed["features"]["remote_compaction_v2"] is False
    assert parsed["plugins"][0]["name"] == "unrelated"
    assert updated.count("remote_compaction_v2") == 1
    if header.startswith("[model_providers"):
        assert parsed["model_providers"]["cortex"]["name"] == "OpenAI"
        assert updated.count("model_providers.") == 1


def test_provider_patch_rejects_array_table_target_without_mutation(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = 'model_provider = "old"\n[[features]]\nother = true\n'
    config.write_text(original)
    config.chmod(0o600)
    with pytest.raises(ValueError, match="array table"):
        apply_provider_patch(tmp_path, ProviderSettings(gateway_port=8799))
    assert config.read_text() == original


def test_reload_restarts_for_pending_binding_changes(monkeypatch, tmp_path: Path) -> None:
    old = PolicySnapshot(1, "now", True, True, "gpt-5.6-luna", "medium", True, ("127.0.0.1", 8787), "https://chatgpt.com/backend-api/codex")
    pending = PolicySnapshot(2, "now", True, True, "gpt-5.6-luna", "medium", True, ("127.0.0.1", 8787), "https://chatgpt.com/backend-api/codex", restart_required=True, pending_listener=("127.0.0.1", 8788), pending_upstream="https://chatgpt.com/backend-api/codex")

    class Manager:
        def reload(self): return pending

        def snapshot(self): return pending

    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", manager=Manager())
    state = type("State", (), {"host": "127.0.0.1", "port": 8787, "upstream": old.upstream})()
    signals: list[int] = []
    reads = iter([state, None, None])
    monkeypatch.setattr(supervisor_module.GatewayState, "read", lambda _: next(reads, None))
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _: True)
    monkeypatch.setattr(supervisor_module, "signal_owned", lambda _, sig: signals.append(sig) or True)
    monkeypatch.setattr(supervisor, "ensure", lambda: {"status": "running", "restarted": True})
    assert supervisor.reload() == {"status": "running", "restarted": True}
    assert signals == [supervisor_module.signal.SIGTERM]


def test_reload_fails_closed_when_owned_child_cannot_be_signaled(monkeypatch, tmp_path: Path) -> None:
    pending = PolicySnapshot(2, "now", True, True, "gpt-5.6-luna", "medium", True,
                             ("127.0.0.1", 8787), "https://chatgpt.com/backend-api/codex",
                             restart_required=True, pending_listener=("127.0.0.1", 8788),
                             pending_upstream="https://chatgpt.com/backend-api/codex")

    class Manager:
        def reload(self): return pending

        def snapshot(self): return pending

    state = type("State", (), {"host": "127.0.0.1", "port": 8787, "upstream": pending.upstream,
                                "to_dict": lambda self: {"host": self.host, "port": self.port}})()
    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", manager=Manager())
    monkeypatch.setattr(supervisor_module.GatewayState, "read", lambda _paths: state)
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _state: True)
    monkeypatch.setattr(supervisor_module, "signal_owned", lambda *_args: False)
    result = supervisor.reload()
    assert result["reload_status"] == "failed"
    assert "signaled" in result["reload_error"]


def test_reload_fails_closed_when_owned_child_does_not_drain(monkeypatch, tmp_path: Path) -> None:
    pending = PolicySnapshot(2, "now", True, True, "gpt-5.6-luna", "medium", True,
                             ("127.0.0.1", 8787), "https://chatgpt.com/backend-api/codex",
                             restart_required=True, pending_listener=("127.0.0.1", 8788),
                             pending_upstream="https://chatgpt.com/backend-api/codex")

    class Manager:
        def reload(self): return pending

        def snapshot(self): return pending

    state = type("State", (), {"host": "127.0.0.1", "port": 8787, "upstream": pending.upstream,
                                "to_dict": lambda self: {"host": self.host, "port": self.port}})()
    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", manager=Manager())
    monkeypatch.setattr(supervisor_module.GatewayState, "read", lambda _paths: state)
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _state: True)
    monkeypatch.setattr(supervisor_module, "signal_owned", lambda *_args: True)
    ticks = iter([0, 31])
    monkeypatch.setattr(supervisor_module.time, "monotonic", lambda: next(ticks, 31))
    result = supervisor.reload()
    assert result["reload_status"] == "failed"
    assert "drain" in result["reload_error"]


def test_reload_control_returns_nonzero_for_signal_or_drain_failure(monkeypatch, tmp_path: Path) -> None:
    class Supervisor:
        def __init__(self, *, codex_home):
            self.codex_home = codex_home

        def reload(self):
            return {"status": "running", "reload_status": "failed", "reload_error": "drain"}

        def status(self):
            return {"status": "running"}

    monkeypatch.setattr(runtime_ctl_module, "GatewaySupervisor", Supervisor)
    assert runtime_ctl_main(["reload", "--codex-home", str(tmp_path / "home")]) == 2


def test_ensure_restarts_healthy_child_when_binding_is_pending(monkeypatch, tmp_path: Path) -> None:
    upstream = "https://chatgpt.com/backend-api/codex"
    pending = PolicySnapshot(
        2, "now", True, True, "gpt-5.6-luna", "medium", True,
        ("127.0.0.1", 8787), upstream, restart_required=True,
        pending_listener=("127.0.0.1", 8788), pending_upstream=upstream,
    )
    current = PolicySnapshot(
        3, "now", True, True, "gpt-5.6-luna", "medium", True,
        ("127.0.0.1", 8788), upstream,
    )

    class Manager:
        def snapshot(self):
            return pending

    class FreshManager:
        def __init__(self, _loader):
            pass

        def snapshot(self):
            return current

    old_state = type("State", (), {"host": "127.0.0.1", "port": 8787, "upstream": upstream})()
    new_state = type("State", (), {"host": "127.0.0.1", "port": 8788, "upstream": upstream})()
    reads = iter([old_state, None, None, new_state])
    signals: list[int] = []
    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", manager=Manager())
    monkeypatch.setattr(supervisor, "startup_lock", lambda: nullcontext())
    monkeypatch.setattr(supervisor, "_healthy", lambda _state: True)
    monkeypatch.setattr(supervisor_module.GatewayState, "read", lambda _paths: next(reads, new_state))
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _state: True)
    monkeypatch.setattr(supervisor_module, "signal_owned", lambda _state, sig: signals.append(sig) or True)
    monkeypatch.setattr(supervisor_module, "ConfigManager", FreshManager)
    monkeypatch.setattr(supervisor, "status", lambda: {"status": "running"})

    assert supervisor.ensure() == {"status": "running"}
    assert signals == [supervisor_module.signal.SIGTERM]


def test_ensure_does_not_reacquire_startup_lock_after_binding_drain(monkeypatch, tmp_path: Path) -> None:
    upstream = "https://chatgpt.com/backend-api/codex"
    pending = PolicySnapshot(
        2, "now", True, True, "gpt-5.6-luna", "medium", True,
        ("127.0.0.1", 8787), upstream, restart_required=True,
        pending_listener=("127.0.0.1", 8788), pending_upstream=upstream,
    )
    current = PolicySnapshot(
        3, "now", True, True, "gpt-5.6-luna", "medium", True,
        ("127.0.0.1", 8788), upstream,
    )

    class Manager:
        def snapshot(self):
            return pending

    class FreshManager:
        def __init__(self, _loader):
            pass

        def snapshot(self):
            return current

    old_state = type("State", (), {"host": "127.0.0.1", "port": 8787, "upstream": upstream})()
    new_state = type("State", (), {"host": "127.0.0.1", "port": 8788, "upstream": upstream})()
    reads = iter([old_state, None, None, new_state])
    entered = False

    class NonReentrantLock:
        def __enter__(self):
            nonlocal entered
            if entered:
                raise AssertionError("startup lock was reacquired recursively")
            entered = True

        def __exit__(self, *_args):
            nonlocal entered
            entered = False

    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", manager=Manager())
    monkeypatch.setattr(supervisor, "startup_lock", lambda: NonReentrantLock())
    monkeypatch.setattr(supervisor, "_healthy", lambda _state: True)
    monkeypatch.setattr(supervisor_module.GatewayState, "read", lambda _paths: next(reads, new_state))
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _state: True)
    monkeypatch.setattr(supervisor_module, "signal_owned", lambda *_args: True)
    monkeypatch.setattr(supervisor_module, "ConfigManager", FreshManager)
    monkeypatch.setattr(supervisor, "status", lambda: {"status": "running"})

    assert supervisor.ensure() == {"status": "running"}
    assert entered is False


def test_ensure_stops_owned_child_when_gateway_is_disabled(monkeypatch, tmp_path: Path) -> None:
    upstream = "https://chatgpt.com/backend-api/codex"
    disabled = PolicySnapshot(
        2, "now", True, True, "gpt-5.6-luna", "medium", False,
        ("127.0.0.1", 8787), upstream,
    )

    class Manager:
        def snapshot(self):
            return disabled

    state = type("State", (), {"host": "127.0.0.1", "port": 8787, "upstream": upstream})()
    reads = iter([state, None, None, None])
    signals: list[int] = []
    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", manager=Manager())
    monkeypatch.setattr(supervisor, "startup_lock", lambda: nullcontext())
    monkeypatch.setattr(supervisor_module.GatewayState, "read", lambda _paths: next(reads, None))
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _state: True)
    monkeypatch.setattr(supervisor_module, "signal_owned", lambda _state, sig: signals.append(sig) or True)

    result = supervisor.ensure()

    assert result["status"] == "stopped"
    assert result["gateway_enabled"] is False
    assert result["runtime"] is None
    assert signals == [supervisor_module.signal.SIGTERM]


def test_ensure_drains_verified_child_after_readiness_failure(monkeypatch, tmp_path: Path) -> None:
    upstream = "https://chatgpt.com/backend-api/codex"
    policy = PolicySnapshot(
        2, "now", True, True, "gpt-5.6-luna", "medium", True,
        ("::1", 8787), upstream,
    )

    class Manager:
        def snapshot(self):
            return policy

    class Child:
        pid = 1234

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            raise AssertionError("verified child should be drained")

    child = Child()
    state = type("State", (), {"pid": child.pid, "host": "::1", "port": 8787, "upstream": upstream})()
    reads = iter([None, state, None])
    signals: list[int] = []
    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", manager=Manager())
    monkeypatch.setattr(supervisor, "startup_lock", lambda: nullcontext())
    monkeypatch.setattr(supervisor_module, "spawn_gateway", lambda **_kwargs: child)
    monkeypatch.setattr(supervisor_module.GatewayState, "read", lambda _paths: next(reads, None))
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _state: True)
    monkeypatch.setattr(supervisor, "_healthy", lambda _state: False)
    monkeypatch.setattr(supervisor_module, "signal_owned", lambda _state, sig: signals.append(sig) or True)
    with pytest.raises(RuntimeError, match="did not become ready"):
        supervisor.ensure(wait_seconds=0)
    assert signals == [supervisor_module.signal.SIGTERM]


def test_ensure_drains_legacy_cortex_gateway_before_rebind(monkeypatch, tmp_path: Path) -> None:
    upstream = "https://chatgpt.com/backend-api/codex"
    policy = PolicySnapshot(
        1, "now", True, True, "gpt-5.6-luna", "medium", True,
        ("127.0.0.1", 8787), upstream,
    )

    class Manager:
        def snapshot(self):
            return policy

    class Child:
        pid = 4321

        def poll(self):
            return None

    child = Child()
    state = type("State", (), {"pid": child.pid, "host": "127.0.0.1", "port": 8787, "upstream": upstream})()
    conflicts = iter(([9876], []))
    reads = iter([None, state])
    signaled: list[tuple[int, str, int]] = []
    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", manager=Manager())
    monkeypatch.setattr(supervisor, "startup_lock", lambda: nullcontext())
    monkeypatch.setattr(supervisor_module, "gateway_processes", lambda **_kwargs: next(conflicts, []))
    monkeypatch.setattr(
        supervisor_module,
        "signal_gateway_process",
        lambda pid, **kwargs: signaled.append((pid, kwargs["host"], kwargs["port"])) or True,
    )
    monkeypatch.setattr(supervisor_module.GatewayState, "read", lambda _paths: next(reads, state))
    monkeypatch.setattr(supervisor_module, "spawn_gateway", lambda **_kwargs: child)
    monkeypatch.setattr(supervisor, "_healthy", lambda _state: True)
    monkeypatch.setattr(supervisor, "status", lambda: {"status": "running"})

    assert supervisor.ensure() == {"status": "running"}
    assert signaled == [(9876, "127.0.0.1", 8787)]


def test_marketplace_bootstrap_keeps_pip_output_off_mcp_stdout(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("CORTEX_DEPENDENCY_DIR", raising=False)
    pip_calls: list[dict[str, object]] = []
    ensured: list[Path] = []

    def fake_run(_args, **kwargs):
        pip_calls.append(kwargs)

    class Supervisor:
        def __init__(self, *, codex_home):
            ensured.append(codex_home)

        def connect(self):
            return {"status": "running"}

        def startup_lock(self):
            return nullcontext()

    monkeypatch.setattr(marketplace_bootstrap.subprocess, "run", fake_run)
    monkeypatch.setattr(marketplace_bootstrap, "GatewaySupervisor", Supervisor)
    monkeypatch.setattr(marketplace_bootstrap, "verified_dependencies", lambda *_: ("manifest", "bytes", "combined"))

    marketplace_bootstrap.bootstrap()

    assert pip_calls and pip_calls[0]["check"] is True
    assert pip_calls[0]["stdout"] is marketplace_bootstrap.subprocess.DEVNULL
    assert ensured == [home]


def test_diagnostics_emit_all_bounded_outcome_fields(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("cortex.gateway")
    with caplog.at_level(logging.INFO, logger="cortex.gateway"):
        log_outcome(logger, request_id="rid", request_kind="ordinary", http_status=200, transport_outcome="completed", duration_ms=1.5)
    message = caplog.records[-1].message
    assert all(f"{field}=" in message for field in REQUIRED_OUTCOME_FIELDS)
    assert "authorization" not in message.lower()


def test_marketplace_bootstrap_is_default_and_dev_recovery_stays_explicit() -> None:
    launcher = Path("scripts/cortex-dev").read_text()
    server = Path("plugins/cortex/scripts/cortex_runtime/server.py").read_text()
    assert "gateway.enabled" not in launcher  # developer launcher only honors explicit config
    assert 'cached_control="${CODEX_HOME}/plugins/cache/cortex/cortex/${candidate_version}/scripts/cortex_runtime_ctl.py"' in launcher
    assert 'python3 -B "${cached_control}" ensure --codex-home "${CODEX_HOME}"' in launcher
    assert 'python3 -B "${script_dir}/../plugins/cortex/scripts/cortex_runtime_ctl.py" ensure' not in launcher
    assert "from .marketplace_bootstrap import bootstrap" in server
    assert "    bootstrap()" in server
    assert "recover_gateway_if_explicitly_enabled" in server


def test_isolated_dependency_delivery_cleans_stale_target_and_verifies_versions() -> None:
    launcher = Path("scripts/cortex-dev").read_text()
    requirements = Path("plugins/cortex/requirements.txt").read_text()
    lock = Path("plugins/cortex/requirements.lock").read_text()
    assert "rm -rf -- \"${dependency_dir}\"" in launcher
    assert "--force-reinstall" in launcher
    assert "--require-hashes" in launcher
    assert "--no-compile" in launcher
    assert "isolated dependency mismatch" in launcher
    assert "aiohttp==3.14.3" in requirements
    assert "zstandard==0.25.0" in requirements
    assert all("==" in line for line in requirements.splitlines() if line and not line.startswith("#"))
    assert len(dependency_lock_entries(PLUGIN)) == 11
    assert all("--hash=sha256:" in line for line in lock.splitlines() if "==" in line)


def test_hash_lock_rejects_missing_or_malformed_hashes(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("aiohttp==3.14.3\n")
    with pytest.raises(ValueError, match="SHA-256 hashes"):
        dependency_lock_entries(tmp_path)
    lock.write_text("aiohttp==3.14.3 --hash=sha256:not-a-digest\n")
    with pytest.raises(ValueError, match="SHA-256 hashes"):
        dependency_lock_entries(tmp_path)


def test_pip_rejects_a_mismatched_dependency_hash(tmp_path: Path) -> None:
    wheel = tmp_path / "demo-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"not-a-wheel")
    lock = tmp_path / "requirements.lock"
    lock.write_text("demo==1.0.0 --hash=sha256:" + "0" * 64 + "\n")
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--no-index", "--find-links", str(tmp_path), "--no-deps", "--target", str(target),
            "--require-hashes", "-r", str(lock),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "DO NOT MATCH THE HASHES" in result.stdout + result.stderr


def test_dependency_identity_binds_manifest_and_installed_bytes(tmp_path: Path) -> None:
    target = tmp_path / "deps"
    target.mkdir(mode=0o700)
    (target / "package.py").write_text("safe = True\n")
    first = dependency_identity(PLUGIN, target, required=True)
    assert all(len(value) == 64 for value in first)
    (target / "package.py").write_text("safe = False\n")
    second = dependency_identity(PLUGIN, target, required=True)
    assert first[0] == second[0]
    assert first[1] != second[1]
    assert first[2] != second[2]


def test_supervisor_health_rejects_substituted_dependency_bytes(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "deps"
    target.mkdir(mode=0o700)
    (target / "package.py").write_text("safe = True\n")
    monkeypatch.setenv("CORTEX_DEPENDENCY_DIR", str(target))
    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", plugin_root=PLUGIN)
    executable = str((PLUGIN / "scripts" / "cortex_gateway.py").resolve())
    state = GatewayState.new(
        pid=os.getpid(), host="127.0.0.1", port=8787,
        plugin_version=json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())["version"],
        package_digest=package_payload_digest(PLUGIN),
        upstream="https://chatgpt.com/backend-api/codex", policy_revision=1,
        executable=executable, plugin_root=PLUGIN,
    )
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _state: True)

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit):
            return json.dumps({
                "service": "cortex-model-gateway", "status": "ok",
                "instance_id": state.instance_id, "package_digest": state.package_digest,
                "plugin_version": state.plugin_version,
                "dependency_manifest_digest": state.dependency_manifest_digest,
                "dependency_bytes_digest": state.dependency_bytes_digest,
                "dependency_digest": state.dependency_digest,
                "host": state.host, "port": state.port,
            }).encode()

    monkeypatch.setattr(supervisor_module, "urlopen", lambda *_args, **_kwargs: Response())
    assert supervisor._healthy(state) is True
    (target / "package.py").write_text("safe = False\n")
    assert supervisor._healthy(state) is False


def test_supervisor_health_brackets_ipv6_authority(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "deps"
    target.mkdir(mode=0o700)
    (target / "package.py").write_text("safe = True\n")
    monkeypatch.setenv("CORTEX_DEPENDENCY_DIR", str(target))
    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", plugin_root=PLUGIN)
    executable = str((PLUGIN / "scripts" / "cortex_gateway.py").resolve())
    state = GatewayState.new(
        pid=os.getpid(), host="::1", port=8787,
        plugin_version=json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())["version"],
        package_digest=package_payload_digest(PLUGIN),
        upstream="https://chatgpt.com/backend-api/codex", policy_revision=1,
        executable=executable, plugin_root=PLUGIN,
    )
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _state: True)
    seen: list[str] = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit):
            return json.dumps({
                "service": "cortex-model-gateway", "status": "ok",
                "instance_id": state.instance_id, "package_digest": state.package_digest,
                "plugin_version": state.plugin_version,
                "dependency_manifest_digest": state.dependency_manifest_digest,
                "dependency_bytes_digest": state.dependency_bytes_digest,
                "dependency_digest": state.dependency_digest,
                "host": state.host, "port": state.port,
            }).encode()

    monkeypatch.setattr(supervisor_module, "urlopen", lambda url, **_kwargs: seen.append(url) or Response())
    assert supervisor._healthy(state) is True
    assert seen == ["http://[::1]:8787/health"]


def test_supervisor_health_rejects_draining_child(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "deps"
    target.mkdir(mode=0o700)
    (target / "package.py").write_text("safe = True\n")
    monkeypatch.setenv("CORTEX_DEPENDENCY_DIR", str(target))
    supervisor = GatewaySupervisor(codex_home=tmp_path / "home", plugin_root=PLUGIN)
    executable = str((PLUGIN / "scripts" / "cortex_gateway.py").resolve())
    state = GatewayState.new(
        pid=os.getpid(), host="127.0.0.1", port=8787,
        plugin_version=json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())["version"],
        package_digest=package_payload_digest(PLUGIN),
        upstream="https://chatgpt.com/backend-api/codex", policy_revision=1,
        executable=executable, plugin_root=PLUGIN,
    )
    monkeypatch.setattr(supervisor_module, "state_matches_process", lambda _state: True)

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit):
            return json.dumps({
                "service": "cortex-model-gateway", "status": "draining",
                "instance_id": state.instance_id, "package_digest": state.package_digest,
                "plugin_version": state.plugin_version,
                "dependency_manifest_digest": state.dependency_manifest_digest,
                "dependency_bytes_digest": state.dependency_bytes_digest,
                "dependency_digest": state.dependency_digest,
                "host": state.host, "port": state.port,
            }).encode()

    monkeypatch.setattr(supervisor_module, "urlopen", lambda *_args, **_kwargs: Response())
    assert supervisor._healthy(state) is False


def test_cold_invalid_config_fails_without_defaults(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    path = write_default_config(home)
    path.write_text("schema_version = 999\n")
    with pytest.raises(ConfigError):
        ConfigManager(ConfigLoader(home)).snapshot()


def test_legacy_capacity_configuration_is_rejected_as_unknown(tmp_path: Path) -> None:
    """There is no configurable proxy slot ceiling to accidentally enable."""
    home = tmp_path / "codex"
    path = write_default_config(home)
    path.write_text(
        "schema_version = 1\n"
        "[gateway]\n"
        "enabled = true\n"
        "max_concurrent_requests = 1\n"
    )
    with pytest.raises(ConfigError, match="unknown gateway key"):
        ConfigManager(ConfigLoader(home)).snapshot()


def test_request_headers_are_forwarded_losslessly_including_duplicates() -> None:
    headers = [
        ("Authorization", "Bearer synthetic"),
        ("Cookie", "principal=a"),
        ("Cookie", "session=b"),
        ("Host", "127.0.0.1:8787"),
        ("Origin", "https://caller.invalid"),
        ("Connection", "X-Private, keep-alive"),
        ("X-Private", "synthetic-value"),
        ("Content-Length", "10"),
    ]
    assert forwarded_header_items(headers, transformed=True) == headers
    expected_upstream = [
        (key, "chatgpt.com" if key.lower() == "host" else value)
        for key, value in headers
    ]
    assert forwarded_header_items(headers, transformed=True, upstream_host="chatgpt.com") == expected_upstream
    # The legacy mapping remains available for diagnostics, but is explicitly
    # not used by the proxy because it cannot represent duplicate fields.
    assert forwarded_headers(headers)["Cookie"] == "session=b"


def test_gateway_forwards_when_legacy_capacity_would_be_saturated() -> None:
    """Admission must not reject or retry when the old 32-slot count is full."""
    async def scenario() -> None:
        seen: list[bytes] = []

        async def upstream_handler(request: web.Request) -> web.Response:
            seen.append(await request.read())
            return web.Response(status=200, body=b"forwarded-once")

        upstream_app = web.Application()
        upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        class Manager:
            def snapshot(self) -> PolicySnapshot:
                return PolicySnapshot(
                    revision=1,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    router_enabled=False,
                    compaction_enabled=False,
                    model="gpt-5.6-luna",
                    effort="medium",
                    gateway_enabled=True,
                    listener=("127.0.0.1", 8787),
                    upstream=f"http://127.0.0.1:{upstream_port}/backend-api/codex",
                )

        app = create_app(Manager())
        proxy = app[GATEWAY_PROXY_KEY]
        proxy_runner = web.AppRunner(app)
        await proxy_runner.setup()
        proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
        await proxy_site.start()
        proxy_port = proxy_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        try:
            # This models the state that previously produced a local 503. The
            # value is drain bookkeeping only and must not affect admission.
            proxy._active = 32
            async with ClientSession() as client:
                response = await client.post(
                    f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses",
                    data=b"single-logical-request",
                )
                assert response.status == 200
                assert response.headers.get("Retry-After") is None
                assert await response.read() == b"forwarded-once"
            assert seen == [b"single-logical-request"]
            for _ in range(100):
                if proxy.active_requests == 32:
                    break
                await asyncio.sleep(0.01)
            assert proxy.active_requests == 32
        finally:
            # Restore the synthetic drain count before app cleanup so the test
            # does not wait on work that it created only for this assertion.
            proxy._active = 0
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(scenario())


def test_proxy_preserves_ordinary_bytes_and_rewrites_v2_fields() -> None:
    async def scenario() -> None:
        seen: list[tuple[bytes, dict[str, str]]] = []
        seen_items: list[list[tuple[str, str]]] = []

        async def upstream_handler(request: web.Request) -> web.Response:
            body = await request.read()
            seen.append((body, dict(request.headers)))
            seen_items.append(list(request.headers.items()))
            response = web.Response(status=201, body=b"data: done\n\n", headers={"Content-Type": "text/event-stream"})
            response.headers.add("Set-Cookie", "upstream=must-not-persist; Path=/")
            return response

        upstream_app = web.Application()
        upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        class Manager:
            def snapshot(self) -> PolicySnapshot:
                return PolicySnapshot(
                    revision=1,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    router_enabled=True,
                    compaction_enabled=True,
                    model="gpt-5.6-luna",
                    effort="medium",
                    gateway_enabled=True,
                    listener=("127.0.0.1", 8787),
                    upstream=f"http://127.0.0.1:{upstream_port}/backend-api/codex",
                )

        proxy_runner = web.AppRunner(create_app(Manager()))
        await proxy_runner.setup()
        proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
        await proxy_site.start()
        proxy_port = proxy_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        async with ClientSession() as client:
            ordinary = b'{"model":"gpt-5.6-sol","input":[{"type":"message"}]}'
            request_headers = CIMultiDict(
                [
                    ("Authorization", "Bearer synthetic"),
                    ("Cookie", "principal=a"),
                    ("Cookie", "session=b"),
                    ("Origin", "https://caller.invalid"),
                    ("X-Duplicate", "one"),
                    ("X-Duplicate", "two"),
                ]
            )
            response = await client.post(f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses?x=1", data=ordinary, headers=request_headers)
            assert response.status == 201
            await response.read()
            assert seen[-1][0] == ordinary
            assert [item for item in seen_items[-1] if item[0].lower() in {"cookie", "origin", "x-duplicate"}] == [
                ("Cookie", "principal=a"),
                ("Cookie", "session=b"),
                ("Origin", "https://caller.invalid"),
                ("X-Duplicate", "one"),
                ("X-Duplicate", "two"),
            ]
            assert any(key.lower() == "host" and value == f"127.0.0.1:{upstream_port}" for key, value in seen_items[-1])
            compact = json.dumps({"model": "gpt-5.6-sol", "reasoning": {"effort": "high", "summary": "auto"}, "input": [{"type": "compaction_trigger"}]}).encode()
            response = await client.post(f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses", data=compact, headers={"Authorization": "Bearer synthetic", "Content-Type": "application/json"})
            assert response.status == 201
            await response.read()
            routed = json.loads(seen[-1][0])
            assert routed["model"] == "gpt-5.6-luna"
            assert routed["reasoning"] == {"effort": "medium", "summary": "auto"}
            assert seen[-1][1]["Authorization"] == "Bearer synthetic"
            response = await client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses",
                data=ordinary,
                headers={"Authorization": "Bearer principal-b", "Cookie": "principal=b"},
            )
            await response.read()
            assert seen[-1][1].get("Cookie") == "principal=b"
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()

    asyncio.run(scenario())


def test_proxy_preserves_accept_encoding_and_raw_response_bytes() -> None:
    async def scenario() -> None:
        seen_accept_encoding: list[str | None] = []
        response_bodies = {
            "gzip": b"\x1f\x8bsynthetic-gzip-bytes",
            "br": b"\x8bsynthetic-br-bytes",
        }

        async def upstream_handler(request: web.Request) -> web.Response:
            encoding = request.headers.get("Accept-Encoding")
            seen_accept_encoding.append(encoding)
            if encoding is None:
                return web.json_response({"models": ["gpt-5.6-luna"]})
            assert encoding in response_bodies
            return web.Response(
                body=response_bodies[encoding],
                headers={"Content-Encoding": encoding, "Content-Type": "application/octet-stream"},
            )

        upstream_app = web.Application()
        upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        class Manager:
            def snapshot(self) -> PolicySnapshot:
                return PolicySnapshot(
                    revision=1,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    router_enabled=False,
                    compaction_enabled=False,
                    model="gpt-5.6-luna",
                    effort="medium",
                    gateway_enabled=True,
                    listener=("127.0.0.1", 8787),
                    upstream=f"http://127.0.0.1:{upstream_port}/backend-api/codex",
                )

        proxy_runner = web.AppRunner(create_app(Manager()))
        await proxy_runner.setup()
        proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
        await proxy_site.start()
        proxy_port = proxy_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        try:
            async with ClientSession(auto_decompress=False, skip_auto_headers={"Accept-Encoding"}) as client:
                response = await client.get(f"http://127.0.0.1:{proxy_port}/backend-api/codex/models")
                assert response.status == 200
                identity_body = await response.read()
                assert identity_body == b'{"models": ["gpt-5.6-luna"]}'
                assert json.loads(identity_body) == {"models": ["gpt-5.6-luna"]}

                for encoding, expected in response_bodies.items():
                    response = await client.get(
                        f"http://127.0.0.1:{proxy_port}/backend-api/codex/models",
                        headers={"Accept-Encoding": encoding},
                    )
                    assert response.status == 200
                    assert response.headers["Content-Encoding"] == encoding
                    assert await response.read() == expected
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

        assert seen_accept_encoding == [None, "gzip", "br"]

    asyncio.run(scenario())


def test_disabled_policy_never_forwards_after_reload() -> None:
    async def scenario() -> None:
        forwarded = 0

        async def upstream_handler(_request: web.Request) -> web.Response:
            nonlocal forwarded
            forwarded += 1
            return web.Response(status=200, text="unexpected")

        upstream_app = web.Application()
        upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        class Manager:
            def snapshot(self) -> PolicySnapshot:
                return PolicySnapshot(
                    revision=2,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    router_enabled=True,
                    compaction_enabled=True,
                    model="gpt-5.6-luna",
                    effort="medium",
                    gateway_enabled=False,
                    listener=("127.0.0.1", 8787),
                    upstream=f"http://127.0.0.1:{upstream_port}/backend-api/codex",
                )

        proxy_runner = web.AppRunner(create_app(Manager()))
        await proxy_runner.setup()
        proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
        await proxy_site.start()
        proxy_port = proxy_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        async with ClientSession() as client:
            response = await client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses",
                json={"input": []},
            )
            assert response.status == 503
            payload = await response.json()
            assert payload["error"]["code"] == "gateway_disabled"
        assert forwarded == 0
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()

    asyncio.run(scenario())


def test_slow_body_rechecks_disable_before_forwarding() -> None:
    async def scenario() -> None:
        forwarded = 0
        body_started = asyncio.Event()
        release_body = asyncio.Event()

        async def upstream_handler(_request: web.Request) -> web.Response:
            nonlocal forwarded
            forwarded += 1
            return web.Response(status=200, text="unexpected")

        upstream_app = web.Application()
        upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        class Manager:
            enabled = True
            calls = 0

            def snapshot(self) -> PolicySnapshot:
                self.calls += 1
                return PolicySnapshot(
                    revision=1 if self.enabled else 2,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    router_enabled=False,
                    compaction_enabled=False,
                    model="gpt-5.6-luna",
                    effort="medium",
                    gateway_enabled=self.enabled,
                    listener=("127.0.0.1", 8787),
                    upstream=f"http://127.0.0.1:{upstream_port}/backend-api/codex",
                )

        manager = Manager()
        proxy_runner = web.AppRunner(create_app(manager))
        await proxy_runner.setup()
        proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
        await proxy_site.start()
        proxy_port = proxy_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        async def slow_body():
            yield b"{\"input\":[]}" 
            body_started.set()
            await release_body.wait()

        async with ClientSession() as client:
            request_task = asyncio.create_task(client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses",
                data=slow_body(),
            ))
            await asyncio.wait_for(body_started.wait(), timeout=2)
            manager.enabled = False
            release_body.set()
            response = await asyncio.wait_for(request_task, timeout=3)
            assert response.status == 503
            payload = await response.json()
            assert payload["error"]["code"] == "gateway_disabled"
        assert forwarded == 0
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()

    asyncio.run(scenario())


def test_proxy_passes_malformed_ordinary_and_legacy_bytes_unchanged_when_enabled(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        seen: list[tuple[str, bytes, dict[str, str]]] = []

        async def upstream_handler(request: web.Request) -> web.Response:
            seen.append((request.raw_path, await request.read(), dict(request.headers)))
            return web.Response(status=207, body=b"upstream-result")

        upstream_app = web.Application(handler_args={"auto_decompress": False})
        upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        class Manager:
            def snapshot(self) -> PolicySnapshot:
                return PolicySnapshot(
                    revision=1,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    router_enabled=True,
                    compaction_enabled=True,
                    model="gpt-5.6-luna",
                    effort="medium",
                    gateway_enabled=True,
                    listener=("127.0.0.1", 8787),
                    upstream=f"http://127.0.0.1:{upstream_port}/backend-api/codex",
                )

        proxy_runner = web.AppRunner(create_app(Manager()))
        await proxy_runner.setup()
        proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
        await proxy_site.start()
        proxy_port = proxy_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        async with ClientSession(auto_decompress=False) as client:
            malformed = b"\xffordinary-not-json"
            response = await client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses?ordinary=1",
                data=malformed,
                headers={"Content-Type": "application/json", "X-Trace": "ordinary"},
            )
            assert response.status == 207
            assert await response.read() == b"upstream-result"
            assert seen[-1][0] == "/backend-api/codex/responses?ordinary=1"
            assert seen[-1][1] == malformed
            assert seen[-1][2]["Content-Type"] == "application/json"
            assert seen[-1][2]["X-Trace"] == "ordinary"

            compressed_malformed = gzip.compress(b"compressed-not-json")
            response = await client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses?ordinary=compressed",
                data=compressed_malformed,
                headers={"Content-Encoding": "gzip", "X-Trace": "compressed"},
            )
            assert response.status == 207
            assert await response.read() == b"upstream-result"
            assert seen[-1][0] == "/backend-api/codex/responses?ordinary=compressed"
            assert seen[-1][1] == compressed_malformed
            assert seen[-1][2]["Content-Encoding"] == "gzip"
            assert seen[-1][2]["X-Trace"] == "compressed"

            legacy = b"legacy-wire-format"
            response = await client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses/compact?legacy=1",
                data=legacy,
                headers={"Content-Type": "application/octet-stream", "X-Trace": "legacy"},
            )
            assert response.status == 207
            assert await response.read() == b"upstream-result"
            assert seen[-1][0] == "/backend-api/codex/responses/compact?legacy=1"
            assert seen[-1][1] == legacy
            assert seen[-1][2]["Content-Type"] == "application/octet-stream"
            assert seen[-1][2]["X-Trace"] == "legacy"

            legacy_json = json.dumps({
                "model": "gpt-5.6-sol",
                "reasoning": {"effort": "high", "summary": "manual"},
                "input": [{"role": "user", "content": "snapshot"}],
            }).encode()
            response = await client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses/compact?legacy=1",
                data=legacy_json,
                headers={"Content-Type": "application/json"},
            )
            assert response.status == 207
            await response.read()
            assert seen[-1][0] == "/backend-api/codex/responses?legacy=1"
            routed = json.loads(seen[-1][1])
            assert routed["model"] == "gpt-5.6-luna"
            assert routed["reasoning"] == {"effort": "medium", "summary": "manual"}
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()

    caplog.set_level(logging.INFO, logger="cortex.gateway")
    asyncio.run(scenario())
    assert "request_kind=legacy" in caplog.text
    assert "transport_outcome=legacy_passthrough" in caplog.text
    assert "request_kind=manual_compaction" in caplog.text
    assert "original_model=gpt-5.6-sol" in caplog.text
    assert "routed_model=gpt-5.6-luna" in caplog.text


def test_proxy_keeps_explicit_compaction_errors_with_enabled_routing() -> None:
    async def scenario() -> None:
        seen: list[bytes] = []

        async def upstream_handler(request: web.Request) -> web.Response:
            seen.append(await request.read())
            return web.Response(status=207, body=b"unexpected-upstream")

        upstream_app = web.Application()
        upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        class Manager:
            def snapshot(self) -> PolicySnapshot:
                return PolicySnapshot(
                    revision=1,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    router_enabled=True,
                    compaction_enabled=True,
                    model="gpt-5.6-luna",
                    effort="medium",
                    gateway_enabled=True,
                    listener=("127.0.0.1", 8787),
                    upstream=f"http://127.0.0.1:{upstream_port}/backend-api/codex",
                )

        proxy_runner = web.AppRunner(create_app(Manager()))
        await proxy_runner.setup()
        proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
        await proxy_site.start()
        proxy_port = proxy_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        async with ClientSession() as client:
            invalid_reasoning = json.dumps({
                "input": [{"type": "compaction_trigger"}],
                "reasoning": "high",
            }).encode()
            response = await client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses",
                data=invalid_reasoning,
                headers={"Content-Type": "application/json"},
            )
            assert response.status == 400
            assert (await response.json())["error"]["code"] == "invalid_compaction_reasoning"
            assert seen == []

            response = await client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses",
                data=b"not-json",
                headers={"Content-Encoding": "br"},
            )
            assert response.status == 400
            assert (await response.json())["error"]["code"] == "unsupported_content_encoding"
            assert seen == []
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "authority",
    ["127.0.0.1", "127.0.0.1:8787", "LOCALHOST", "localhost:443", "[::1]", "[::1]:8787"],
)
def test_host_authority_accepts_only_valid_loopback_forms(authority: str) -> None:
    assert _valid_loopback_authority(authority)


@pytest.mark.parametrize(
    "authority",
    [None, "", " :8787", ":8787", "127.0.0.1:", "127.0.0.1:0", "127.0.0.1:65536", "127.0.0.1:abc", "::1", "[::dead]:1", "[::1]junk"],
)
def test_host_authority_rejects_empty_malformed_and_non_loopback_forms(authority: str | None) -> None:
    assert not _valid_loopback_authority(authority)


def test_client_disconnect_cancels_only_its_own_upstream_request() -> None:
    async def scenario() -> None:
        victim_started = asyncio.Event()
        victim_release = asyncio.Event()
        cancel_started = asyncio.Event()

        async def upstream_handler(request: web.Request) -> web.Response:
            kind = request.headers.get("X-Request")
            if kind == "victim":
                victim_started.set()
                await victim_release.wait()
                return web.Response(status=200, body=b"victim-ok")
            cancel_started.set()
            await asyncio.sleep(1)
            return web.Response(status=200, body=b"cancelled-request-should-not-complete")

        upstream_app = web.Application()
        upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        class Manager:
            def snapshot(self) -> PolicySnapshot:
                return PolicySnapshot(
                    revision=1,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    router_enabled=False,
                    compaction_enabled=False,
                    model="gpt-5.6-luna",
                    effort="medium",
                    gateway_enabled=True,
                    listener=("127.0.0.1", 8787),
                    upstream=f"http://127.0.0.1:{upstream_port}/backend-api/codex",
                )

        try:
            proxy = GatewayProxy(Manager())
            await proxy.start()
            assert proxy._session is not None
            victim_task = asyncio.create_task(
                proxy._session.post(
                    f"http://127.0.0.1:{upstream_port}/backend-api/codex/responses",
                    data=b"victim",
                    headers={"X-Request": "victim"},
                )
            )
            cancel_task = asyncio.create_task(
                proxy._session.post(
                    f"http://127.0.0.1:{upstream_port}/backend-api/codex/responses",
                    data=b"cancel",
                    headers={"X-Request": "cancel"},
                )
            )
            try:
                await asyncio.wait_for(victim_started.wait(), timeout=2)
                await asyncio.wait_for(cancel_started.wait(), timeout=2)
                await proxy._cancel_upstream(cancel_task)
                assert not proxy._session.closed
                victim_release.set()
                response = await asyncio.wait_for(victim_task, timeout=3)
                assert response.status == 200
                assert await response.read() == b"victim-ok"
            finally:
                if not victim_task.done():
                    victim_task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await victim_task
                await proxy.close()
        finally:
            await upstream_runner.cleanup()

    asyncio.run(scenario())


def test_wire_ingress_disables_aiohttp_auto_decompression() -> None:
    async def scenario() -> None:
        seen: list[tuple[bytes, str | None]] = []

        async def upstream_handler(request: web.Request) -> web.Response:
            seen.append((await request.read(), request.headers.get("Content-Encoding")))
            return web.Response(body=seen[-1][0], headers={"Content-Encoding": "gzip"})

        upstream_app = web.Application(handler_args={"auto_decompress": False})
        upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        class Manager:
            def snapshot(self) -> PolicySnapshot:
                return PolicySnapshot(
                    revision=1,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    router_enabled=False,
                    compaction_enabled=False,
                    model="gpt-5.6-luna",
                    effort="medium",
                    gateway_enabled=True,
                    listener=("127.0.0.1", 8787),
                    upstream=f"http://127.0.0.1:{upstream_port}/backend-api/codex",
                )

        proxy_runner = web.AppRunner(create_app(Manager()))
        await proxy_runner.setup()
        proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
        await proxy_site.start()
        proxy_port = proxy_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        raw = gzip.compress(b"wire-preserved")
        async with ClientSession(auto_decompress=False) as client:
            response = await client.post(
                f"http://127.0.0.1:{proxy_port}/backend-api/codex/responses",
                data=raw,
                headers={"Content-Encoding": "gzip"},
            )
            assert response.status == 200
            assert await response.read() == raw
        assert seen == [(raw, "gzip")]
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()

    asyncio.run(scenario())
