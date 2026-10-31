from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, available_timezones

import pandas as pd
import streamlit as st
from sqlalchemy import select

from connectiq import __version__
from connectiq.analytics import breakdown, daily_report, event_frame, overview_stats, pending_ageing, period_report, prospect_frame
from connectiq.constants import CATEGORIES, CITIES, JOB_TITLES, PRIORITIES, STATUSES, VERIFICATION
from connectiq.csv_io import COMPANY_COLUMNS, PROSPECT_COLUMNS, export_companies, export_frame, export_prospects
from connectiq.db import healthy, transaction
from connectiq.models import Company, FollowUpReminder, Prospect, SearchHistory, User
from connectiq.services import (add_interaction, add_reminder, archive_prospect, change_status, complete_reminder,
    complete_search, correct_date, create_search, delete_company, get_setting, restore_prospect, save_company,
    save_prospect, set_setting)
from connectiq.ui import bars, chart, csv_upload, import_summary, instant_picker, metrics, mutation, notice, range_picker, table, title
from connectiq.validation import aware, text_value


def go(page):
    st.session_state["page"] = page
    st.rerun()


def company_options(engine):
    with transaction(engine) as s:
        return {c.id: c.name for c in s.scalars(select(Company).order_by(Company.name))}


def prospect_form(engine, user_id, tz, key, existing=None, defaults=None):
    companies = company_options(engine)
    if not companies:
        notice("Add or import your companies to start saving prospects.")
        if st.button("Go to companies", key=key + "_companies"):
            go("Companies")
        return
    default = defaults or {}
    def value(field, fallback=""):
        return getattr(existing, field) if existing else default.get(field, fallback)
    with st.form(key, clear_on_submit=existing is None):
        a, b = st.columns(2)
        name = a.text_input("Full name", value=value("full_name"), max_chars=200)
        selected = value("company_id", next(iter(companies)))
        company_id = b.selectbox("Company", list(companies), format_func=companies.get,
                                  index=list(companies).index(selected) if selected in companies else 0)
        url = st.text_input("LinkedIn profile URL", value=value("linkedin_url"), placeholder="https://www.linkedin.com/in/…", max_chars=500)
        a, b = st.columns(2)
        designation = a.text_input("Current designation", value=value("designation"), max_chars=200)
        location = b.text_input("Location", value=value("location", "Bengaluru"), max_chars=200)
        a, b, c = st.columns(3)
        categories = [""] + list(CATEGORIES)
        current_category = value("category", "RTL Design")
        if current_category not in categories:
            categories.append(current_category)
        category = a.selectbox("Professional category", categories, index=categories.index(current_category))
        priority = b.selectbox("Priority", PRIORITIES, index=PRIORITIES.index(value("priority", "Medium")))
        discovery = c.date_input("Discovery date", value=value("discovery_date", datetime.now(ZoneInfo(tz)).date()),
                                 max_value=datetime.now(ZoneInfo(tz)).date())
        notes = st.text_area("Notes", value=value("notes"), max_chars=10000)
        allow = st.checkbox("I reviewed matching names; this is a different person", help="Only needed when someone with this name already exists at the company.")
        submit = st.form_submit_button("Save changes" if existing else "Add prospect", type="primary")
    if submit:
        data = dict(full_name=name, company_id=company_id, linkedin_url=url, designation=designation,
                    location=location, category=category, priority=priority, discovery_date=discovery, notes=notes)
        ok, _ = mutation(engine, lambda s: save_prospect(s, user_id, data, existing.id if existing else None, allow))
        if ok:
            st.rerun()


