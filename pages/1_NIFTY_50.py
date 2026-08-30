from __future__ import annotations

import os

import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.configuration import is_configured_value
from nandi_v2.charting import candlestick_chart_html


TIMEFRAME_PRESETS = {
    "1 min": 1,
    "2 min": 2,
    "3 min": 3,
    "5 min": 5,
    "10 min": 10,
    "15 min": 15,
    "30 min": 30,
    "1 hour": 60,
}

st.set_page_config(page_title="Nandi · NIFTY 50", page_icon="N", layout="wide")

if not st.session_state.get("logged_in", False):
    st.error("Please sign in from the Nandi home page first.")
    st.stop()


def configured_token() -> str:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not is_configured_value(token):
        try:
            token = str(st.secrets.get("upstox", {}).get("access_token", ""))
        except Exception:
            token = ""
    return token if is_configured_value(token) else ""


@st.cache_resource
def market_client(token: str) -> UpstoxOptionChainClient:
    return UpstoxOptionChainClient(access_token=token, timeout_seconds=15)


st.title("NIFTY 50 · Read Only")
st.caption("NIFTY 50 market-data chart only. No broker order can be placed, modified, or cancelled from this page.")

choice = st.selectbox("Timeframe", tuple(TIMEFRAME_PRESETS), index=5)
interval = TIMEFRAME_PRESETS[choice]

token = configured_token()
if not token:
    st.warning("Nandi's read-only Upstox token is not available in this session.")
    st.stop()


@st.fragment(run_every="30s")
def render_nifty() -> None:
    client = market_client(token)
    try:
        candles = tuple(client.fetch_intraday_candles(interval))
    except (UpstoxAPIError, ValueError) as exc:
        st.warning(f"NIFTY 50 data unavailable: {exc}")
        return

    if not candles:
        st.info("Waiting for NIFTY 50 candles.")
        return

    latest = candles[-1]
    today_move = latest.close - candles[0].open
    metric = st.columns(3)
    metric[0].metric("NIFTY 50", f"{latest.close:,.2f}", f"{today_move:+.2f} today")
    metric[1].metric("Timeframe", choice)
    metric[2].metric("Mode", "Read only")

    components.html(
        candlestick_chart_html(
            candles,
            interval_minutes=interval,
            title="NIFTY 50",
            subtitle=f"NSE_INDEX|Nifty 50 · {choice} · Upstox read-only OHLC",
            evidence_note="Market-data view only. No order placement is available from this page.",
            chart_height=620,
        ),
        height=735,
        scrolling=False,
    )


render_nifty()
