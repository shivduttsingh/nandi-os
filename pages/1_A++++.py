from __future__ import annotations

import os

import streamlit as st

from nandi_oi.configuration import is_configured_value
from shiv_v1.ui import PRIMARY_TIMEFRAMES
from shiv_v2 import a4_ui


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


def _reset_a4_timeframe_state() -> None:
    """Do not carry persistence/setup-age evidence across primary timeframes."""
    for key in (
        "a4_last_snapshot_key",
        "a4_last_side",
        "a4_persistence",
        "a4_first_side",
        "a4_first_strike",
        "a4_first_seen_at",
        "a4_first_premium",
    ):
        st.session_state.pop(key, None)


active_paper = st.session_state.get("a4_paper_trade")
selected_timeframe = st.sidebar.selectbox(
    "A++++ primary timeframe",
    PRIMARY_TIMEFRAMES,
    index=PRIMARY_TIMEFRAMES.index(5),
    format_func=lambda value: f"{value}m" if value < 60 else "1h",
    key="a4_primary_timeframe",
    disabled=active_paper is not None,
    on_change=_reset_a4_timeframe_state,
)
st.sidebar.caption(
    "1m/3m/5m/15m NIFTY context remains the background confirmation set. The selected timeframe drives the A++++ primary regime, options, entry and validation bucket."
)
if active_paper is not None:
    st.sidebar.caption("Timeframe is locked while an A++++ paper trade is active.")

# A++++ was originally fixed to 5m. The research engine reads this module-level
# primary interval dynamically, so expose it as the user's selected timeframe.
a4_ui.PRIMARY_INTERVAL = int(selected_timeframe)

a4_ui.render_a4_terminal(access_token)
