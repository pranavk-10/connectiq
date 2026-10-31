"""ConnectIQ private Streamlit entrypoint. No CRM content is rendered before authentication."""
import html
import logging
import os
import time
import uuid

import streamlit as st
from sqlalchemy import select

from connectiq.auth import authenticate, credential_fingerprint, valid_hash
from connectiq.bootstrap import initialize
from connectiq.config import get_config
from connectiq.db import make_engine, transaction
from connectiq.models import User
from connectiq.pages import PAGES
from connectiq.services import get_setting
from connectiq.ui import brand, style, title

st.set_page_config(page_title="ConnectIQ · Semiconductor networking", page_icon="connectiq/assets/favicon.svg",
                   layout="wide", initial_sidebar_state="auto")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("connectiq")


@st.cache_resource
def resources(config):
    engine = make_engine(config.database_url)
    summary = initialize(engine, config)
    return engine, summary


def sign_in(engine, config):
    fingerprint = credential_fingerprint(config.password_hash)
    auth = st.session_state.get("auth")
    if auth and auth["expires"] > time.time() and auth["fingerprint"] == fingerprint:
        return auth["user_id"]
    st.session_state.pop("auth", None)
    _, center, _ = st.columns([1, 1.3, 1])
    with center:
        brand()
        title("Your network, in focus.", "A private workspace for semiconductor connections.")
        with st.form("signin"):
            email = st.text_input("Email", max_chars=254)
            password = st.text_input("Password", type="password", max_chars=256)
            submit = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        st.caption("Your networking records are private. LinkedIn invitations and conversations stay in your hands.")
        if submit:
            with transaction(engine) as session:
                user_id = authenticate(session, email, password)
            if user_id:
                st.session_state["auth"] = dict(user_id=user_id, expires=time.time() + config.session_hours * 3600, fingerprint=fingerprint)
                # Never retain password widget values after a successful login.
                st.rerun()
            else:
                st.error("Unable to sign in. Check your credentials, or wait 15 minutes if the account is temporarily locked.")
    st.stop()


def main():
    style(st.session_state.get("light_mode", False))
    config = get_config()
    if not config.admin_email or not valid_hash(config.password_hash):
        brand()
        title("Set up your workspace", "Configure the private owner account to begin.")
        st.write("Run the local setup command below, then restart the application. For Render, set ADMIN_EMAIL, ADMIN_PASSWORD_HASH and STREAMLIT_SERVER_COOKIE_SECRET in the environment.")
        st.code("python -m connectiq.cli setup", language="bash")
        st.caption("No company or prospect information is available before sign-in.")
        return
    engine, summary = resources(config)
    user_id = sign_in(engine, config)
    with transaction(engine) as session:
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None:
            st.session_state.clear()
            st.rerun()
        prefs = get_setting(session, user_id, "preferences", {})
        appearance = get_setting(session, user_id, "appearance", {"light": False})
    tz = prefs.get("timezone", config.timezone)
    if "light_mode" not in st.session_state:
        st.session_state["light_mode"] = appearance["light"]
        style(appearance["light"])
    page = st.session_state.get("page", "Overview")
    with st.sidebar:
        brand()
        st.markdown('<div class="workspace">Semiconductor networking · India</div>', unsafe_allow_html=True)
        for name in PAGES:
            if st.button(name, type="primary" if page == name else "secondary", use_container_width=True, key="nav_" + name):
                st.session_state["page"] = name
                st.rerun()
        st.divider()
        st.write(user.display_name)
        st.caption("Private workspace · " + tz)
        if st.button("Sign out", use_container_width=True):
            st.session_state.clear()
            st.rerun()
    st.markdown(f'<div class="shell-line"><span>Workspace / {html.escape(page)}</span><span>{html.escape(user.display_name)} · Private</span></div>', unsafe_allow_html=True)
    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    if summary.get("status") == "missing_csv":
        st.warning(summary["message"])
    PAGES.get(page, PAGES["Overview"])(engine, user_id, tz)


try:
    main()
except Exception as exc:
    # SQLAlchemy exceptions may contain submitted PII; do not log exception text or locals.
    incident = uuid.uuid4().hex[:10]
    logger.error("Request failed incident=%s type=%s", incident, type(exc).__name__)
    st.error(f"The workspace could not complete this operation. Refresh and try again. Reference: {incident}.")
    st.caption("For startup issues, check the environment and run python -m connectiq.cli health. Your records are preserved.")