def overview(engine, user_id, tz):
    with transaction(engine) as s:
        stats, p = overview_stats(s, user_id, tz), prospect_frame(s, user_id)
        events = event_frame(s, user_id, tz)
    left, right = st.columns([4, 1])
    with left:
        title("Overview", f"{stats['Pending requests']} requests awaiting a response. Your semiconductor network, in focus.")
    if right.button("Discover people", type="primary", use_container_width=True):
        go("Discover People")
    labels = list(stats)[:10]
    for group in (labels[:5], labels[5:]):
        metrics([(label, f"{stats[label]:.1f}%" if label == "Acceptance rate" else f"{stats[label]:,}") for label in group])
    st.caption(f"Live totals · {stats['Total requests']} requests recorded · {stats['Withdrawn requests']} withdrawn · week starts Monday · {tz}")
    a, b = st.columns([2, 1])
    with a:
        dates = range_picker(tz, "overview_range")
    with b:
        st.caption("Start with your next connection")
        if st.button("Add a prospect", use_container_width=True):
            st.session_state["prospect_view"] = "Add prospect"
            go("Prospects")
    if not dates:
        return
    with transaction(engine) as s:
        daily = daily_report(s, user_id, *dates, tz)
    a, b = st.columns([1.7, 1])
    with a, st.container(border=True):
        st.subheader("Connection activity")
        chart(daily, ["Requests", "Acceptances"], key="overview_daily")
        if not daily.Requests.sum() and not daily.Acceptances.sum():
            st.caption("No requests or acceptances recorded in this period.")
    with b, st.container(border=True):
        st.subheader("Your network pipeline")
        counts = p.Status.value_counts()
        summary = pd.DataFrame({"Stage": STATUSES, "Prospects": [int(counts.get(v, 0)) for v in STATUSES]})
        table(summary)
    a, b = st.columns(2)
    with a, st.container(border=True):
        st.subheader("Companies in your network")
        bars(breakdown(p, "Company"), "Company", "Prospects", "overview_companies")
    with b, st.container(border=True):
        st.subheader("Recent activity")
        filtered = events[(events.recorded_at.map(lambda x: x.astimezone(ZoneInfo(tz)).date()) >= dates[0]) &
                          (events.recorded_at.map(lambda x: x.astimezone(ZoneInfo(tz)).date()) <= dates[1])].iloc[::-1].head(5)
        if filtered.empty:
            notice("Add a prospect to begin your activity history.")
        else:
            recent = filtered[["Person", "Company", "Designation", "kind", "recorded_at", "Current status"]].rename(columns={"kind": "Action", "recorded_at": "Date"})
            recent["Date"] = recent.Date.map(lambda v: v.astimezone(ZoneInfo(tz)).strftime("%d %b %H:%M"))
            table(recent)
        if st.button("View analytics", key="overview_analytics"):
            go("Analytics")
    with st.expander("More networking trends"):
        for frequency, label in (("W-MON", "Weekly activity"), ("MS", "Monthly activity")):
            st.subheader(label)
            chart(period_report(daily, frequency), ["Requests", "Acceptances"], "bar", key="overview_" + frequency)
        st.subheader("Acceptance-rate trend")
        chart(daily, ["Acceptance rate"], percent=True, key="overview_rate")
        for field in ("Designation", "Location"):
            st.subheader(field + " distribution")
            bars(breakdown(p, field), field, "Prospects", "overview_" + field)


