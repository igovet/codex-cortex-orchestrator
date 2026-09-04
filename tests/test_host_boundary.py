"""Source-only host boundary contracts; fixture transports are not live proof."""
import asyncio
from dataclasses import replace
import json
import stat

import pytest

from cortex_runtime.host_boundary import (
    Capability, CodexCliHostAdapter, CodexDesktopHostAdapter, CodexHostProbe,
    HostIdentity, HostObservation, SpawnSpec, ToolContract, canonical,
    CodexHostAdapter,
)


def identity(host="cli"):
    return HostIdentity(host, "app", "engine", "payload", "catalogue", "config", "connection")


def snapshot(host="cli"):
    return CodexHostProbe.capture(identity(host), tuple(
        ToolContract(op, "fixture." + op, canonical({"type": "object"}), Capability("declared", ("fixture-catalogue",)))
        for op in ("spawn", "list", "wait", "send_message", "interrupt")
    ), {
        "models.default": Capability("configured", ("fixture-config",)),
        "efforts.default": Capability("declared", ("fixture-catalogue",), canonical(["medium", "max"])),
    })


class Transport:
    def __init__(self):
        self.calls = []

    async def invoke(self, contract, intent):
        self.calls.append((contract, intent))
        return HostObservation(contract.operation, {
            "spawn": "ambiguous", "list": "unverified", "wait": "timeout",
            "send_message": "sent", "interrupt": "acknowledged",
        }[contract.operation], "fixture-event")


@pytest.mark.parametrize("cls,host", [(CodexCliHostAdapter, "cli"), (CodexDesktopHostAdapter, "desktop")])
def test_adapters_preserve_uncertainty_and_never_retry(cls, host):
    transport = Transport()
    adapter = cls(snapshot(host), transport)
    current = identity(host)
    results = [
        asyncio.run(adapter.spawn(SpawnSpec("dispatch", "work", "general"), current)),
        asyncio.run(adapter.list_agents(current)),
        asyncio.run(adapter.wait(("agent",), current)),
        asyncio.run(adapter.send_message("agent", "change", current)),
        asyncio.run(adapter.interrupt("agent", current)),
    ]
    assert [r.status for r in results] == ["ambiguous", "unverified", "timeout", "sent", "acknowledged"]
    assert len(transport.calls) == 5
    assert not any(r.complete or r.quiescent for r in results)


@pytest.mark.parametrize("field", ["host", "app_version", "engine_version", "payload_digest", "catalogue_digest", "config_digest", "connection_generation"])
def test_every_environment_change_invalidates_snapshot(field):
    transport = Transport()
    adapter = CodexCliHostAdapter(snapshot(), transport)
    current = replace(identity(), **{field: "desktop" if field == "host" else "changed"})
    with pytest.raises(ValueError, match="stale"):
        asyncio.run(adapter.list_agents(current))
    assert transport.calls == []


def test_snapshot_has_unknown_not_false_and_deterministic_digest(tmp_path):
    value = snapshot()
    assert value.capability("recovery.resume").state == "unverified"
    assert value.capability("capacity.available").value_json == "null"
    assert value.snapshot_digest == snapshot().snapshot_digest
    path = tmp_path / "capabilities.json"
    CodexHostProbe.save(value, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["digest"] == value.snapshot_digest
    with pytest.raises(FileExistsError):
        CodexHostProbe.save(value, path)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        CodexHostProbe.save(value, link)


def test_model_default_omits_luna_and_disallows_ultra():
    assert "model" not in SpawnSpec("d", "m", "general").model_options()
    assert SpawnSpec("d", "m", "architect", "gpt-5.6-terra", "max").model_options()["model"] == "gpt-5.6-terra"
    with pytest.raises(ValueError):
        SpawnSpec("d", "m", "general", "gpt-5.6-luna")
    with pytest.raises(ValueError):
        SpawnSpec("d", "m", "general", effort="ultra")


def test_snapshot_rejects_public_or_symlink_directory(tmp_path):
    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="owner-private"):
        CodexHostProbe.save(snapshot(), directory / "host.json")
    link = tmp_path / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink"):
        CodexHostProbe.save(snapshot(), link / "host.json")


def test_unverified_model_or_effort_cannot_dispatch():
    transport = Transport()
    adapter = CodexCliHostAdapter(snapshot(), transport)
    for spec in (SpawnSpec("d", "m", "architect", "gpt-5.6-terra"), SpawnSpec("d", "m", "general", effort="high")):
        with pytest.raises(ValueError, match="unverified"):
            asyncio.run(adapter.spawn(spec, identity()))
    assert not transport.calls


def test_observation_claims_require_consistent_facts():
    with pytest.raises(ValueError):
        HostObservation("spawn", "started", "event")
    with pytest.raises(ValueError):
        HostObservation("interrupt", "acknowledged", "event", quiescent=True)
    with pytest.raises(ValueError):
        HostObservation("wait", "timeout", "event", complete=True)
    with pytest.raises(ValueError):
        Capability("observed")


def test_wrong_host_and_wrong_transport_result_rejected():
    with pytest.raises(ValueError):
        CodexDesktopHostAdapter(snapshot(), Transport())

    class WrongTransport:
        async def invoke(self, contract, intent):
            return HostObservation("wait", "timeout", "fixture")

    with pytest.raises(ValueError, match="mismatched"):
        asyncio.run(CodexCliHostAdapter(snapshot(), WrongTransport()).list_agents(identity()))


def test_durable_dispatch_uses_the_host_boundary(monkeypatch):
    from cortex_runtime.delegation import native_dispatch_projection
    original = CodexHostAdapter.prepare_spawn
    calls = []
    def observed(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(CodexHostAdapter, "prepare_spawn", observed)
    value = native_dispatch_projection(assignment_ref="fixture", task_name="general",
        message="server rendered context", model="gpt-5.6-luna", reasoning_effort="max")
    assert calls[0]["model_route"] == "default"
    arguments = value["native_arguments"]
    assert "model" not in arguments
    assert list(arguments)[-1] == "message"
    assert arguments["reasoning_effort"] == "max"
