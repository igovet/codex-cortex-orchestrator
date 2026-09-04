"""Durable input ordering and one-shot consumption, not host-adapter evidence."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from cortex_runtime import submission_queue as queue
from test_obligation_integrity import task_store


KEY = b"k" * 32


def submit(store, text, session="root"):
    return store._write(lambda c: queue.capture(c, session=session, turn="active-turn", text=text, key=KEY))


def use(store, task_id, source, purpose="change", session="root"):
    return store._write(lambda c: queue.consume(c, reference=source.reference, session=session,
                       task_id=task_id, purpose=purpose, key=KEY))


def bind_initial(store, task_id, original):
    source = submit(store, original)
    assert not use(store, task_id, source, purpose="initial")


def test_repeated_messages_are_not_overwritten_or_merged(task_store):
    store, task_id, _, original = task_store
    bind_initial(store, task_id, original)
    text = "  Уточнение 🌍\nСохранить данные.\n"
    first, second = submit(store, text), submit(store, text)
    assert first.reference != second.reference
    assert second.arrival == first.arrival + 1
    assert store._read(lambda c: queue.pending(c, session="root")) == (first.reference, second.reference)
    observed = store._read(lambda c: queue.read(c, reference=first.reference, session="root", key=KEY))
    assert observed.text.encode() == text.encode()
    with pytest.raises(queue.SubmissionError, match="earlier_source_pending"):
        use(store, task_id, second)
    assert use(store, task_id, first) is False
    assert use(store, task_id, first) is True
    assert use(store, task_id, second) is False
    assert store._read(lambda c: queue.pending(c, session="root")) == ()


def test_consumed_source_cannot_be_reinterpreted_as_new_authority(task_store):
    store, task_id, _, original = task_store
    bind_initial(store, task_id, original)
    source = submit(store, "Show progress.")
    use(store, task_id, source, purpose="information")
    with pytest.raises(queue.SubmissionError, match="already_consumed"):
        use(store, task_id, source, purpose="decision")
    with pytest.raises(queue.SubmissionError, match="not_available"):
        use(store, task_id, source, session="foreign")


def test_wrong_key_never_reads_source_body(task_store):
    store, _, _, _ = task_store
    source = submit(store, "private source")
    with pytest.raises(queue.SubmissionError, match="signature_invalid"):
        store._read(lambda c: queue.read(c, reference=source.reference, session="root", key=b"x" * 32))


def test_parallel_messages_keep_distinct_durable_arrival_order(task_store):
    store, task_id, _, original = task_store
    bind_initial(store, task_id, original)
    with ThreadPoolExecutor(max_workers=4) as pool:
        sources = list(pool.map(lambda i: submit(store, f"Requirement {i}"), range(12)))
    ordered = sorted(sources, key=lambda item: item.arrival)
    assert len({s.reference for s in ordered}) == 12
    assert store._read(lambda c: queue.pending(c, session="root")) == tuple(s.reference for s in ordered)
    for source in ordered:
        assert not use(store, task_id, source)


def test_rolled_back_capture_leaves_no_partial_submission(task_store):
    store, _, _, _ = task_store
    def failed(c):
        queue.capture(c, session="root", turn="turn", text="Change", key=KEY)
        raise RuntimeError("injected before commit")
    with pytest.raises(RuntimeError, match="injected"):
        store._write(failed)
    assert store._read(lambda c: queue.pending(c, session="root")) == ()


def test_source_and_consumption_rows_are_immutable(task_store):
    store, task_id, _, original = task_store
    bind_initial(store, task_id, original)
    source = submit(store, "Keep requirements")
    use(store, task_id, source)
    def modify(c):
        for sql in ("UPDATE source_submissions SET body='weakened'", "DELETE FROM source_submissions",
                    "UPDATE source_consumptions SET purpose='decision'", "DELETE FROM source_consumptions"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                c.execute(sql)
    store._write(modify)


def test_initial_binding_requires_exact_original_not_a_summary(task_store):
    store, task_id, _, original = task_store
    summary = submit(store, "Keep requirements")
    with pytest.raises(queue.SubmissionError, match="original_source_mismatch"):
        use(store, task_id, summary, purpose="initial")
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM source_consumptions").fetchone()[0]) == 0
    # A separate session has its own ordered inbox; original bytes are not trimmed.
    exact = submit(store, original, session="initial-root")
    assert use(store, task_id, exact, purpose="initial", session="initial-root") is False
    assert use(store, task_id, exact, purpose="initial", session="initial-root") is True
    second = submit(store, original, session="initial-root")
    with pytest.raises(queue.SubmissionError, match="initial_source_already_bound"):
        use(store, task_id, second, purpose="initial", session="initial-root")


def test_signed_message_cannot_be_copied_into_another_project(task_store):
    from cortex_runtime.v12_store import V12Store
    store, _, _, _ = task_store
    source = submit(store, "Keep requirements")
    copied = store._read(lambda c: tuple(c.execute("SELECT * FROM source_submissions WHERE source_ref=?",
                                                 (source.reference,)).fetchone()))
    project = store.project_root.parent / "other-project"
    project.mkdir()
    other = V12Store(project)
    other._write(lambda c: c.execute("INSERT INTO source_submissions VALUES (?,?,?,?,?,?)", copied))
    with pytest.raises(queue.SubmissionError, match="signature_invalid"):
        other._read(lambda c: queue.read(c, reference=source.reference, session="root", key=KEY))


@pytest.mark.parametrize("purpose", ["change", "decision", "information"])
def test_noninitial_input_requires_original_session_binding(task_store, purpose):
    store, task_id, _, original = task_store
    unbound = submit(store, "New instruction", session="other")
    with pytest.raises(queue.SubmissionError, match="initial_source_required"):
        use(store, task_id, unbound, purpose=purpose, session="other")
    bind_initial(store, task_id, original)
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(queue.SubmissionError, match="source_task_session_mismatch"):
        use(store, task_id, unbound, purpose=purpose, session="other")
    assert store._read(lambda c: list(c.iterdump())) == before
    assert store._read(lambda c: queue.pending(c, session="other")) == (unbound.reference,)
