"""Shared Streamlit presentation, with escaped data and consistent chart styling."""
import html
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from connectiq.db import transaction
from connectiq.validation import ValidationError, aware

ACCENT = "#62b5e5"


def style(light=False):
    css = (Path(__file__).parent / "assets/app.css").read_text(encoding="utf-8")
    tokens = "--ground:#f6f6f6;--surface:#ffffff;--raised:#e9e9e9;--ink:#202020;--muted:#595959;--rule:#dedede;--accent:#0873ac;" if light else ""
    st.markdown(f"<style>{css}\n:root{{{tokens}}}</style>", unsafe_allow_html=True)


def brand():
    st.markdown('<div class="brand"><svg width="29" height="29" viewBox="0 0 32 32" aria-hidden="true"><path d="M21 5H10L4 11v10l6 6h11l7-7V9M13 10l-4 4v5l4 4h6l4-4v-5l-4-4h-6M21 5v5M27 20h-5" fill="none" stroke="currentColor" stroke-width="2"/></svg><span>ConnectIQ</span></div>', unsafe_allow_html=True)


def title(name, subtitle):
    st.title(name)
    st.markdown(f'<p class="page-description">{html.escape(subtitle)}</p>', unsafe_allow_html=True)


def metrics(items):
    cells = "".join(f'<div class="metric"><div>{html.escape(label)}</div><strong>{html.escape(str(value))}</strong></div>' for label, value in items)
    st.markdown(f'<div class="metric-strip">{cells}</div>', unsafe_allow_html=True)


def table(frame, key=None, height=None):
    st.dataframe(frame, hide_index=True, use_container_width=True, height=height or "auto", key=key,
                 column_config={"LinkedIn URL": st.column_config.LinkColumn("LinkedIn profile", display_text="Open profile"),
                                "URL": st.column_config.LinkColumn("Search", display_text="Open search")})


def notice(message):
    st.markdown(f'<div class="empty-state">{html.escape(message)}</div>', unsafe_allow_html=True)


def mutation(engine, action, message="Saved."):
    try:
        with transaction(engine) as session:
            result = action(session)
        st.session_state["flash"] = message
        return True, result
    except ValidationError as exc:
        st.error(str(exc))
    except IntegrityError:
        st.error("This record conflicts with an existing record. Refresh and review duplicates before saving.")
    except StaleDataError:
        st.error("This record changed in another session. Refresh before saving again.")
    return False, None


def range_picker(tz, key):
    today = datetime.now(ZoneInfo(tz)).date()
    value = st.date_input("Reporting period", value=(today - timedelta(days=29), today),
                          max_value=today, key=key, help="Chart and activity dates use your workspace timezone.")
    if not isinstance(value, tuple) or len(value) != 2:
        st.info("Choose both a start and an end date.")
        return None
    if (value[1] - value[0]).days > 3660:
        st.error("Choose a period of at most ten years.")
        return None
    return value


def instant_picker(label, tz, key, value=None):
    local = aware(value).astimezone(ZoneInfo(tz)) if value else datetime.now(ZoneInfo(tz))
    a, b = st.columns([2, 1])
    day = a.date_input(label, value=local.date(), max_value=datetime.now(ZoneInfo(tz)).date(), key=key + "_date")
    clock = b.time_input("Time", value=local.time().replace(second=0, microsecond=0), key=key + "_time")
    return datetime.combine(day, clock or time(0), tzinfo=ZoneInfo(tz))


def chart(frame, series, kind="line", height=230, percent=False, key=None):
    colors = [ACCENT, "#a1a1a1", "#696969"]
    fig = go.Figure()
    for index, column in enumerate(series):
        kwargs = dict(x=frame["Date"], y=frame[column], name=column, marker_color=colors[index % 3])
        if kind == "bar":
            fig.add_trace(go.Bar(**kwargs))
        else:
            fig.add_trace(go.Scatter(**kwargs, mode="lines", line=dict(width=2, color=colors[index % 3], shape="linear")))
    light = st.session_state.get("light_mode", False)
    ink = "#595959" if light else "#b4b4b4"
    fig.update_layout(height=height, margin=dict(l=5, r=5, t=18, b=5), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, Segoe UI, sans-serif", size=12, color=ink),
                      showlegend=len(series) > 1, legend=dict(orientation="h", y=1.24, x=0),
                      hovermode="x unified", bargap=0.35)
    fig.update_xaxes(showgrid=False, nticks=5, tickangle=0, title=None)
    fig.update_yaxes(gridcolor="#dedede" if light else "#2a2a2a", nticks=4, rangemode="tozero", title=None,
                     ticksuffix="%" if percent else "", tickformat=".0f")
    if frame.empty or not frame[series].to_numpy().any():
        fig.update_yaxes(range=[0, 1], tickvals=[0, 1])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


def bars(frame, label, value, key):
    if frame.empty:
        notice("No prospects in this view yet.")
        return
    shown = frame.head(10).iloc[::-1]
    fig = go.Figure(go.Bar(x=shown[value], y=shown[label], orientation="h", marker_color=ACCENT,
                           hovertemplate="%{y}: %{x}<extra></extra>"))
    light = st.session_state.get("light_mode", False)
    fig.update_layout(height=230, margin=dict(l=0, r=10, t=10, b=0), paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, Segoe UI, sans-serif", size=12, color="#595959" if light else "#b4b4b4"))
    fig.update_xaxes(rangemode="tozero", nticks=4, tickformat=".0f", gridcolor="#dedede" if light else "#2a2a2a")
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


def import_summary(summary):
    data = summary.as_dict() if hasattr(summary, "as_dict") else summary
    st.success(f"{data['imported']} imported · {len(data['duplicates'])} duplicates · {len(data['invalid'])} invalid · {data['total']} rows reviewed")
    if data["duplicates"]:
        st.write("Duplicate records (skipped)")
        table(pd.DataFrame(data["duplicates"]))
    if data["invalid"]:
        st.write("Invalid records (skipped)")
        table(pd.DataFrame(data["invalid"]))


def csv_upload(engine, kind, user_id, key):
    from connectiq.csv_io import import_companies, import_prospects
    upload = st.file_uploader(f"Import {kind} CSV", type="csv", key=key)
    st.caption("UTF-8 · maximum 5 MB / 10,000 rows · existing records are preserved")
    if st.button("Validate and import", key=key + "_run", disabled=upload is None):
        action = (lambda s: import_companies(s, upload.getvalue())) if kind == "companies" else (lambda s: import_prospects(s, user_id, upload.getvalue()))
        ok, result = mutation(engine, action, "CSV import completed.")
        if ok:
            st.session_state[key + "_summary"] = result.as_dict()
            st.rerun()
    if key + "_summary" in st.session_state:
        import_summary(st.session_state[key + "_summary"])
