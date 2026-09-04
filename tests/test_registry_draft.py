"""Incremental extraction is durable, task-bound and never completion proof."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from cortex_runtime.registry_draft import RegistryDraft, RegistryDraftError, GUARDS
from cortex_runtime.obligation_sources import snapshot
from cortex_runtime import submission_queue
from cortex_runtime.domain_api import record_steering
from test_obligation_integrity import task_store

KEY = b"d" * 32


@pytest.fixture
def draft_case(task_store):
    store, task, ref, original = task_store
    def initial(c):
        source = submission_queue.capture(c, session="root", turn="first", text=original, key=KEY)
        submission_queue.consume(c, reference=source.reference, session="root", task_id=task, purpose="initial", key=KEY)
        draft = RegistryDraft(c, task_id=task, session="root", key=KEY).begin(source.reference)
        return source, draft
    source, draft = store._write(initial)
    def call(operation, *args, session="root"):
        return store._write(lambda c: getattr(RegistryDraft(c, task_id=task, session=session, key=KEY), operation)(*args))
    return store, task, ref, original, source, draft, call


def test_scalar_operations_resume_and_reconcile_without_changing_obligations(draft_case):
    store, task, _, original, source, draft, call = draft_case
    before = store._read(lambda c: snapshot(c, task))
    assert call("begin", source.reference) == draft
    assert not call("inspect", draft)["structurally_complete"]
    requirement = call("add_requirement", draft, "Preserve every obligation")
    assert call("add_requirement", draft, "Preserve every obligation") == requirement
    acceptance = call("add_criterion", draft, requirement, "acceptance", "No weakening")
    verification = call("add_criterion", draft, requirement, "verification", "No weakening")
    assert acceptance != verification
    for subject in (requirement, acceptance, verification):
        call("link_source", draft, subject, 0, len(original))
        call("link_source", draft, subject, 0, len(original))
    observed = call("inspect", draft)
    assert observed["structurally_complete"]
    assert observed["semantic_review"] == "unverified"
    assert observed["execution_authority"] is False
    assert len(observed["subjects"]) == 3
    assert observed == call("inspect", draft)
    assert before == store._read(lambda c: snapshot(c, task))
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM registry_draft_links").fetchone()[0]) == 3


def test_incomplete_ranges_and_criteria_remain_visible(draft_case):
    _, _, _, original, _, draft, call = draft_case
    requirement = call("add_requirement", draft, "First sentence only")
    criterion = call("add_criterion", draft, requirement, "acceptance", "Keep criteria")
    call("link_source", draft, requirement, 0, original.index("\n"))
    result = call("inspect", draft)
    assert result["uncovered"]
    assert result["unmapped"] == ((requirement, criterion),)
    assert not result["structurally_complete"]
    before = result["content_digest"]
    call("add_criterion", draft, requirement, "verification", "Check every source sentence")
    assert call("inspect", draft)["content_digest"] != before


def test_foreign_session_or_draft_cannot_gain_source_authority(draft_case):
    store, task, _, _, source, draft, call = draft_case
    with pytest.raises(submission_queue.SubmissionError, match="source_not_available"):
        call("begin", source.reference, session="foreign")
    with pytest.raises(RegistryDraftError, match="draft_unavailable"):
        call("add_requirement", "draft_unknown", "Do something")
    with pytest.raises(RegistryDraftError, match="draft_unavailable"):
        store._read(lambda c: RegistryDraft(c, task_id="foreign", session="root", key=KEY).inspect(draft))


@pytest.mark.parametrize("purpose", ["decision", "information"])
def test_nonchange_messages_are_never_requirement_authority(draft_case, purpose):
    store, task, _, _, _, _, call = draft_case
    def capture(c):
        source = submission_queue.capture(c, session="root", turn="later", text="Show progress", key=KEY)
        submission_queue.consume(c, reference=source.reference, session="root", task_id=task, purpose=purpose, key=KEY)
        return source.reference
    source = store._write(capture)
    with pytest.raises(RegistryDraftError, match="requirement_source_unavailable"):
        call("begin", source)


def test_steering_invalidates_old_draft_without_rewriting_it(draft_case):
    store, _, ref, _, _, draft, call = draft_case
    call("add_requirement", draft, "Preserve obligations")
    prior = store._read(lambda c: [tuple(r) for r in c.execute("SELECT * FROM registry_draft_subjects")])
    record_steering(task_ref=ref, response_original="Also support keyboard input", user_language="en",
                    retire=[], add=[{"outcome": "Keyboard", "acceptance": [], "constraints": [], "verification": []}])
    with pytest.raises(RegistryDraftError, match="draft_registry_stale"):
        call("add_requirement", draft, "Silently weaken")
    assert prior == store._read(lambda c: [tuple(r) for r in c.execute("SELECT * FROM registry_draft_subjects")])


def test_invalid_parent_range_or_text_leaves_draft_unchanged(draft_case):
    store, _, _, _, _, draft, call = draft_case
    requirement = call("add_requirement", draft, "Preserve requirements")
    criterion = call("add_criterion", draft, requirement, "verification", "Check history")
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(RegistryDraftError, match="draft_parent_not_requirement"):
        call("add_criterion", draft, criterion, "acceptance", "Invalid nested criterion")
    with pytest.raises(RegistryDraftError, match="draft_text_invalid"):
        call("add_requirement", draft, " " * 10)
    with pytest.raises(ValueError, match="source_range_invalid"):
        call("link_source", draft, requirement, True, 3)
    with pytest.raises(RegistryDraftError, match="draft_subject_unavailable"):
        call("link_source", draft, "other", 0, 3)
    assert before == store._read(lambda c: list(c.iterdump()))


def test_crash_rolls_back_only_uncommitted_scalar_operation(draft_case):
    store, task, _, _, _, draft, call = draft_case
    first = call("add_requirement", draft, "Already durable")
    def interrupted(c):
        RegistryDraft(c, task_id=task, session="root", key=KEY).add_requirement(draft, "Not committed")
        raise RuntimeError("injected crash")
    with pytest.raises(RuntimeError, match="injected crash"):
        store._write(interrupted)
    assert [s["subject_ref"] for s in call("inspect", draft)["subjects"]] == [first]


def test_draft_history_guards_are_required_and_immutable(draft_case):
    store, _, _, _, _, draft, call = draft_case
    call("add_requirement", draft, "Retain history")
    def mutate(c):
        actual = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert GUARDS <= actual
        for table in ("registry_drafts", "registry_draft_subjects"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                c.execute(f"DELETE FROM {table}")
    store._write(mutate)


def test_sql_relation_guards_reject_unconsumed_source_and_foreign_parent(draft_case):
    store, task, _, _, _, draft, call = draft_case
    requirement = call("add_requirement", draft, "Keep scope")
    def attack(c):
        source = submission_queue.capture(c, session="root", turn="new", text="New source", key=KEY)
        with pytest.raises(sqlite3.IntegrityError, match="source is not task-bound"):
            c.execute("INSERT INTO registry_drafts VALUES ('foreign',?,?,?)", (task, source.reference, "untrusted"))
        with pytest.raises(sqlite3.IntegrityError, match="parent is not a scoped"):
            c.execute("INSERT INTO registry_draft_subjects VALUES ('child','foreign',?,'acceptance','No leak')", (requirement,))
        with pytest.raises(sqlite3.IntegrityError, match="link is not scoped"):
            c.execute("INSERT INTO registry_draft_links VALUES ('foreign',?,0,1)", (requirement,))
    store._write(attack)


def test_extraction_correction_is_versioned_without_weakening_active_registry(draft_case):
    store, task, _, original, _, draft, call = draft_case
    active = store._read(lambda c: snapshot(c, task))
    subject = call("add_requirement", draft, "Incomplete interpretation")
    call("link_source", draft, subject, 0, len(original))
    first = call("inspect", draft)
    call("amend_text", draft, subject, "Preserve all source requirements and criteria", first["content_digest"])
    call("amend_text", draft, subject, "Preserve all source requirements and criteria", first["content_digest"])
    corrected = call("inspect", draft)
    assert corrected["content_digest"] != first["content_digest"]
    assert corrected["mapping_digest"] == first["mapping_digest"]
    assert corrected["semantic_review"] == "unverified"
    assert corrected["subjects"][0]["text"] == "Preserve all source requirements and criteria"
    assert store._read(lambda c: c.execute("SELECT text FROM registry_draft_subjects").fetchone()[0]) == "Incomplete interpretation"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM registry_draft_amendments").fetchone()[0]) == 1
    assert active == store._read(lambda c: snapshot(c, task))
    def rewrite(c):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("DELETE FROM registry_draft_amendments")
    store._write(rewrite)


def test_late_reconciliation_never_overwrites_newer_extraction(draft_case):
    _, _, _, _, _, draft, call = draft_case
    subject = call("add_requirement", draft, "Original extraction")
    first = call("inspect", draft)["content_digest"]
    call("amend_text", draft, subject, "Correction one", first)
    second = call("inspect", draft)["content_digest"]
    call("amend_text", draft, subject, "Correction two", second)
    latest = call("inspect", draft)
    call("amend_text", draft, subject, "Correction one", first)
    assert call("inspect", draft) == latest
    with pytest.raises(RegistryDraftError, match="draft_amendment_conflict"):
        call("amend_text", draft, subject, "Other old correction", first)
    with pytest.raises(RegistryDraftError, match="draft_content_stale"):
        call("amend_text", draft, subject, "Unobserved correction", "unknown")
    assert call("inspect", draft) == latest


def test_parallel_amendments_commit_only_one_current_base(draft_case):
    store, _, _, _, _, draft, call = draft_case
    subject = call("add_requirement", draft, "Original")
    base = call("inspect", draft)["content_digest"]
    def amend(index):
        try:
            call("amend_text", draft, subject, f"Correction {index}", base)
            return "committed"
        except RegistryDraftError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(amend, range(4)))
    assert results.count("committed") == 1
    assert results.count("draft_amendment_conflict") == 3
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM registry_draft_amendments").fetchone()[0]) == 1


def test_missing_draft_guard_is_not_migrated_or_ignored(draft_case):
    from cortex_runtime.v12_store import V12Store, V12StoreError
    from cortex_runtime.v12_maintenance import health
    store, task, _, _, _, _, _ = draft_case
    store._write(lambda c: c.execute("DROP TRIGGER registry_draft_parent_bound"))
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(V12StoreError) as failure:
        V12Store(store.project_root)
    assert failure.value.code == "schema_unsupported"
    assert not health(task_id=task)["healthy"]
    assert before == store._read(lambda c: list(c.iterdump()))
