from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import streamlit as st

from nandi_oi.auth import CredentialConfigurationError, LoginLockout
from nandi_oi.configuration import is_configured_value
from nandi_oi.market_schedule import MarketSchedule
from shiv_v1.after_hours import render_after_hours
from shiv_v2.live import render_shiv_terminal


IST = ZoneInfo("Asia/Kolkata")
st.set_page_config(page_title="Shiv", page_icon="S", layout="wide", initial_sidebar_state="expanded")


def secret_section(name: str) -> dict[str, Any]:
    try:
        value = st.secrets.get(name, {})
        return dict(value) if value else {}
    except Exception:
        return {}


def configured_auth() -> tuple[str | None, str | None, str]:
    auth = secret_section("auth")
    username = str(auth.get("username", ""))
    password = str(auth.get("password", ""))
    if not (is_configured_value(username) and is_configured_value(password)):
        username = os.getenv("NANDI_AUTH_USERNAME", "")
        password = os.getenv("NANDI_AUTH_PASSWORD", "")
    if is_configured_value(username) and is_configured_value(password):
        return username, password, ""
    return None, None, "Authentication is not configured. Add [auth] username/password to Shiv Streamlit Secrets."


def configured_upstox_token() -> str:
    token = str(secret_section("upstox").get("access_token", ""))
    if not is_configured_value(token):
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    return token if is_configured_value(token) else ""


def login_page() -> None:
    st.markdown(
        """
        <div style="max-width:620px;margin:4rem auto 1rem auto;border:1px solid #dce8e1;border-radius:20px;padding:1.5rem;background:#fff">
          <div style="font-size:.72rem;letter-spacing:.14em;font-weight:800;color:#126b3a;text-transform:uppercase">Shiv</div>
          <div style="font-size:2rem;font-weight:850;letter-spacing:-.04em">Private research terminal</div>
          <div style="color:#66756d;margin-top:.35rem">Advanced V2 NIFTY decision research. Read-only market data and paper simulation only.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    username_expected, password_expected, auth_error = configured_auth()
    _, middle, _ = st.columns([1, 1.1, 1])
    with middle:
        with st.form("shiv_login"):
            username = st.text_input("Email or username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in to Shiv", use_container_width=True)
        if submitted:
            try:
                result = LoginLockout(st.session_state).authenticate(
                    username,
                    password,
                    username_expected,
                    password_expected,
                )
            except CredentialConfigurationError:
                st.error(auth_error)
            else:
                if result.authenticated:
                    st.session_state.shiv_logged_in = True
                    st.rerun()
                elif result.locked:
                    st.error("Too many failed attempts. Please retry later.")
                else:
                    st.error(f"Invalid credentials. {result.attempts_remaining} attempt(s) remaining.")
        if auth_error:
            st.warning(auth_error)


def render_refresh_controls(session, now: datetime) -> None:
    """Always-visible market-data controls for live and research modes."""
    st.sidebar.markdown("### Data controls")
    if session.is_open:
        st.sidebar.success("AUTO-REFRESH ON · every 30 seconds")
        st.sidebar.caption("Live NIFTY, options and Shiv decisions recalculate automatically during the NSE session.")
    else:
        st.sidebar.info("RESEARCH MODE · live decisions paused")
        st.sidebar.caption("Refresh still reloads the latest available/stale market snapshot and charts.")

    if st.sidebar.button("Refresh Shiv now", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.session_state.shiv_manual_refresh_at = datetime.now(IST)
        st.rerun()

    refreshed_at = st.session_state.get("shiv_manual_refresh_at")
    if isinstance(refreshed_at, datetime):
        st.sidebar.caption(f"Last manual refresh: {refreshed_at.astimezone(IST).strftime('%H:%M:%S')} IST")
    else:
        st.sidebar.caption(f"Page checked: {now.strftime('%H:%M:%S')} IST")


if "shiv_logged_in" not in st.session_state:
    st.session_state.shiv_logged_in = False

if not st.session_state.shiv_logged_in:
    login_page()
    st.stop()

access_token = configured_upstox_token()
if not access_token:
    st.error("Shiv needs the [upstox] access_token in this app's Streamlit Secrets to load read-only market data.")
    st.stop()

now = datetime.now(IST)
session = MarketSchedule().status(now)
render_refresh_controls(session, now)

if session.is_open:
    render_shiv_terminal(access_token)
else:
    render_after_hours(access_token)

st.sidebar.divider()
if st.sidebar.button("Sign out", use_container_width=True):
    st.session_state.shiv_logged_in = False
    st.rerun()
