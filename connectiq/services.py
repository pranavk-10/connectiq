"""Transactional operations. Callers own commits; no operation silently changes history."""
import hashlib
from datetime import date, datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

from sqlalchemy import func, select

from connectiq.constants import PRIORITIES, STATUSES, VERIFICATION
from connectiq.models import ActivityEvent, Company, FollowUpReminder, Prospect, SearchHistory, UserSettings, utcnow
from connectiq.validation import DuplicateError, ValidationError, aware, name_key, past_instant, profile_url, text_value, website_url


def owned_prospect(session, user_id, prospect_id, include_deleted=False):
    query = select(Prospect).where(Prospect.id == prospect_id, Prospect.user_id == user_id).with_for_update()
    if not include_deleted:
        query = query.where(Prospect.deleted_at.is_(None))
    prospect = session.scalar(query)
    if not prospect:
        raise ValidationError("Prospect not found.")
    return prospect


def record_event(session, user_id, kind, prospect=None, effective_at=None, **kwargs):
    event = ActivityEvent(user_id=user_id, prospect_id=prospect.id if prospect else None,
                          kind=kind, effective_at=effective_at or utcnow(), **kwargs)
    session.add(event)
    session.flush()
    return event


def save_company(session, data, company_id=None):
    name = text_value(data.get("name", ""), "Company name", 300, True, preserve=True)
    existing = session.scalar(select(Company).where(Company.name == name))
    if existing and existing.id != company_id:
        raise DuplicateError("This exact company name already exists.")
    company = session.get(Company, company_id) if company_id else Company()
    if company is None:
        raise ValidationError("Company not found.")
    verification = data.get("verification_status", "Unverified")
    if verification not in VERIFICATION:
        raise ValidationError("Choose a valid verification status.")
    values = dict(name=name, website=website_url(data.get("website", "")), verification_status=verification)
    for field in ("locations", "domains", "notes"):
        values[field] = text_value(data.get(field, ""), field.title(), 10000)
    for key, value in values.items():
        setattr(company, key, value)
    session.add(company)
    session.flush()
    return company


def delete_company(session, company_id, confirmed=False):
    if not confirmed:
        raise ValidationError("Confirm the company deletion.")
    if session.scalar(select(func.count()).select_from(Prospect).where(Prospect.company_id == company_id)):
        raise ValidationError("This company has prospect history. Reassign prospects before deleting; archived history is retained.")
    company = session.get(Company, company_id)
    if company:
        session.delete(company)
        session.flush()


def save_prospect(session, user_id, data, prospect_id=None, allow_likely_duplicate=False):
    p = owned_prospect(session, user_id, prospect_id) if prospect_id else None
    full_name = text_value(data.get("full_name", ""), "Full name", 200, True)
    url = profile_url(data.get("linkedin_url", ""))
    try:
        company_id = int(data.get("company_id", 0))
    except (ValueError, TypeError):
        raise ValidationError("Choose a company.") from None
    if not session.get(Company, company_id):
        raise ValidationError("Choose an existing company.")
    duplicate = session.scalar(select(Prospect).where(Prospect.user_id == user_id, Prospect.linkedin_url == url))
    if duplicate and duplicate.id != prospect_id:
        raise DuplicateError("This LinkedIn profile already exists, including archived prospects. Restore it from the archive if needed.")
    likely = session.scalar(select(Prospect).where(Prospect.user_id == user_id, Prospect.company_id == company_id,
        Prospect.name_key == name_key(full_name), Prospect.id != (prospect_id or 0), Prospect.deleted_at.is_(None)))
    if likely and not allow_likely_duplicate:
        raise DuplicateError("A similar name exists at this company. Review it, then confirm they are different people to save.")
    from connectiq.config import get_config
    tz = get_setting(session, user_id, "preferences", {}).get("timezone", get_config().timezone)
    today = datetime.now(ZoneInfo(tz)).date()
    discovery = data.get("discovery_date", today)
    if not isinstance(discovery, date) or discovery > today:
        raise ValidationError("Discovery date must be today or earlier.")
    priority = data.get("priority", "Medium")
    if priority not in PRIORITIES:
        raise ValidationError("Choose a valid priority.")
    values = dict(full_name=full_name, name_key=name_key(full_name), company_id=company_id,
                  linkedin_url=url, discovery_date=discovery, priority=priority)
    for field in ("designation", "location", "category", "notes"):
        values[field] = text_value(data.get(field, ""), field.title(), 10000 if field == "notes" else (100 if field == "category" else 200))
    changes = {k: {"old": str(getattr(p, k)), "new": str(v)} for k, v in values.items() if p and getattr(p, k) != v}
    if p is None:
        p = Prospect(user_id=user_id, status="Discovered", **values)
        session.add(p)
        session.flush()
        record_event(session, user_id, "prospect_added", p, to_status="Discovered")
    else:
        for key, value in values.items():
            setattr(p, key, value)
        if changes:
            record_event(session, user_id, "prospect_edited", p, details={"changes": changes})
    return p


