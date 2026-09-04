"""Current typed terminal report views; no legacy envelope or generic fallback."""
from collections.abc import Mapping
from typing import Any

from cortex_runtime.markdown_document import (
    BulletList, Document, Finding, KeyValue, OrderedSteps, Paragraph, Section, Table,
)
from cortex_runtime.typed_publications import validate_report


def _list(title, values):
    return Section(title, [BulletList(values)] if values else [Paragraph("None reported.")])


def _checks(checks):
    return Table(["Expected check", "Required", "Description"], [
        [row["key"], "yes" if row["required"] else "no", row["description"]] for row in checks])


def _artifact(artifact):
    if artifact is None:
        return [Section("Artifact boundary", [Paragraph("Artifact-independent; no artifact observation claimed.")])]
    pairs = [("Artifact observation", artifact)]
    if "boundary" in artifact:
        pairs.append(("New boundary observation", artifact["boundary"]))
    sections = []
    for title, observation in pairs:
        changes = observation["changes"]
        sections.append(Section(title, [
            KeyValue("Method", observation["method"]),
            KeyValue("Start fingerprint", observation["start"]),
            KeyValue("End fingerprint", observation["end"]),
            KeyValue("Changed path count", str(changes["count"])),
            KeyValue("Changed-path commitment", changes["digest"]),
            KeyValue("Within assigned domains", "yes" if changes["within_domains"] else "no"),
            BulletList(["Changed path sample: " + path for path in changes["samples"]]),
        ]))
    if "baseline_changes" in artifact:
        changes = artifact["baseline_changes"]
        sections.append(Section("Pre-existing changes since sealed baseline", [
            Paragraph("These observations are not changes attributed to this worker."),
            KeyValue("Changed path count", str(changes["count"])),
            KeyValue("Changed-path commitment", changes["digest"]),
            KeyValue("Within assigned domains", "yes" if changes["within_domains"] else "no"),
            BulletList(changes["samples"]),
        ]))
    return sections


def _plan(content):
    sections = [Section("Scope", [Paragraph(content["scope"])])]
    for candidate in content["candidates"]:
        sections.append(Section("Alternative: " + candidate["key"], [BulletList(candidate["consequences"])]))
        delta = candidate["delta"]
        sections.append(Section("Proposed semantic change", [
            KeyValue("Retired outcomes", "; ".join(delta["retire"]) or "None"),
            Table(["Outcome", "Acceptance", "Constraints", "Verification"], [
                [item["outcome"], "; ".join(item["acceptance"]), "; ".join(item["constraints"]), "; ".join(item["verification"])]
                for item in delta["add"]]),
        ]))
        sections.extend(_plan_graph(candidate["graph"]))
    return sections


