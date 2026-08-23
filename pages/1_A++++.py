from __future__ import annotations

import os

import streamlit as st

from nandi_oi.configuration import is_configured_value
from shiv_v2.a4_ui import render_a4_terminal


st.set_page_config(page_title="A++++", page_icon="A", layout="wide", initial_sidebar_state="expanded")


if not st.session_state.get("shiv_logged_in", False):
    st.warning("Sign in from the Shiv app first, then open A++++.")
    try:
        st.page_link("shiv_app.py", label="Open Shiv app")
    except Exception:
        pass
    st.stop()


try:
    upstox = dict(st.secrets.get("upstox", {}))
except Exception:
    upstox = {}

access_token = str(upstox.get("access_token", ""))
if not is_configured_value(access_token):
    access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")

if not is_configured_value(access_token):
    st.error("A++++ needs the [upstox] access_token in this app's Streamlit Secrets to load read-only market data.")
    st.stop()


render_a4_terminal(access_token)
