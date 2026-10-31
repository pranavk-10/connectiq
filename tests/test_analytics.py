from datetime import date, datetime, timedelta, timezone

import pytest

from connectiq.analytics import breakdown, daily_report, overview_stats, pending_ageing, period_report, prospect_frame
from connectiq.services import archive_prospect, change_status, correct_date, save_prospect


def test_empty_database(session):
    stats = overview_stats(session, 1)
    assert all(value == 0 for value in stats.values())
    daily = daily_report(session, 1, date(2026, 1, 1), date(2026, 1, 5))
    assert len(daily) == 5 and daily.Requests.sum() == 0 and daily["Acceptance rate"].sum() == 0
    assert breakdown(prospect_frame(session, 1), "Company").empty
    assert pending_ageing(prospect_frame(session, 1)).empty


def test_daily_weekly_monthly_ist_boundaries(session, company):
    # Monday February 2, 2026 at 00:30 IST. UTC is still Sunday.
    now = datetime(2026, 2, 1, 19, 0, tzinfo=timezone.utc)
    times = [datetime(2026, 2, 1, 18, 45, tzinfo=timezone.utc),  # today and this week
             datetime(2026, 2, 1, 18, 0, tzinfo=timezone.utc),   # yesterday, this month
             datetime(2026, 1, 31, 17, 0, tzinfo=timezone.utc)]  # previous month
    for i, moment in enumerate(times):
        p = save_prospect(session, 1, {"full_name": f"Boundary person {i}", "company_id": company.id,
                                     "linkedin_url": f"https://linkedin.com/in/boundary-{i}"})
        change_status(session, 1, p.id, "Request Sent", moment)
    stats = overview_stats(session, 1, now=now)
    assert (stats["Requests today"], stats["Requests this week"], stats["Requests this month"], stats["Total requests"]) == (1, 1, 2, 3)
    assert stats["Pending requests"] == 3


def test_acceptance_rate_requests_not_all_prospects(session, company):
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for i in range(4):
        p = save_prospect(session, 1, {"full_name": f"Rate person {i}", "company_id": company.id,
            "linkedin_url": f"linkedin.com/in/rate-person-{i}"})
        if i < 2:
            change_status(session, 1, p.id, "Request Sent", start)
        if i == 0:
            change_status(session, 1, p.id, "Accepted", start + timedelta(days=2))
    stats = overview_stats(session, 1, now=start + timedelta(days=2))
    assert stats["Acceptance rate"] == 50
    assert stats["Accepted today"] == 1 and stats["Accepted connections"] == 1
    assert stats["Pending requests"] == 1 and stats["Total prospects"] == 4


def test_corrections_move_reporting_date_without_double_count(session, prospect):
    sent = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
    change_status(session, 1, prospect.id, "Request Sent", sent)
    correct_date(session, 1, prospect.id, "request_sent_at", sent - timedelta(days=1), "Recorded on wrong day")
    daily = daily_report(session, 1, date(2026, 1, 4), date(2026, 1, 5))
    assert list(daily.Requests) == [1, 0]
    assert overview_stats(session, 1)["Total requests"] == 1


def test_pending_ageing_and_accepted_followup(session, prospect):
    sent = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
    change_status(session, 1, prospect.id, "Request Sent", sent)
    pending = pending_ageing(prospect_frame(session, 1), now=sent + timedelta(days=14))
    assert pending.iloc[0]["Days pending"] == 14
    change_status(session, 1, prospect.id, "Accepted", sent + timedelta(days=3))
    change_status(session, 1, prospect.id, "Follow-up Required", sent + timedelta(days=4))
    assert overview_stats(session, 1)["Pending requests"] == 0
    assert overview_stats(session, 1)["Accepted connections"] == 1


def test_archived_history_counts_but_not_active_inventory(session, prospect):
    change_status(session, 1, prospect.id, "Request Sent", datetime(2026, 1, 5, tzinfo=timezone.utc))
    archive_prospect(session, 1, prospect.id, True)
    stats = overview_stats(session, 1)
    assert stats["Total prospects"] == 0 and stats["Total requests"] == 1


def test_weekly_monthly_aggregation_and_range_validation(session):
    from connectiq.validation import ValidationError
    daily = daily_report(session, 1, date(2026, 1, 4), date(2026, 2, 2))
    weekly = period_report(daily, "W-MON")
    assert all(d.weekday() == 0 for d in weekly.Date)
    assert weekly.Date.iloc[0] == date(2025, 12, 29)
    monthly = period_report(daily, "MS")
    assert list(monthly.Date) == [date(2026, 1, 1), date(2026, 2, 1)]
    with pytest.raises(ValidationError):
        daily_report(session, 1, date(2026, 2, 2), date(2026, 1, 1))


def test_acceptance_trend_does_not_exceed_100(session, prospect):
    sent = datetime(2026, 1, 1, tzinfo=timezone.utc)
    change_status(session, 1, prospect.id, "Request Sent", sent)
    change_status(session, 1, prospect.id, "Accepted", sent + timedelta(days=5))
    daily = daily_report(session, 1, date(2026, 1, 5), date(2026, 1, 7))
    assert list(daily["Acceptance rate"]) == [0, 100, 100]
    assert daily["Accepted from cohort"].sum() == 0