def discover(engine, user_id, tz):
    title("Discover People", "Find your next semiconductor connection through a targeted LinkedIn search.")
    companies = company_options(engine)
    if not companies:
        notice("Import your company database on the Companies page to begin.")
        return
    with transaction(engine) as s:
        preferences = get_setting(s, user_id, "preferences", {})
    a, b = st.columns([1.3, 1])
    with a, st.container(border=True):
        st.subheader("Build a people search")
        company_id = st.selectbox("Company", list(companies), format_func=companies.get)
        designation = st.selectbox("Job designation", list(JOB_TITLES) + ["Custom job title"])
        if designation == "Custom job title":
            designation = st.text_input("Custom job title", max_chars=200)
        cities = list(CITIES) + ["Other Indian locations", "All India"]
        preferred = preferences.get("cities", ["Bengaluru"])
        location = st.selectbox("Location", cities, index=cities.index(preferred[0]) if preferred and preferred[0] in cities else 0)
        if location == "Other Indian locations":
            location = st.text_input("Indian city or region", max_chars=200)
        if st.button("Generate search link", type="primary"):
            ok, result = mutation(engine, lambda s: create_search(s, user_id, company_id, designation, location), "Search ready.")
            if ok:
                st.session_state["generated_search"] = {"url": result[0].url, "company": companies[company_id], "designation": designation, "location": location}
        current = st.session_state.get("generated_search")
        if current:
            st.write(f"{current['company']} · {current['designation']} · {current['location']}")
            st.link_button("Open LinkedIn search", current["url"], use_container_width=True)
            st.code(current["url"], language=None, wrap_lines=True)
            st.caption("Use the copy button on the URL. Search opens in a new tab.")
    with b, st.container(border=True):
        st.subheader("From discovery to connection")
        st.write("Search people by company, role and city. Review profiles on LinkedIn, then save relevant professionals here.")
        st.write("You send invitations and messages personally. Mark the request date when you return to ConnectIQ.")
        st.caption("Search keywords help narrow results; LinkedIn decides the matches. Company inclusion does not verify an India RTL/ASIC team.")
    with st.expander("Add a discovered prospect", expanded=False):
        prospect_form(engine, user_id, tz, "discover_add", defaults={"company_id": company_id, "designation": designation, "location": location})
    st.subheader("Search history")
    with transaction(engine) as s:
        history = list(s.scalars(select(SearchHistory).where(SearchHistory.user_id == user_id).order_by(SearchHistory.created_at.desc())))
    state = st.radio("Search status", ["All", "Open", "Completed"], horizontal=True)
    history = [h for h in history if state == "All" or (state == "Completed") == bool(h.completed_at)]
    if not history:
        notice("Your searches will appear here. Repeated searches share one history entry.")
    else:
        table(pd.DataFrame([{"Company": h.company_name, "Designation": h.designation, "Location": h.location,
                             "Created": aware(h.created_at).astimezone(ZoneInfo(tz)).strftime("%d %b %Y"),
                             "Status": "Completed" if h.completed_at else "Open", "URL": h.url} for h in history]))
        open_items = {h.id: f"{h.company_name} · {h.designation} · {h.location}" for h in history if not h.completed_at}
        if open_items:
            selected = st.selectbox("Search to complete", list(open_items), format_func=open_items.get)
            if st.button("Mark search completed"):
                ok, _ = mutation(engine, lambda s: complete_search(s, user_id, selected))
                if ok:
                    st.rerun()


