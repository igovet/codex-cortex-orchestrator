#!/usr/bin/env python3
"""Render compact Agent v2 profiles and references from one shared source."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/cortex"
SOURCES = PLUGIN / "agent-sources"
AGENTS = PLUGIN / "agents"
WORKER_REFERENCES = (
    "code-and-evidence.md",
    "interactive-resources.md",
    "report-publication.md",
)
SPECIALIZED = (
    "Profile title",
    "Role and responsibility",
    "When to use this profile",
    "Specialist workflow",
    "Quality criteria",
    "Report and handoff",
)


def sections(markdown: str) -> dict[str, str]:
    lines = markdown.rstrip().splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError("specialization must start with one profile title")
    result = {"Profile title": lines[0]}
    for chunk in re.split(r"(?m)^## ", "\n".join(lines[1:]))[1:]:
        heading, separator, body = chunk.partition("\n")
        if not separator or heading in result:
            raise ValueError("invalid or duplicate specialization section")
        result[heading] = body.strip()
    if set(result) != set(SPECIALIZED):
        raise ValueError("specialization sections do not match the canonical template")
    return result


def expected_profiles(plugin: Path = PLUGIN) -> dict[Path, bytes]:
    source_root = plugin / "agent-sources"
    template = (source_root / "worker-protocol.md").read_text()
    profiles = json.loads((plugin / "profiles.json").read_text())["profiles"]
    rendered: dict[Path, bytes] = {}
    for profile in profiles:
        filename = profile["filename"]
        report_template = profile["report_template"]
        if report_template not in {"general", "planning", "investigation", "implementation", "verification", "documentation", "synthesis"}:
            raise ValueError(f"invalid report template for {profile['name']}")
        values = sections((source_root / (Path(filename).stem + ".md")).read_text())
        prompt = template.replace("{{Default report template}}", report_template)
        for key in SPECIALIZED:
            prompt = prompt.replace("{{" + key + "}}", values[key])
        if "{{" in prompt or "}}" in prompt:
            raise ValueError("unresolved worker protocol placeholder")
        body = (
            "name = " + json.dumps(profile["name"], ensure_ascii=False) + "\n"
            "description = " + json.dumps(profile["description"], ensure_ascii=False) + "\n"
            'developer_instructions = """\n' + prompt.rstrip() + '\n"""\n'
        )
        rendered[plugin / "agents" / filename] = body.encode()
    return rendered


def expected_skills(plugin: Path = PLUGIN) -> dict[Path, bytes]:
    rendered = {}
    for path, body in expected_profiles(plugin).items():
        profile = tomllib.loads(body.decode())
        name = 'worker-' + path.stem
        description = 'Cortex delegated specialist only: ' + profile['description']
        frontmatter = '---\nname: ' + name + '\ndescription: ' + json.dumps(description) + '\n---\n\n'
        footer = '\n<!-- END OF COMPLETE CORTEX WORKER SKILL -->\n'
        text = frontmatter + profile['developer_instructions'].rstrip() + '\n' + footer
        rendered[plugin / 'skills' / name / 'SKILL.md'] = text.encode()
    return rendered


def expected_worker_references(plugin: Path = PLUGIN) -> dict[Path, bytes]:
    source_root = plugin / "agent-sources" / "references"
    rendered = {}
    for skill_path in expected_skills(plugin):
        reference_root = skill_path.parent / "references"
        for filename in WORKER_REFERENCES:
            rendered[reference_root / filename] = (source_root / filename).read_bytes()
    return rendered


def expected_agent_references(plugin: Path = PLUGIN) -> dict[Path, bytes]:
    source_root = plugin / "agent-sources" / "references"
    return {
        plugin / "agents" / "references" / filename:
        (source_root / filename).read_bytes()
        for filename in WORKER_REFERENCES
    }


def expected_embedded_guidance(plugin: Path = PLUGIN) -> dict[Path, bytes]:
    # Harvest always needs the census, so keep it in that selected skill.
    # Optional declared references still use normal progressive loading.
    path = plugin / 'skills/knowledge-harvest/SKILL.md'
    marker = '<!-- BEGIN HOST-ATTACHED FEATURE CENSUS -->'
    head = path.read_text().split(marker, 1)[0].rstrip()
    census = (path.parent / 'references/feature-census.md').read_text().strip()
    return {path: (head + '\n\n' + marker + '\n\n' + census
                  + '\n\n<!-- END HOST-ATTACHED FEATURE CENSUS -->\n').encode()}


def check(plugin: Path = PLUGIN) -> None:
    expected = expected_profiles(plugin)
    actual = set((plugin / "agents").glob("*.toml"))
    if actual != set(expected):
        raise ValueError("generated Agent v2 profile set differs from source catalogue")
    skills = expected_skills(plugin)
    if set((plugin / "skills").glob("worker-*/SKILL.md")) != set(skills):
        raise ValueError("generated worker skill set differs from source catalogue")
    expected.update(skills)
    references = expected_worker_references(plugin)
    if set((plugin / "skills").glob("worker-*/references/*.md")) != set(references):
        raise ValueError("generated worker reference set differs from source catalogue")
    expected.update(references)
    agent_references = expected_agent_references(plugin)
    if set((plugin / "agents" / "references").glob("*.md")) != set(agent_references):
        raise ValueError("generated agent reference set differs from source catalogue")
    expected.update(agent_references)
    expected.update(expected_embedded_guidance(plugin))
    for path, body in expected.items():
        if path.read_bytes() != body:
            raise ValueError(f"generated Agent v2 profile is stale: {path.name}")


def write(plugin: Path = PLUGIN) -> None:
    generated = (
        expected_profiles(plugin)
        | expected_skills(plugin)
        | expected_worker_references(plugin)
        | expected_agent_references(plugin)
        | expected_embedded_guidance(plugin)
    )
    for path, body in generated.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "write"))
    args = parser.parse_args()
    try:
        check() if args.action == "check" else write()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Agent profile generation failed: {exc}") from None
    print(f"Agent v2 profiles {args.action}: 22 compact prompts with shared references")