def latest_event(session, p, kind):
    return session.scalar(select(ActivityEvent).where(ActivityEvent.prospect_id == p.id,
                          ActivityEvent.kind == kind).order_by(ActivityEvent.id.desc()).limit(1))


def interaction_instants(session, p):
    events = list(session.scalars(select(ActivityEvent).where(ActivityEvent.prospect_id == p.id)
                                  .order_by(ActivityEvent.id)))
    corrections = {e.related_event_id: aware(e.effective_at) for e in events if e.kind == "date_corrected"}
    return [(e, corrections.get(e.id, aware(e.effective_at))) for e in events if e.kind == "interaction"]


def change_status(session, user_id, prospect_id, status, effective_at=None):
    if status not in STATUSES:
        raise ValidationError("Choose a valid connection status.")
    p = owned_prospect(session, user_id, prospect_id)
    if p.status == status:
        return p
    moment = past_instant(effective_at or utcnow())
    old, kind, related = p.status, "status_changed", None
    if status == "Request Sent":
        if p.request_sent_at and old not in ("Withdrawn", "Declined"):
            raise ValidationError("This request is already recorded. Correct its date instead, or withdraw/decline it before recording a new request.")
        prior = latest_event(session, p, "request_sent")
        if prior and moment < aware(p.request_sent_at):
            raise ValidationError("A new request cannot predate the previous request.")
        p.request_sent_at, p.accepted_at, kind = moment, None, "request_sent"
    elif status == "Accepted":
        if not p.request_sent_at:
            raise ValidationError("Record the actual request date before marking it accepted.")
        if moment < aware(p.request_sent_at):
            raise ValidationError("Acceptance cannot precede the request.")
        if not p.accepted_at:
            p.accepted_at, kind = moment, "accepted"
            related = latest_event(session, p, "request_sent").id
    elif status in ("Declined", "Withdrawn") and not p.request_sent_at:
        raise ValidationError("Record a request before marking it declined or withdrawn.")
    p.status = status
    record_event(session, user_id, kind, p, moment, from_status=old, to_status=status, related_event_id=related)
    return p


def correct_date(session, user_id, prospect_id, field, value, reason):
    if field not in ("request_sent_at", "accepted_at", "last_interaction_at"):
        raise ValidationError("This date cannot be corrected here.")
    reason = text_value(reason, "Correction reason", 2000, True)
    p = owned_prospect(session, user_id, prospect_id)
    moment = past_instant(value)
    old = getattr(p, field)
    if old is None:
        raise ValidationError("Record the activity first before correcting its date.")
    if field == "request_sent_at" and p.accepted_at and moment > aware(p.accepted_at):
        raise ValidationError("Request cannot be after acceptance.")
    if field == "accepted_at" and p.request_sent_at and moment < aware(p.request_sent_at):
        raise ValidationError("Acceptance cannot precede the request.")
    kind = {"request_sent_at": "request_sent", "accepted_at": "accepted", "last_interaction_at": "interaction"}[field]
    target = latest_event(session, p, kind)
    if field == "last_interaction_at":
        interactions = interaction_instants(session, p)
        target = max(interactions, key=lambda item: item[1])[0] if interactions else None
    if field == "request_sent_at" and target:
        previous = session.scalar(select(ActivityEvent).where(ActivityEvent.prospect_id == p.id,
            ActivityEvent.kind == "request_sent", ActivityEvent.id < target.id).order_by(ActivityEvent.id.desc()))
        if previous:
            corrected = session.scalar(select(ActivityEvent).where(ActivityEvent.kind == "date_corrected",
                ActivityEvent.related_event_id == previous.id).order_by(ActivityEvent.id.desc()))
            if moment < aware((corrected or previous).effective_at):
                raise ValidationError("The request cannot predate the previous request cycle.")
    setattr(p, field, moment)
    record_event(session, user_id, "date_corrected", p, moment, related_event_id=target.id if target else None,
                 details={"field": field, "old": aware(old).isoformat(), "new": moment.isoformat(), "reason": reason})
    if field == "last_interaction_at":
        p.last_interaction_at = max(value for _, value in interaction_instants(session, p))