def prospects(engine, user_id, tz):
    title("Prospects", "A considered network starts with the people you choose to keep.")
    views = ["All prospects", "Add prospect", "Import / export", "Archive"]
    if requested := st.session_state.pop("prospect_view", None):
        st.session_state["prospect_workspace"] = requested
    view = st.radio("Prospect workspace", views, horizontal=True, key="prospect_workspace")
    if view == "Add prospect":
        prospect_form(engine, user_id, tz, "new_prospect")
        return
    if view == "Import / export":
        st.subheader("Move your prospect data")
        st.caption("Required: Full Name, Company Name, LinkedIn URL. Company names must match exactly. Dates use ISO 8601; timestamps without an offset are UTC. Imports add records and never overwrite existing prospects.")
        csv_upload(engine, "prospects", user_id, "prospect_import")
        with transaction(engine) as s:
            payload = export_prospects(s, user_id)
        st.download_button("Export all prospects", payload, "connectiq-prospects.csv", "text/csv")
        st.download_button("Download CSV template", export_frame(pd.DataFrame(columns=list(PROSPECT_COLUMNS))), "prospect-template.csv", "text/csv")
        return
    with transaction(engine) as s:
        frame = prospect_frame(s, user_id, archived=view == "Archive")
    if view == "Archive":
        st.caption("Deleted prospects are archived to preserve activity and duplicate detection.")
        if frame.empty:
            notice("No archived prospects.")
            return
        table(frame.drop(columns="id"))
        names = dict(zip(frame.id, frame["Full name"] + " · " + frame.Company))
        selected = st.selectbox("Prospect to restore", list(names), format_func=names.get)
        if st.button("Restore prospect"):
            ok, _ = mutation(engine, lambda s: restore_prospect(s, user_id, selected), "Prospect restored.")
            if ok:
                st.rerun()
        return
    query = st.text_input("Search prospects", placeholder="Name, company, designation or notes")
    with st.expander("Filters and sorting"):
        cols = st.columns(3)
        for i, field in enumerate(["Company", "Designation", "Location", "Category", "Status"]):
            chosen = cols[i % 3].multiselect(field, sorted(frame[field].unique()), key="filter_" + field)
            if chosen:
                frame = frame[frame[field].isin(chosen)]
        sort = cols[2].selectbox("Sort by", ["Newest discovered", "Oldest discovered", "Priority"])
    if query:
        mask = frame[["Full name", "Company", "Designation", "Notes"]].apply(lambda c: c.str.contains(query, case=False, regex=False)).any(axis=1)
        frame = frame[mask]
    if sort == "Oldest discovered":
        frame = frame.sort_values("Discovery date")
    elif sort == "Priority":
        frame = frame.assign(_rank=frame.Priority.map({"High": 0, "Medium": 1, "Low": 2})).sort_values("_rank").drop(columns="_rank")
    st.caption(f"{len(frame):,} prospects in this view")
    if frame.empty:
        notice("No matching prospects. Add someone you have reviewed or adjust your filters.")
        return
    page = st.number_input("Page", min_value=1, max_value=max(1, (len(frame) + 49) // 50), value=1, step=1)
    shown = frame.iloc[(page - 1) * 50:page * 50]
    table(shown[["Full name", "Company", "Designation", "Location", "Status", "Priority", "LinkedIn URL"]], height=400)
    st.download_button("Export this view", export_frame(frame.drop(columns="id")), "prospect-view.csv", "text/csv")
    names = dict(zip(frame.id, frame["Full name"] + " · " + frame.Company))
    selected = st.selectbox("Open prospect", list(names), format_func=names.get)
    prospect_detail(engine, user_id, tz, selected)


def prospect_detail(engine, user_id, tz, prospect_id):
    with transaction(engine) as s:
        p = s.get(Prospect, prospect_id)
        events = event_frame(s, user_id, tz)
        events = events[events.prospect_id == prospect_id].iloc[::-1]
    st.subheader(p.full_name)
    st.link_button("Open LinkedIn profile", p.linkedin_url)
    labels = []
    for label, field in [("Added", "created_at"), ("Request sent", "request_sent_at"), ("Accepted", "accepted_at"), ("Last interaction", "last_interaction_at")]:
        value = getattr(p, field)
        labels.append(f"{label}: {aware(value).astimezone(ZoneInfo(tz)).strftime('%d %b %Y %H:%M') if value else '—'}")
    st.caption(" · ".join(labels))
    status_tab, edit_tab, history_tab = st.tabs(["Connection & reminders", "Edit details", "Activity history"])
    with status_tab:
        a, b = st.columns(2)
        with a, st.form(f"status_{p.id}"):
            st.subheader("Update connection")
            status = st.selectbox("Connection status", STATUSES, index=STATUSES.index(p.status))
            moment = instant_picker("Activity date", tz, f"status_at_{p.id}")
            if st.form_submit_button("Update status", type="primary"):
                ok, _ = mutation(engine, lambda s: change_status(s, user_id, p.id, status, moment), "Connection status updated.")
                if ok:
                    st.rerun()
        with b, st.form(f"reminder_{p.id}"):
            st.subheader("Set a reminder")
            due = st.date_input("Reminder date", value=datetime.now(ZoneInfo(tz)).date() + timedelta(days=7))
            note = st.text_area("Reminder note", max_chars=2000)
            if st.form_submit_button("Create reminder"):
                ok, _ = mutation(engine, lambda s: add_reminder(s, user_id, p.id, due, note), "Reminder created.")
                if ok:
                    st.rerun()
        with st.expander("Correct a recorded date"):
            fields = {"Request sent": "request_sent_at", "Accepted": "accepted_at", "Last interaction": "last_interaction_at"}
            field = st.selectbox("Date to correct", list(fields), key=f"correction_field_{p.id}")
            with st.form(f"correction_{p.id}"):
                moment = instant_picker("Correct date", tz, f"correct_at_{p.id}_{field}", getattr(p, fields[field]))
                reason = st.text_input("Reason for correction", max_chars=2000)
                st.caption("The original event and your correction will both stay in the audit history.")
                if st.form_submit_button("Record correction"):
                    ok, _ = mutation(engine, lambda s: correct_date(s, user_id, p.id, fields[field], moment, reason), "Correction recorded.")
                    if ok:
                        st.rerun()
        interaction_form(engine, user_id, tz, p.id, "detail_interaction")
    with edit_tab:
        prospect_form(engine, user_id, tz, f"edit_{p.id}", existing=p)
        with st.expander("Delete prospect"):
            confirm = st.checkbox(f"Delete {p.full_name} from the active CRM", key=f"delete_confirm_{p.id}")
            st.caption("The record moves to Archive; its activity history remains available.")
            if st.button("Delete prospect", disabled=not confirm, key=f"delete_{p.id}"):
                ok, _ = mutation(engine, lambda s: archive_prospect(s, user_id, p.id, confirm), "Prospect archived.")
                if ok:
                    st.rerun()
    with history_tab:
        if events.empty:
            notice("No activity recorded.")
        else:
            history = events[["kind", "Previous status", "New status", "recorded_at", "Original effective at", "effective_at", "details"]].rename(columns={"kind": "Action", "recorded_at": "Recorded at", "effective_at": "Effective at", "details": "Details"})
            for field in ["Recorded at", "Original effective at", "Effective at"]:
                history[field] = history[field].map(lambda v: v.astimezone(ZoneInfo(tz)).isoformat())
            history["Details"] = history.Details.map(str)
            table(history)


def interaction_form(engine, user_id, tz, prospect_id, key):
    with st.form(key + str(prospect_id), clear_on_submit=True):
        st.subheader("Log an interaction")
        note = st.text_area("Interaction notes", max_chars=10000)
        moment = instant_picker("Interaction date", tz, key + "_at_" + str(prospect_id))
        if st.form_submit_button("Save interaction"):
            ok, _ = mutation(engine, lambda s: add_interaction(s, user_id, prospect_id, note, moment), "Interaction recorded.")
            if ok:
                st.rerun()


def followups(engine, user_id, tz):
    today = datetime.now(ZoneInfo(tz)).date()
    with transaction(engine) as s:
        p = prospect_frame(s, user_id)
        rows = s.execute(select(FollowUpReminder, Prospect.full_name, Company.name).select_from(FollowUpReminder)
            .join(Prospect, FollowUpReminder.prospect_id == Prospect.id).join(Company, Prospect.company_id == Company.id)
            .where(FollowUpReminder.user_id == user_id, Prospect.deleted_at.is_(None)).order_by(FollowUpReminder.due_date)).all()
    overdue = sum(r.due_date < today and not r.completed_at for r, _, _ in rows)
    title("Follow-ups", f"{overdue} overdue reminders. Keep the next conversation in sight.")
    state = st.radio("Reminder status", ["Open", "Overdue", "Upcoming", "Completed", "All"], horizontal=True)
    with st.expander("Filter reminder dates"):
        limit_dates = st.checkbox("Limit to date range")
        dates = st.date_input("Reminder date range", (today - timedelta(days=30), today + timedelta(days=30)))
    filtered = []
    for r, name, company in rows:
        if state == "Open" and r.completed_at or state == "Overdue" and (r.completed_at or r.due_date >= today):
            continue
        if state == "Upcoming" and (r.completed_at or r.due_date < today) or state == "Completed" and not r.completed_at:
            continue
        if limit_dates and (len(dates) != 2 or not dates[0] <= r.due_date <= dates[1]):
            continue
        filtered.append({"id": r.id, "Person": name, "Company": company, "Due date": r.due_date, "Reminder": r.note,
                         "Status": "Completed" if r.completed_at else "Overdue" if r.due_date < today else "Due today" if r.due_date == today else "Upcoming"})
    st.subheader("Your reminders")
    if not filtered:
        notice("No reminders in this view. Set a reminder on a prospect or below.")
    else:
        table(pd.DataFrame(filtered).drop(columns="id"))
        options = {r["id"]: f"{r['Person']} · {r['Due date']} · {r['Reminder']}" for r in filtered if r["Status"] != "Completed"}
        if options:
            choice = st.selectbox("Reminder to complete", list(options), format_func=options.get)
            if st.button("Mark reminder completed"):
                ok, _ = mutation(engine, lambda s: complete_reminder(s, user_id, choice), "Reminder completed.")
                if ok:
                    st.rerun()
    st.subheader("People to review")
    pending = pending_ageing(p, tz)
    threshold = st.slider("Pending for at least this many days", 0, 90, 14)
    review = pending[pending["Days pending"] >= threshold]
    flagged = p[p.Status == "Follow-up Required"]
    if review.empty and flagged.empty:
        notice("No prospects currently need review at this threshold.")
    else:
        combined = pd.concat([review, flagged]).drop_duplicates("id")
        table(combined[["Full name", "Company", "Status", "Request sent", "Days pending", "LinkedIn URL"]])
    if not p.empty:
        st.subheader("Plan the next interaction")
        options = dict(zip(p.id, p["Full name"] + " · " + p.Company))
        selected = st.selectbox("Prospect", list(options), format_func=options.get)
        with st.form("followup_new_reminder"):
            due = st.date_input("Due date", today + timedelta(days=7))
            note = st.text_input("Reminder", max_chars=2000)
            if st.form_submit_button("Create reminder"):
                ok, _ = mutation(engine, lambda s: add_reminder(s, user_id, selected, due, note), "Reminder created.")
                if ok:
                    st.rerun()
        interaction_form(engine, user_id, tz, selected, "followup_interaction")


def analytics(engine, user_id, tz):
    title("Analytics", "The cadence, coverage and outcomes of your networking activity.")
    dates = range_picker(tz, "analytics_range")
    if not dates:
        return
    with transaction(engine) as s:
        daily = daily_report(s, user_id, *dates, tz)
        stats = overview_stats(s, user_id, tz)
        prospects = prospect_frame(s, user_id)
        events = event_frame(s, user_id, tz)
    sent = int(daily.Requests.sum())
    cohort_rate = daily["Accepted from cohort"].sum() / sent * 100 if sent else 0
    metrics([("Requests in period", sent), ("Accepted in period", int(daily.Acceptances.sum())),
             ("Activities in period", int(daily.Activities.sum())), ("Period cohort rate", f"{cohort_rate:.1f}%"),
             ("Overall acceptance rate", f"{stats['Acceptance rate']:.1f}%")])
    st.caption("Cohort rate: requests sent in the selected period that were accepted by its end. Overall rate: accepted request cycles / all recorded request cycles. Counts include archived activity.")
    frequency = st.radio("Group activity by", ["Daily", "Weekly", "Monthly"], horizontal=True)
    report = daily if frequency == "Daily" else period_report(daily, "W-MON" if frequency == "Weekly" else "MS")
    a, b = st.columns([1.5, 1])
    with a, st.container(border=True):
        st.subheader("Networking activity")
        chart(report, ["Requests", "Acceptances"], "bar", key="analytics_activity")
    with b, st.container(border=True):
        st.subheader("Acceptance-rate trend")
        chart(report, ["Acceptance rate"], percent=True, key="analytics_rate")
    st.download_button("Download performance report", export_frame(report), "connectiq-performance.csv", "text/csv")
    with st.expander("Performance table"):
        table(report)
        st.subheader("All networking activities")
        chart(report, ["Activities"], "bar", key="analytics_all_activity")
    st.subheader("Network coverage")
    scope = st.radio("Prospect scope", ["Discovered in period", "All current prospects"], horizontal=True)
    scoped = prospects[(prospects["Discovery date"] >= dates[0]) & (prospects["Discovery date"] <= dates[1])] if scope == "Discovered in period" else prospects
    tabs = st.tabs(["Companies", "Designations", "Locations", "Statuses"])
    for tab, field in zip(tabs, ["Company", "Designation", "Location", "Status"]):
        with tab:
            data = breakdown(scoped, field)
            a, b = st.columns(2)
            with a:
                bars(data, field, "Prospects", "analytics_" + field)
            with b:
                table(data)
            st.download_button("Download " + field.lower() + " report", export_frame(data), f"connectiq-{field.lower()}.csv", "text/csv", key="download_" + field)
    st.subheader("Pending request ageing")
    pending = pending_ageing(prospects, tz)
    st.caption("Current pending requests as of today; independent of the historical reporting period.")
    if pending.empty:
        notice("No pending requests.")
    else:
        data = pending[["Full name", "Company", "Request sent", "Days pending"]]
        table(data)
        st.download_button("Download pending ageing", export_frame(data), "connectiq-pending.csv", "text/csv")
    st.subheader("Activity audit export")
    e = events[events.recorded_at.map(lambda v: dates[0] <= v.astimezone(ZoneInfo(tz)).date() <= dates[1])]
    st.download_button("Download activity audit", export_frame(e), "connectiq-activity.csv", "text/csv")


def companies(engine, user_id, tz):
    with transaction(engine) as s:
        records = list(s.scalars(select(Company).order_by(Company.name)))
        summary = get_setting(s, user_id, "initial_company_import")
        statistics = breakdown(prospect_frame(s, user_id), "Company")
    title("Companies", f"{len(records)} companies in your semiconductor research universe.")
    st.caption("Company inclusion is a research starting point. India office and RTL/ASIC team details remain unverified unless you verify them.")
    listing, adding, importing = st.tabs(["Company directory", "Add company", "Import / export"])
    with listing:
        a, b = st.columns([2, 1])
        query = a.text_input("Search companies", placeholder="Search the company directory")
        verification = b.selectbox("Verification", ["All"] + list(VERIFICATION))
        filtered = [c for c in records if query.casefold() in c.name.casefold() and (verification == "All" or c.verification_status == verification)]
        counts = statistics.set_index("Company").to_dict("index") if not statistics.empty else {}
        rows = [{"Company": c.name, "Prospects": counts.get(c.name, {}).get("Prospects", 0),
                 "Accepted": counts.get(c.name, {}).get("Accepted", 0), "Pending": counts.get(c.name, {}).get("Pending", 0),
                 "Locations": c.locations, "Domains": c.domains, "Verification": c.verification_status} for c in filtered]
        st.caption(f"{len(filtered)} of {len(records)} companies")
        if not rows:
            notice("No matching companies.")
        else:
            table(pd.DataFrame(rows), height=450)
            options = {c.id: c.name for c in filtered}
            selected = st.selectbox("Company to edit", list(options), format_func=options.get)
            current = next(c for c in filtered if c.id == selected)
            with st.expander("Edit company details"):
                company_form(engine, f"edit_company_{selected}", current)
            with st.expander("Delete company"):
                confirm = st.checkbox(f"Delete {current.name}")
                st.caption("Companies with prospect history cannot be deleted. Existing records are never reset on restart.")
                if st.button("Delete company", disabled=not confirm):
                    ok, _ = mutation(engine, lambda s: delete_company(s, selected, confirm), "Company deleted.")
                    if ok:
                        st.rerun()
    with adding:
        company_form(engine, "new_company")
    with importing:
        st.subheader("Import companies")
        st.caption('Required column: Company Name. Names are preserved exactly; only exact duplicate names are skipped.')
        csv_upload(engine, "companies", user_id, "company_import")
        with transaction(engine) as s:
            payload = export_companies(s)
        st.download_button("Export company database", payload, "connectiq-companies.csv", "text/csv")
        st.download_button("Download company CSV template", export_frame(pd.DataFrame(columns=list(COMPANY_COLUMNS))), "company-template.csv", "text/csv")
        if summary:
            st.subheader("Original company import")
            import_summary(summary)
            st.caption("Original source checksum: " + summary["sha256"])


def company_form(engine, key, current=None):
    with st.form(key, clear_on_submit=current is None):
        name = st.text_input("Company name", current.name if current else "", max_chars=300)
        website = st.text_input("Website (optional)", current.website if current else "", placeholder="https://…", max_chars=1000)
        a, b = st.columns(2)
        locations = a.text_input("Indian office locations", current.locations if current else "")
        domains = b.text_input("Semiconductor design domains", current.domains if current else "")
        verification = st.selectbox("Verification status", VERIFICATION, index=VERIFICATION.index(current.verification_status) if current else 0, key=key + "_verification")
        notes = st.text_area("Company notes", current.notes if current else "", max_chars=10000,
                             help="For verified claims, record the source and date of your verification.")
        if st.form_submit_button("Save company", type="primary"):
            data = dict(name=name, website=website, locations=locations, domains=domains, verification_status=verification, notes=notes)
            ok, _ = mutation(engine, lambda s: save_company(s, data, current.id if current else None), "Company saved.")
            if ok:
                st.rerun()


def settings(engine, user_id, tz):
    with transaction(engine) as s:
        user = s.get(User, user_id)
        prefs = get_setting(s, user_id, "preferences", {})
    title("Settings", "Your private workspace, research preferences and data.")
    a, b = st.columns([1, 2])
    with a:
        st.subheader("Profile & preferences")
        st.write("Choose the roles and cities you research most often.")
        st.caption("Workspace dates and reports follow your selected timezone.")
    with b, st.form("profile_settings"):
        name = st.text_input("Display name", user.display_name, max_chars=100)
        st.text_input("Sign-in email", user.email, disabled=True)
        categories = st.multiselect("Preferred job categories", CATEGORIES, default=prefs.get("categories", ["RTL Design", "ASIC Design"]))
        cities = st.multiselect("Preferred cities", CITIES, default=prefs.get("cities", ["Bengaluru"]))
        zones = ["Asia/Kolkata"] + sorted(available_timezones() - {"Asia/Kolkata"})
        timezone_name = st.selectbox("Timezone", zones, index=zones.index(tz))
        if st.form_submit_button("Save preferences", type="primary"):
            def save(s):
                s.get(User, user_id).display_name = text_value(name, "Display name", 100, True)
                set_setting(s, user_id, "preferences", dict(categories=categories, cities=cities, timezone=timezone_name))
            ok, _ = mutation(engine, save)
            if ok:
                st.rerun()
    a, b = st.columns([1, 2])
    with a:
        st.subheader("Appearance")
        st.caption("Use a dark or light workspace.")
    with b:
        light = st.toggle("Light mode", value=st.session_state.get("light_mode", False))
        if light != st.session_state.get("light_mode", False):
            ok, _ = mutation(engine, lambda s: set_setting(s, user_id, "appearance", {"light": light}))
            if ok:
                st.session_state["light_mode"] = light
                st.rerun()
    a, b = st.columns([1, 2])
    with a:
        st.subheader("Database & exports")
        st.caption("CSV downloads contain personal information. Store them privately.")
    with b:
        st.write(f"Database: {'Connected' if healthy(engine) else 'Unavailable'} · {engine.dialect.name}")
        st.write(f"ConnectIQ {__version__}")
        with transaction(engine) as s:
            company_csv, prospect_csv = export_companies(s), export_prospects(s, user_id)
        st.download_button("Export companies", company_csv, "connectiq-companies.csv", "text/csv")
        st.download_button("Export prospects", prospect_csv, "connectiq-prospects.csv", "text/csv")
        if st.button("Import company CSV"):
            go("Companies")
        if st.button("Import prospect CSV"):
            st.session_state["prospect_view"] = "Import / export"
            go("Prospects")
    a, b = st.columns([1, 2])
    with a:
        st.subheader("Authentication")
        st.caption("Private single-user access is required.")
    with b:
        st.write("Password protection enabled · five failed attempts trigger a 15-minute lock")
        st.write("Rotate your password with `python -m connectiq.cli password`, update ADMIN_PASSWORD_HASH in your deployment environment, and restart. Existing sessions are invalidated.")
        st.caption("Your sign-in email is managed through ADMIN_EMAIL. No LinkedIn credentials or session cookies are stored.")


PAGES = {"Overview": overview, "Discover People": discover, "Prospects": prospects, "Follow-ups": followups,
         "Analytics": analytics, "Companies": companies, "Settings": settings}