def _plan_graph(graph):
    sections = [Section("Outcome acceptance composition", [Table(
            ["Outcome", "Required contributions", "Non-execution reason"],
            [[row["outcome"], ", ".join(row["all_of"]) or "None", row.get("non_execution", "")]
             for row in graph["outcomes"]])]),
        Section("Execution dependencies", [Table(
            ["Node", "Purpose", "Owner", "Execution mode", "Predecessors"],
            [[n["key"], n["kind"], n["owner"], n["execution_mode"],
              ", ".join(e["node"] for e in n["dependencies"]) or "None"] for n in graph["nodes"]])]),
        Section("Planned artifact boundary", [
            KeyValue("Fingerprint method", graph["fingerprint_method"]), BulletList(graph["artifact_paths"])]),
        Section("Finite workflow budgets", [
            KeyValue(k.replace("_", " "), str(v)) for k, v in graph["budgets"].items()]),
    ]
    # Declaration order is presentation order; dependencies define execution.
    for node in graph["nodes"]:
        blocks = [KeyValue("Responsibility", node["responsibility"]),
            KeyValue("Activation", node["activation"]),
            KeyValue("Contributions", ", ".join(node["contributions"]) or "None"),
            KeyValue("Verifies", "; ".join(r["kind"] + ": " + r["name"] for r in node["verifies"]) or "None"),
            KeyValue("Required capabilities", ", ".join(node["requires"]) or "None"),
            KeyValue("Produced capabilities", ", ".join(node["provides"]) or "None"),
            KeyValue("Mutation domains", ", ".join(node["mutation_domains"]) or "None"),
            OrderedSteps(node["work"]),
            BulletList(["Acceptance: " + v for v in node["acceptance"]]), _checks(node["checks"]),
        ]
        if node["dependencies"]:
            blocks.append(Table(["Predecessor", "Capabilities", "Optional", "Allow non-applicable"], [
                [e["node"], ", ".join(e["capabilities"]), "yes" if e["optional"] else "no",
                 "yes" if e["allow_not_applicable"] else "no"] for e in node["dependencies"]]))
        sections.append(Section("Node: " + node["key"], blocks))
        if "remediation" in node:
            policy = node["remediation"]
            sections.append(Section("Bounded remediation: " + node["key"], [
                KeyValue("Generation budget", str(policy["generation_budget"])),
                KeyValue("Strategy budget", str(policy["strategy_budget"])),
                KeyValue("Mutation domains", ", ".join(policy["mutation_domains"])),
                KeyValue("Restores", ", ".join(policy["restores"])),
                KeyValue("Independent classification required", "yes" if policy["classification_verification"] else "no"),
                _checks(policy["regression_checks"]),
            ]))
            for strategy in policy["strategies"]:
                sections.append(Section("Repair strategy: " + node["key"] + " / " + strategy["key"], [
                    OrderedSteps(strategy["work"]), _checks(strategy["diagnostic_checks"])]))
    return sections


def _coverage(content):
    sections = []
    for node in content["node_coverage"]:
        blocks = []
        for row in node["coverage"]:
            blocks.append(KeyValue(row["kind"].title() + ": " + row["name"], row["status"]))
            for fact in row["verification"]:
                details = {k.replace("_", " "): fact[k] for k in (
                    "classification", "classification_assessment", "strategy_assessment", "not_applicable",
                ) if k in fact}
                blocks.append(Finding(title=fact["check_key"], evidence=fact["summary"],
                    conclusion=fact["state"], details=details))
        sections.append(Section("Observed coverage: " + node["node"], blocks))
    return sections


def present_report(report_type: str, content: Mapping[str, Any], report: Mapping[str, Any] | None = None) -> Document:
    """Reject unknown shapes; the store preserves the report if rendering fails."""
    validate_report(report_type, content)
    metadata = report or {}
    if report_type == "plan":
        title, sections = "Implementation Plan", _plan(content)
    else:
        title = "Implementation Result" if report_type == "result" else "Documentation Impact"
        sections = []
        if report_type == "result":
            sections.extend([Section("Outcome", [Paragraph(content["outcome"])]),
                Section("Changes", [Table(["Path", "Change"], [
                    [r["path"], r["summary"]] for r in content["changes"]])]
                    if content["changes"] else [Paragraph("No changes reported.")])])
        else:
            sections.extend([Section("Documentation findings", [Table(["Area", "Finding"], [
                [r["area"], r["summary"]] for r in content["findings"]])]
                if content["findings"] else [Paragraph("No affected areas reported.")]),
                _list("Recommendations", content["recommendations"])])
        sections.append(Section("Documentation impact", [Paragraph(content["documentation_impact"])]))
        sections.extend(_coverage(content))
    sections.extend(_artifact(content.get("artifact")))
    sections.extend([_list("Risks", content["risks"]), _list("Unresolved items", content["unresolved"])])
    labels = []
    if report_type == "plan" and metadata.get("review_policy"):
        labels.append(KeyValue("Review policy", str(metadata["review_policy"]).upper()))
    status = str(metadata.get("assembly_state", "finalized")).upper() + " — " + content["status"].upper()
    return Document(title, status=status, summary=content["summary"], sections=sections, metadata=labels)


def render_report(report_type: str, content: Mapping[str, Any], report: Mapping[str, Any] | None = None) -> str:
    return present_report(report_type, content, report).render()
