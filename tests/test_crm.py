from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from connectiq.models import ActivityEvent, Company, FollowUpReminder, Prospect, User
from connectiq.services import (add_interaction, add_reminder, archive_prospect, change_status, complete_reminder,
    correct_date, delete_company, restore_prospect, save_company, save_prospect)
from connectiq.validation import DuplicateError, ValidationError, aware, profile_url, website_url

SENT = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
ACCEPTED = SENT + timedelta(days=3)


def test_company_create_edit_duplicate_exact(session):
    c = save_company(session, {"name": " Exact name "})
    assert c.name == " Exact name " and c.verification_status == "Unverified"
    with pytest.raises(DuplicateError):
        save_company(session, {"name": " Exact name "})
    save_company(session, {"name": "exact name"})
    save_company(session, {"name": " Exact name ", "website": "https://example.test", "notes": "Manual research"}, c.id)
    assert c.notes == "Manual research"


def test_delete_company_requires_confirmation_and_protects_history(session, company, prospect):
    with pytest.raises(ValidationError):
        delete_company(session, company.id)
    with pytest.raises(ValidationError):
        delete_company(session, company.id, True)
    empty = save_company(session, {"name": "Empty test company"})
    delete_company(session, empty.id, True)
    assert session.get(Company, empty.id) is None


def test_prospect_creation_and_edit_audit(session, prospect):
    assert prospect.status == "Discovered"
    data = dict(full_name="Asha Rao", company_id=prospect.company_id, linkedin_url=prospect.linkedin_url,
                designation="Senior RTL Design Engineer", location="Pune", discovery_date=date(2026, 1, 1), priority="High")
    updated = save_prospect(session, 1, data, prospect.id)
    assert updated.designation == data["designation"] and updated.priority == "High"
    kinds = list(session.scalars(select(ActivityEvent.kind)))
    assert kinds == ["prospect_added", "prospect_edited"]


def test_duplicate_linkedin_and_likely_name(session, prospect):
    data = dict(full_name="Different Name", company_id=prospect.company_id,
                linkedin_url="http://in.linkedin.com/in/ASHA-RAO-TEST/?tracking=x#fragment")
    with pytest.raises(DuplicateError):
        save_prospect(session, 1, data)
    data.update(full_name="  ASHA   RAO ", linkedin_url="linkedin.com/in/second-person-test")
    with pytest.raises(DuplicateError):
        save_prospect(session, 1, data)
    assert save_prospect(session, 1, data, allow_likely_duplicate=True)


@pytest.mark.parametrize("value", ["javascript:alert(1)", "https://linkedin.com.evil.test/in/a", "https://evil.test/in/a", "https://www.linkedin.com/company/a", "https://user@linkedin.com/in/a", "https://linkedin.com:44/in/a", "https://linkedin.com/in/a/other", "https://linkedin.com/in/a%2Fb"])
def test_reject_unsafe_profile_urls(value):
    with pytest.raises(ValidationError):
        profile_url(value)


def test_url_normalization():
    assert profile_url("in.linkedin.com/in/Name-123/?trk=x") == "https://www.linkedin.com/in/name-123"
    with pytest.raises(ValidationError):
        website_url("javascript:alert(1)")


def test_request_accept_timestamps_and_no_double_count(session, prospect):
    change_status(session, 1, prospect.id, "Request Sent", SENT)
    change_status(session, 1, prospect.id, "Request Sent", SENT)
    change_status(session, 1, prospect.id, "Accepted", ACCEPTED)
    assert aware(prospect.request_sent_at) == SENT
    assert aware(prospect.accepted_at) == ACCEPTED
    assert session.scalar(select(func.count()).select_from(ActivityEvent).where(ActivityEvent.kind == "request_sent")) == 1
    assert session.scalar(select(func.count()).select_from(ActivityEvent).where(ActivityEvent.kind == "accepted")) == 1


@pytest.mark.parametrize("status", ["To Review", "Shortlisted", "Follow-up Required"])
def test_non_connection_status_transitions(session, prospect, status):
    change_status(session, 1, prospect.id, status)
    event = session.scalar(select(ActivityEvent).order_by(ActivityEvent.id.desc()))
    assert (event.from_status, event.to_status, prospect.status) == ("Discovered", status, status)


