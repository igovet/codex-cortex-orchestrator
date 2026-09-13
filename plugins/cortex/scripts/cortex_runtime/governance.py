"""Bounded advisory governance projection; never a dispatch or acceptance gate."""
import re


def governance_snapshot(db, task_id):
    empty = dict(status="unset", mode=None, governance_id=None, report_id=None)
    try:
        row = db.execute(
            """SELECT g.id,g.mode,g.report_id FROM governance g
               JOIN editions e ON e.report_id=g.report_id
               WHERE g.task_id=? ORDER BY e.sequence DESC LIMIT 1""", (task_id,)
        ).fetchone()
        if row is None:
            return empty
        gid, mode, report = row
        if (mode not in {"minimal", "light", "full"}
                or not isinstance(gid, str) or re.fullmatch(r"g_[0-9a-f]{12}", gid) is None
                or not isinstance(report, str) or re.fullmatch(r"r_[0-9a-f]{12}", report) is None):
            return {**empty, "status": "unavailable"}
        return dict(status="selected", mode=mode, governance_id=gid, report_id=report)
    except Exception:
        # Optional projection failure must not roll back an otherwise valid call.
        # No exception text, private rationale or storage paths enter the result.
        return {**empty, "status": "unavailable"}
