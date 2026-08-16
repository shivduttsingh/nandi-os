from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.configuration import is_configured_value
from nandi_v2.charting import candlestick_chart_html, completed_candles


IST = ZoneInfo("Asia/Kolkata")
TRADINGVIEW_NIFTY_URL = "https://www.tradingview.com/symbols/NSE-NIFTY/"

st.set_page_config(page_title="Nandi · Upstox NIFTY Chart", page_icon="N", layout="wide")

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
def candle_client(token: str) -> UpstoxOptionChainClient:
    return UpstoxOptionChainClient(access_token=token, timeout_seconds=15)


if "upstox_chart_candles" not in st.session_state:
    st.session_state.upstox_chart_candles = tuple()
if "upstox_chart_error" not in st.session_state:
    st.session_state.upstox_chart_error = ""

st.title("NIFTY Upstox · TradingView-style chart")
st.caption(
    "TradingView Lightweight Charts™ renders read-only Upstox V3 OHLC candles. "
    "Nandi's stable decision mode always uses completed 15-minute candles."
)

interval = st.radio(
    "Chart timeframe",
    (1, 3, 5, 15, 30, 60),
    index=3,
    horizontal=True,
    format_func=lambda value: f"{value}m",
)
controls = st.columns([1, 1, 2])
controls[0].button("Refresh now", type="primary", use_container_width=True)
controls[1].link_button("Open full TradingView", TRADINGVIEW_NIFTY_URL, use_container_width=True)
controls[2].caption(
    "A timeframe other than 15m changes this visual chart only; Nandi's stable entry context remains 15m."
)

token = configured_token()
if not token:
    st.warning("Add the read-only Upstox access token to Streamlit Secrets before opening the candle chart.")
    st.code('[upstox]\naccess_token = "YOUR_READ_ONLY_TOKEN"', language="toml")
    st.stop()


@st.fragment(run_every="30s")
def live_chart() -> None:
    try:
        candles = candle_client(token).fetch_intraday_candles(interval)
        st.session_state.upstox_chart_candles = candles
        st.session_state.upstox_chart_error = ""
    except (UpstoxAPIError, ValueError) as exc:
        st.session_state.upstox_chart_error = str(exc)

    candles = tuple(st.session_state.upstox_chart_candles)
    if not candles:
        st.info("Waiting for the first valid Upstox NIFTY candle response.")
        if st.session_state.upstox_chart_error:
            st.warning(st.session_state.upstox_chart_error)
        return
    complete = completed_candles(candles, datetime.now(IST), interval)
    structure = "WAIT / RANGE"
    if len(complete) >= 2:
        previous, latest = complete[-2], complete[-1]
        if latest.close > previous.high:
            structure = "BULLISH BREAKOUT"
        elif latest.close < previous.low:
            structure = "BEARISH BREAKDOWN"
    last = candles[-1]
    first = candles[0]
    metrics = st.columns(4)
    metrics[0].metric("NIFTY", f"{last.close:,.2f}", f"{last.close-first.open:+.2f}")
    metrics[1].metric("Session high", f"{max(item.high for item in candles):,.2f}")
    metrics[2].metric("Session low", f"{min(item.low for item in candles):,.2f}")
    metrics[3].metric("Closed-candle structure", structure)
    components.html(
        candlestick_chart_html(candles, interval_minutes=interval),
        height=625,
        scrolling=False,
    )
    last_complete = complete[-1].timestamp.strftime("%I:%M %p") if complete else "Waiting"
    st.caption(f"Last completed candle: {last_complete} IST · refresh: 30 seconds")
    if st.session_state.upstox_chart_error:
        st.warning("Latest refresh failed; the last valid chart remains visible. " + st.session_state.upstox_chart_error)


live_chart()