def test_invalid_chronology_and_status(session, prospect):
    with pytest.raises(ValidationError):
        change_status(session, 1, prospect.id, "Invalid")
    with pytest.raises(ValidationError):
        change_status(session, 1, prospect.id, "Accepted", ACCEPTED)
    with pytest.raises(ValidationError):
        change_status(session, 1, prospect.id, "Request Sent", datetime.now(timezone.utc) + timedelta(days=1))
    change_status(session, 1, prospect.id, "Request Sent", SENT)
    with pytest.raises(ValidationError):
        change_status(session, 1, prospect.id, "Accepted", SENT - timedelta(days=1))


def test_date_correction_preserves_original_event(session, prospect):
    change_status(session, 1, prospect.id, "Request Sent", SENT)
    original = session.scalar(select(ActivityEvent).where(ActivityEvent.kind == "request_sent"))
    corrected = SENT - timedelta(days=1)
    correct_date(session, 1, prospect.id, "request_sent_at", corrected, "Correcting my calendar entry")
    assert aware(original.effective_at) == SENT
    assert aware(prospect.request_sent_at) == corrected
    audit = session.scalar(select(ActivityEvent).where(ActivityEvent.kind == "date_corrected"))
    assert audit.related_event_id == original.id and audit.details["old"] == SENT.isoformat()


def test_correction_requires_reason_and_chronology(session, prospect):
    change_status(session, 1, prospect.id, "Request Sent", SENT)
    change_status(session, 1, prospect.id, "Accepted", ACCEPTED)
    with pytest.raises(ValidationError):
        correct_date(session, 1, prospect.id, "request_sent_at", ACCEPTED + timedelta(days=1), "Wrong")
    with pytest.raises(ValidationError):
        correct_date(session, 1, prospect.id, "accepted_at", ACCEPTED, "")


def test_new_request_cycle(session, prospect):
    change_status(session, 1, prospect.id, "Request Sent", SENT)
    change_status(session, 1, prospect.id, "Withdrawn", SENT + timedelta(days=3))
    change_status(session, 1, prospect.id, "Request Sent", SENT + timedelta(days=5))
    change_status(session, 1, prospect.id, "Accepted", SENT + timedelta(days=7))
    assert session.scalar(select(func.count()).select_from(ActivityEvent).where(ActivityEvent.kind == "request_sent")) == 2


def test_archive_retains_events_and_duplicate_guard(session, prospect):
    with pytest.raises(ValidationError):
        archive_prospect(session, 1, prospect.id)
    archive_prospect(session, 1, prospect.id, True)
    assert prospect.deleted_at
    assert session.get(Prospect, prospect.id)
    with pytest.raises(ValidationError):
        change_status(session, 1, prospect.id, "To Review")
    restore_prospect(session, 1, prospect.id)
    assert prospect.deleted_at is None


def test_owner_isolation(session, prospect, password_hash):
    other = User(email="second@example.test", password_hash=password_hash)
    session.add(other)
    session.flush()
    with pytest.raises(ValidationError):
        change_status(session, other.id, prospect.id, "Accepted")


def test_followup_reminder_completion_is_idempotent(session, prospect):
    reminder = add_reminder(session, 1, prospect.id, date(2026, 1, 9), "Review the response manually")
    assert reminder.completed_at is None
    complete_reminder(session, 1, reminder.id)
    complete_reminder(session, 1, reminder.id)
    assert session.get(FollowUpReminder, reminder.id).completed_at
    assert session.scalar(select(func.count()).select_from(ActivityEvent).where(ActivityEvent.kind == "reminder_completed")) == 1


def test_interaction_notes_keep_history(session, prospect):
    add_interaction(session, 1, prospect.id, "Met at a conference", SENT)
    add_interaction(session, 1, prospect.id, "Discussed verification", ACCEPTED)
    assert aware(prospect.last_interaction_at) == ACCEPTED
    assert session.scalar(select(func.count()).select_from(ActivityEvent).where(ActivityEvent.kind == "interaction")) == 2


def test_interaction_correction_targets_latest_time_not_last_entry(session, prospect):
    newest = add_interaction(session, 1, prospect.id, "Recent interaction", ACCEPTED)
    older = add_interaction(session, 1, prospect.id, "Backfilled older interaction", SENT)
    correct_date(session, 1, prospect.id, "last_interaction_at", SENT - timedelta(days=1), "Correcting the recent entry")
    correction = session.scalar(select(ActivityEvent).where(ActivityEvent.kind == "date_corrected"))
    assert correction.related_event_id == newest.id and correction.related_event_id != older.id
    assert aware(prospect.last_interaction_at) == SENT
