"""Source-backed harvest completeness validation."""
from __future__ import annotations

# Loaded through the public facade after Cortex initialization so the validator
# remains a focused domain module without creating a second server instance.
from cortex import (
    Any,
    HARVEST_PROJECT_DOCS,
    Path,
    _contained_path,
    _is_knowledge_harvest_task,
    re,
)

def validate_harvest_coverage_manifest(project_root: Path, task: dict[str, Any], gate: str) -> None:
    """Require harvest documentation to cover real, behavior-complete feature pages."""
    if gate not in {"documentation", "review", "close"} or not _is_knowledge_harvest_task(task):
        return
    missing_project_docs: list[str] = []
    project_doc_paths: dict[str, Path] = {}
    for relative in HARVEST_PROJECT_DOCS:
        project_doc = _contained_path(project_root, project_root / relative, "canonical harvest project document")
        if not project_doc.is_file() or project_doc.is_symlink():
            missing_project_docs.append(relative)
            continue
        if project_doc.stat().st_size > 512 * 1024:
            raise ValueError(f"canonical harvest project document exceeds the 512 KiB validation limit: {relative}")
        if len(re.findall(r"[A-Za-z0-9]+", project_doc.read_text(encoding="utf-8"))) < 12:
            raise ValueError(f"canonical harvest project document is shallow or empty: {relative}")
        project_doc_paths[relative] = project_doc
    if missing_project_docs:
        raise ValueError(
            "harvest canonical project documentation is incomplete; missing: "
            + ", ".join(missing_project_docs)
        )
    project_index = project_doc_paths["docs/project/index.md"]
    linked_project_docs: set[Path] = set()
    for raw_link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", project_index.read_text(encoding="utf-8")):
        target = raw_link.strip().strip("<>").split("#", 1)[0].strip()
        if target and "://" not in target and not target.startswith("/"):
            linked_project_docs.add((project_index.parent / target).resolve())
    unlinked_project_docs = [
        relative for relative in HARVEST_PROJECT_DOCS[1:]
        if project_doc_paths[relative].resolve() not in linked_project_docs
    ]
    if unlinked_project_docs:
        raise ValueError(
            "harvest project index must link every canonical project document; missing links: "
            + ", ".join(unlinked_project_docs)
        )
    path = _contained_path(
        project_root,
        project_root / "docs/features/index.md",
        "harvest coverage manifest",
    )
    if not path.is_file() or path.is_symlink():
        raise ValueError("harvest coverage manifest is missing: docs/features/index.md")
    if path.stat().st_size > 512 * 1024:
        raise ValueError("harvest coverage manifest exceeds the 512 KiB validation limit")
    raw_text = path.read_text(encoding="utf-8")
    text = raw_text.lower()
    missing = []
    expected_section_labels = (
        "Coverage matrix", "Inventory totals", "Unmapped surfaces",
        "Exclusions", "Known unknowns",
    )
    headings = {
        re.sub(r"\s+", " ", match.group(1).strip().rstrip("#").strip()).lower()
        for line in raw_text.splitlines()
        if (match := re.match(r"^#{2,6}\s+(.+?)\s*$", line.strip()))
    }
    absent_sections = [
        label for label in expected_section_labels if label.lower() not in headings
    ]
    if absent_sections:
        missing.append("sections (" + ", ".join(absent_sections) + ")")
    expected_header_labels = (
        "Feature", "Runtime owner", "Entry points", "Source evidence",
        "Documentation", "Verification", "Status",
    )
    expected_headers = tuple(label.lower() for label in expected_header_labels)
    table_headers: list[tuple[int, tuple[str, ...]]] = []
    lines = raw_text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if "|" not in line:
            continue
        cells = tuple(
            re.sub(r"\s+", " ", cell.strip().strip("`")).lower()
            for cell in line.strip().strip("|").split("|")
        )
        separator_cells = tuple(
            cell.strip() for cell in lines[index + 1].strip().strip("|").split("|")
        )
        if len(cells) != len(separator_cells) or not cells:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells):
            table_headers.append((index, cells))
    coverage_table = next(
        (
            (index, headers)
            for index, headers in table_headers
            if headers[:len(expected_headers)] == expected_headers
        ),
        None,
    )
    if coverage_table is None:
        missing.append(
            "matrix columns (expected exact header prefix: "
            + " | ".join(expected_header_labels)
            + ")"
        )
    if missing:
        raise ValueError(
            "harvest coverage manifest is shallow or incomplete; missing: " + ", ".join(missing)
        )
    header_index, coverage_headers = coverage_table
    coverage_rows: list[tuple[str, ...]] = []
    for line in lines[header_index + 2:]:
        if not line.strip().startswith("|"):
            break
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        if len(cells) != len(coverage_headers):
            raise ValueError(
                "harvest coverage matrix row has "
                f"{len(cells)} columns but the header has {len(coverage_headers)}"
            )
        coverage_rows.append(cells)
    if not coverage_rows:
        raise ValueError("harvest coverage matrix has no feature rows")
    documentation_links: set[Path] = set()
    row_errors: list[str] = []
    allowed_statuses = {"covered", "documented", "verified", "excluded"}
    for row_number, cells in enumerate(coverage_rows, 1):
        required_cells = cells[:len(expected_headers)]
        empty_labels = [
            expected_header_labels[index]
            for index, value in enumerate(required_cells)
            if not value.strip()
        ]
        if empty_labels:
            row_errors.append(f"row {row_number} has empty: {', '.join(empty_labels)}")
        status = re.sub(r"\s+", " ", required_cells[6].strip()).lower()
        if status not in allowed_statuses:
            row_errors.append(
                f"row {row_number} status must be covered, documented, verified, or excluded; got {required_cells[6]!r}"
            )
        documentation_cell = required_cells[4]
        row_links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", documentation_cell)
        if status != "excluded" and not row_links:
            row_errors.append(f"row {row_number} Documentation must link to a canonical feature page")
        row_has_canonical_index = status == "excluded"
        for raw_link in row_links:
            target = raw_link.strip().strip("<>").split("#", 1)[0].strip()
            if not target or "://" in target or target.startswith("/"):
                row_errors.append(f"row {row_number} Documentation has an invalid project-relative link")
                continue
            candidate = (path.parent / target).resolve()
            try:
                relative_feature_page = candidate.relative_to((project_root / "docs/features").resolve())
            except ValueError:
                row_errors.append(f"row {row_number} Documentation link leaves docs/features")
                continue
            if len(relative_feature_page.parts) >= 2 and relative_feature_page.name == "index.md":
                row_has_canonical_index = True
            documentation_links.add(candidate)
        if not row_has_canonical_index:
            row_errors.append(
                f"row {row_number} Documentation must include a canonical docs/features/<feature>/index.md link"
            )
    if row_errors:
        raise ValueError("harvest coverage matrix rows are invalid: " + "; ".join(row_errors))
    if not documentation_links:
        raise ValueError("harvest coverage manifest has no feature documentation links")
    missing_pages = sorted(
        item.relative_to(project_root).as_posix()
        for item in documentation_links
        if not item.is_file() or item.is_symlink()
    )
    if missing_pages:
        raise ValueError("harvest coverage manifest references missing feature pages: " + ", ".join(missing_pages))
    incomplete_rows = [
        line.strip() for line in text.splitlines()
        if line.lstrip().startswith("|") and re.search(r"\|\s*(?:partial|unknown|planned|unmapped)\b", line)
    ]
    if incomplete_rows:
        raise ValueError("harvest coverage manifest still has incomplete feature rows; finish every feature before reporting")
    required_page_topics = {
        "runtime": ("runtime", "owner"),
        "behavior": ("behavior", "workflow", "scenario", "logic"),
        "state/data": ("state", "data", "persistence"),
        "interfaces": ("interface", "entry point", "route", "api"),
        "failure/recovery": ("failure", "recovery", "error"),
        "verification": ("verification", "test"),
    }
    shallow_pages: list[str] = []
    for page in sorted(documentation_links):
        content = page.read_text(encoding="utf-8").lower()
        absent = [topic for topic, markers in required_page_topics.items() if not any(marker in content for marker in markers)]
        if absent:
            shallow_pages.append(f"{page.relative_to(project_root).as_posix()} ({', '.join(absent)})")
    if shallow_pages:
        raise ValueError("harvest feature pages lack required behavior coverage: " + "; ".join(shallow_pages))
