from __future__ import annotations

import os

import streamlit as st

from nandi_oi.auth import CredentialConfigurationError, LoginLockout
from nandi_oi.configuration import is_configured_value


st.set_page_config(page_title="Nandi", page_icon="N", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    .stApp { background: #ffffff; }
    .block-container { max-width: 1200px; padding-top: 2rem; }
    section[data-testid="stSidebar"] { background: #f7faf8; }
    </style>
    """,
    unsafe_allow_html=True,
)


def secret_section(name: str) -> dict:
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
    return None, None, "Authentication is not configured. Add auth.username and auth.password to Streamlit Secrets."


APP_USERNAME, APP_PASSWORD, AUTH_ERROR = configured_auth()

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False


if not st.session_state.logged_in:
    st.title("Nandi")
    st.caption("Private NIFTY research terminal")
    _, middle, _ = st.columns([1, 1.1, 1])
    with middle:
        with st.container(border=True):
            st.subheader("Sign in")
            with st.form("login_form"):
                username = st.text_input("Email or username")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign in", use_container_width=True)
            if submitted:
                try:
                    result = LoginLockout(st.session_state).authenticate(
                        username,
                        password,
                        APP_USERNAME,
                        APP_PASSWORD,
                    )
                except CredentialConfigurationError:
                    st.error(AUTH_ERROR)
                else:
                    if result.authenticated:
                        st.session_state.logged_in = True
                        st.rerun()
                    elif result.locked:
                        st.error("Too many failed attempts. Please retry later.")
                    else:
                        st.error(f"Invalid credentials. {result.attempts_remaining} attempt(s) remaining.")
            if AUTH_ERROR:
                st.warning(AUTH_ERROR)
    st.stop()


st.title("Nandi")
st.caption("Clean research view: NIFTY 50 read-only chart and PROFIT FIRST only.")

left, right = st.columns(2, gap="large")
with left:
    with st.container(border=True):
        st.subheader("NIFTY 50 · Read Only")
        st.write("Live NIFTY 50 chart from the existing read-only Upstox market-data connection.")
        st.page_link("pages/1_NIFTY_50.py", label="Open NIFTY 50", use_container_width=True)

with right:
    with st.container(border=True):
        st.subheader("PROFIT FIRST")
        st.write("Frozen midday reversal setup with the +943.43-point Jan–Jun research replay and exact recent-week replay control.")
        st.page_link("pages/8_PROFIT_FIRST.py", label="Open PROFIT FIRST", use_container_width=True)

st.caption("Research-only. Nandi does not place broker orders from these pages.")
