"""Metrics from recorded events, with timezone boundaries and auditable date corrections."""
from datetime import timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select

from connectiq.models import ActivityEvent, Company, Prospect, utcnow
from connectiq.validation import ValidationError, aware


def prospect_frame(session, user_id, archived=False):
    query = select(Prospect, Company.name).join(Company).where(Prospect.user_id == user_id)
    query = query.where(Prospect.deleted_at.is_not(None) if archived else Prospect.deleted_at.is_(None))
    columns = ["id", "Full name", "Company", "Designation", "LinkedIn URL", "Location", "Category", "Status",
               "Priority", "Discovery date", "Date added", "Request sent", "Accepted", "Last interaction", "Notes"]
    rows = [[p.id, p.full_name, c, p.designation, p.linkedin_url, p.location, p.category, p.status, p.priority,
             p.discovery_date, p.created_at, p.request_sent_at, p.accepted_at, p.last_interaction_at, p.notes]
            for p, c in session.execute(query.order_by(Prospect.discovery_date.desc(), Prospect.id.desc()))]
    return pd.DataFrame(rows, columns=columns)


def event_frame(session, user_id, tz="Asia/Kolkata"):
    columns = ["id", "prospect_id", "kind", "date", "recorded_at", "effective_at", "Person", "Company", "Designation", "Current status", "related_event_id", "details", "Previous status", "New status", "Original effective at"]
    events = session.execute(select(ActivityEvent, Prospect, Company.name).outerjoin(Prospect, ActivityEvent.prospect_id == Prospect.id)
        .outerjoin(Company, Prospect.company_id == Company.id).where(ActivityEvent.user_id == user_id).order_by(ActivityEvent.id)).all()
    corrections = {e.related_event_id: e.effective_at for e, _, _ in events if e.kind == "date_corrected" and e.related_event_id}
    rows = []
    zone = ZoneInfo(tz)
    for e, p, company in events:
        effective = aware(corrections.get(e.id, e.effective_at))
        rows.append([e.id, e.prospect_id, e.kind, effective.astimezone(zone).date(), aware(e.occurred_at), effective,
                     p.full_name if p else "Workspace", company or "", p.designation if p else "",
                     p.status if p else "", e.related_event_id, e.details, e.from_status, e.to_status, aware(e.effective_at)])
    return pd.DataFrame(rows, columns=columns)


def overview_stats(session, user_id, tz="Asia/Kolkata", now=None):
    today = aware(now or utcnow()).astimezone(ZoneInfo(tz)).date()
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    p = prospect_frame(session, user_id)
    e = event_frame(session, user_id, tz)
    requests = e[e.kind == "request_sent"]
    accepted = e[e.kind == "accepted"]
    def count_since(rows, since):
        return int(((rows.date >= since) & (rows.date <= today)).sum())
    accepted_requests = set(accepted.related_event_id.dropna().astype(int))
    return {
        "Total prospects": len(p), "Companies covered": p.Company.nunique(),
        "Requests today": count_since(requests, today), "Requests this week": count_since(requests, week),
        "Requests this month": count_since(requests, month), "Accepted today": count_since(accepted, today),
        "Accepted connections": int((p.Status == "Accepted").sum() + ((p.Status == "Follow-up Required") & p.Accepted.notna()).sum()),
        "Pending requests": int((p.Status.isin(["Request Sent", "Follow-up Required"]) & p["Request sent"].notna() & p.Accepted.isna()).sum()),
        "Declined requests": int((p.Status == "Declined").sum()),
        "Acceptance rate": round(len(accepted_requests) / len(requests) * 100, 1) if len(requests) else 0.0,
        "Total requests": len(requests), "Withdrawn requests": int((p.Status == "Withdrawn").sum()),
    }


def daily_report(session, user_id, start, end, tz="Asia/Kolkata"):
    if start > end or (end - start).days > 3660:
        raise ValidationError("Choose an ordered date range of at most ten years.")
    e = event_frame(session, user_id, tz)
    requests = e[e.kind == "request_sent"]
    accepted = e[e.kind == "accepted"]
    recorded_days = e.recorded_at.map(lambda value: value.astimezone(ZoneInfo(tz)).date()).value_counts().to_dict()
    rows = []
    for day in pd.date_range(start, end):
        current = day.date()
        sent = int((requests.date == current).sum())
        accepted_today = int((accepted.date == current).sum())
        cumulative_requests = set(requests.loc[requests.date <= current, "id"])
        cumulative_accepted = set(accepted.loc[accepted.date <= current, "related_event_id"].dropna().astype(int)) & cumulative_requests
        cohort = set(requests.loc[requests.date == current, "id"])
        accepted_by_end = set(accepted.loc[accepted.date <= end, "related_event_id"].dropna().astype(int))
        rows.append({"Date": current, "Requests": sent, "Acceptances": accepted_today,
                     "Activities": int(recorded_days.get(current, 0)),
                     "Accepted from cohort": len(cohort & accepted_by_end),
                     "Acceptance rate": round(len(cumulative_accepted) / len(cumulative_requests) * 100, 1) if cumulative_requests else 0.0})
    return pd.DataFrame(rows)


def period_report(daily, frequency):
    if daily.empty:
        return daily
    data = daily.copy()
    data["Date"] = pd.to_datetime(data.Date)
    result = data.set_index("Date").resample(frequency, closed="left", label="left").agg({"Requests": "sum", "Acceptances": "sum", "Activities": "sum", "Accepted from cohort": "sum", "Acceptance rate": "last"}).reset_index()
    result["Date"] = result.Date.dt.date
    return result


def breakdown(prospects, field):
    if prospects.empty:
        return pd.DataFrame(columns=[field, "Prospects", "Accepted", "Pending", "Declined", "Withdrawn"])
    data = prospects.copy()
    data[field] = data[field].replace("", "Unspecified")
    data["_accepted"] = data.Status.eq("Accepted") | (data.Status.eq("Follow-up Required") & data.Accepted.notna())
    data["_pending"] = data.Status.isin(["Request Sent", "Follow-up Required"]) & data["Request sent"].notna() & data.Accepted.isna()
    data["_declined"], data["_withdrawn"] = data.Status.eq("Declined"), data.Status.eq("Withdrawn")
    return data.groupby(field).agg(Prospects=("id", "count"), Accepted=("_accepted", "sum"), Pending=("_pending", "sum"),
                                 Declined=("_declined", "sum"), Withdrawn=("_withdrawn", "sum")).reset_index().sort_values("Prospects", ascending=False)


def pending_ageing(prospects, tz="Asia/Kolkata", now=None):
    today = aware(now or utcnow()).astimezone(ZoneInfo(tz)).date()
    p = prospects[prospects.Status.isin(["Request Sent", "Follow-up Required"]) & prospects["Request sent"].notna() & prospects.Accepted.isna()].copy()
    p["Days pending"] = p["Request sent"].map(lambda value: max(0, (today - aware(value).astimezone(ZoneInfo(tz)).date()).days))
    return p.sort_values("Days pending", ascending=False)