def archive_prospect(session, user_id, prospect_id, confirmed=False):
    if not confirmed:
        raise ValidationError("Confirm the prospect deletion.")
    p = owned_prospect(session, user_id, prospect_id)
    p.deleted_at = utcnow()
    record_event(session, user_id, "prospect_archived", p)


def restore_prospect(session, user_id, prospect_id):
    p = owned_prospect(session, user_id, prospect_id, include_deleted=True)
    p.deleted_at = None
    record_event(session, user_id, "prospect_restored", p)


def add_interaction(session, user_id, prospect_id, note, effective_at=None):
    p = owned_prospect(session, user_id, prospect_id)
    note = text_value(note, "Interaction note", 10000, True)
    moment = past_instant(effective_at or utcnow())
    if not p.last_interaction_at or moment > aware(p.last_interaction_at):
        p.last_interaction_at = moment
    return record_event(session, user_id, "interaction", p, moment, details={"note": note})


def add_reminder(session, user_id, prospect_id, due_date, note):
    p = owned_prospect(session, user_id, prospect_id)
    if not isinstance(due_date, date):
        raise ValidationError("Choose a reminder date.")
    reminder = FollowUpReminder(user_id=user_id, prospect_id=p.id, due_date=due_date,
                                note=text_value(note, "Reminder", 2000, True))
    session.add(reminder)
    session.flush()
    record_event(session, user_id, "reminder_created", p, details={"reminder_id": reminder.id, "due_date": due_date.isoformat(), "note": reminder.note})
    return reminder


def complete_reminder(session, user_id, reminder_id):
    reminder = session.scalar(select(FollowUpReminder).where(FollowUpReminder.id == reminder_id,
                                  FollowUpReminder.user_id == user_id).with_for_update())
    if not reminder:
        raise ValidationError("Reminder not found.")
    p = owned_prospect(session, user_id, reminder.prospect_id)
    if not reminder.completed_at:
        reminder.completed_at = utcnow()
        record_event(session, user_id, "reminder_completed", p, details={"reminder_id": reminder.id})


def search_url(company, designation, location):
    parts = [text_value(v, label, 300, True) for v, label in
             ((designation, "Designation"), (company, "Company"), (location, "Location"))]
    parts[2] = "India" if parts[2] == "All India" else parts[2]
    query = " ".join('"' + p.replace('"', "") + '"' for p in parts)
    return "https://www.linkedin.com/search/results/people/?" + urlencode({"keywords": query})


def create_search(session, user_id, company_id, designation, location):
    company = session.get(Company, company_id)
    if not company:
        raise ValidationError("Choose a company.")
    designation = text_value(designation, "Designation", 200, True)
    location = text_value(location, "Location", 200, True)
    url = search_url(company.name, designation, location)
    digest = hashlib.sha256(url.encode()).hexdigest()
    existing = session.scalar(select(SearchHistory).where(SearchHistory.user_id == user_id, SearchHistory.url_hash == digest))
    if existing:
        return existing, False
    item = SearchHistory(user_id=user_id, company_id=company.id, company_name=company.name,
                         designation=designation, location=location, url=url, url_hash=digest)
    session.add(item)
    session.flush()
    return item, True


def complete_search(session, user_id, search_id):
    search = session.scalar(select(SearchHistory).where(SearchHistory.id == search_id, SearchHistory.user_id == user_id))
    if not search:
        raise ValidationError("Search not found.")
    search.completed_at = search.completed_at or utcnow()


def get_setting(session, user_id, key, default=None):
    item = session.scalar(select(UserSettings).where(UserSettings.user_id == user_id, UserSettings.key == key))
    return item.value if item else default


def set_setting(session, user_id, key, value):
    item = session.scalar(select(UserSettings).where(UserSettings.user_id == user_id, UserSettings.key == key))
    if item:
        item.value = value
    else:
        session.add(UserSettings(user_id=user_id, key=key, value=value))
