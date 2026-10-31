from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from connectiq.models import ActivityEvent
from connectiq.services import change_status, correct_date


@pytest.mark.parametrize("statement", ["UPDATE activity_events SET kind = 'tampered'", "DELETE FROM activity_events"])
def test_audit_cannot_be_mutated(session, prospect, statement):
    with pytest.raises(DBAPIError, match="append-only"):
        with session.begin_nested():
            session.execute(text(statement))
    assert session.scalar(select(ActivityEvent.kind)) == "prospect_added"


def test_multiple_corrections_keep_every_version(session, prospect):
    initial = datetime(2026, 1, 6, 10, tzinfo=timezone.utc)
    change_status(session, 1, prospect.id, "Request Sent", initial)
    for days in (1, 2):
        correct_date(session, 1, prospect.id, "request_sent_at", initial - timedelta(days=days), f"Correction {days}")
    events = list(session.scalars(select(ActivityEvent).order_by(ActivityEvent.id)))
    assert len(events) == 4
    assert [e.kind for e in events] == ["prospect_added", "request_sent", "date_corrected", "date_corrected"]
