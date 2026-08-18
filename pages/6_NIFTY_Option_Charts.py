from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.configuration import is_configured_value
from nandi_v2.atm_strategy import ATMConfirmationSignal, assess_atm_confirmation
from nandi_v2.charting import (
    candlestick_chart_html,
    completed_candles,
)
from nandi_v2.strike_window_ui import render_strike_window_charts


IST = ZoneInfo("Asia/Kolkata")
INTERVAL_MINUTES = 15

st.set_page_config(page_title="Nandi · NIFTY & ATM Option Charts", page_icon="N", layout="wide")

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


for key, value in {
    "standalone_nifty_candles": tuple(),
    "standalone_atm_ce_candles": tuple(),
    "standalone_atm_pe_candles": tuple(),
    "standalone_atm_strike": None,
    "standalone_atm_expiry": "",
    "standalone_chart_error": "",
    "standalone_strike_window_candles": tuple(),
    "standalone_strike_window_error": "",
}.items():
    if key not in st.session_state:
        st.session_state[key] = value

st.title("NIFTY 50 + live ATM option charts")
st.caption(
    "All three charts use read-only Upstox market data. The two premium charts automatically "
    "follow the nearest-expiry ATM CE and PE contracts; this page cannot place orders."
)

token = configured_token()
if not token:
    st.warning("Add the read-only Upstox token to load the live ATM CE and PE premium charts.")
    st.code('[upstox]\naccess_token = "YOUR_READ_ONLY_TOKEN"', language="toml")
    st.stop()


@st.fragment(run_every="30s")
def live_atm_pair() -> None:
    client = candle_client(token)
    try:
        nifty_candles = client.fetch_intraday_candles(INTERVAL_MINUTES)
        st.session_state.standalone_nifty_candles = nifty_candles
        st.session_state.standalone_chart_error = ""
    except (UpstoxAPIError, ValueError) as exc:
        st.session_state.standalone_chart_error = str(exc)

    nifty_candles = tuple(st.session_state.standalone_nifty_candles)
    if not nifty_candles:
        st.info("Waiting for NIFTY 50 candles from Upstox.")
        if st.session_state.standalone_chart_error:
            st.warning(st.session_state.standalone_chart_error)
        return

    st.subheader("NIFTY 50 · Upstox read only")
    st.metric(
        "NIFTY 50",
        f"{nifty_candles[-1].close:,.2f}",
        f"{nifty_candles[-1].close - nifty_candles[0].open:+.2f} today",
    )
    components.html(
        candlestick_chart_html(
            nifty_candles,
            interval_minutes=INTERVAL_MINUTES,
            title="NIFTY 50",
            subtitle="NSE_INDEX|Nifty 50 · read-only Upstox V3 OHLC data",
            evidence_note=(
                "Market-data view only: this chart cannot place or modify an Upstox order. "
                "The forming candle is display-only for Nandi's chart confirmation."
            ),
            chart_height=540,
        ),
        height=655,
        scrolling=False,
    )

    try:
        pair = client.resolve_atm_option_instruments("current_week", nifty_candles[-1].close)
        ce_candles = client.fetch_instrument_intraday_candles(
            pair.ce_instrument_key,
            INTERVAL_MINUTES,
        )
        pe_candles = client.fetch_instrument_intraday_candles(
            pair.pe_instrument_key,
            INTERVAL_MINUTES,
        )
        # Publish only a complete matching pair.
        st.session_state.standalone_atm_ce_candles = ce_candles
        st.session_state.standalone_atm_pe_candles = pe_candles
        st.session_state.standalone_atm_strike = pair.strike
        st.session_state.standalone_atm_expiry = pair.expiry
        st.session_state.standalone_chart_error = ""
    except (UpstoxAPIError, ValueError) as exc:
        st.session_state.standalone_chart_error = str(exc)

    ce_candles = tuple(st.session_state.standalone_atm_ce_candles)
    pe_candles = tuple(st.session_state.standalone_atm_pe_candles)
    strike = st.session_state.standalone_atm_strike
    expiry = st.session_state.standalone_atm_expiry
    if strike is None or not ce_candles or not pe_candles:
        st.info("Waiting for the nearest-expiry ATM CE and PE candle pair.")
        if st.session_state.standalone_chart_error:
            st.warning(st.session_state.standalone_chart_error)
        return

    st.subheader(f"Nearest-expiry ATM pair · NIFTY {strike:.0f} · {expiry}")
    ce_column, pe_column = st.columns(2, gap="large")
    for column, side, candles in (
        (ce_column, "CE", ce_candles),
        (pe_column, "PE", pe_candles),
    ):
        with column:
            st.metric(
                f"ATM {strike:.0f} {side}",
                f"₹{candles[-1].close:,.2f}",
                f"{candles[-1].close - candles[0].open:+.2f} today",
            )
            components.html(
                candlestick_chart_html(
                    candles,
                    interval_minutes=INTERVAL_MINUTES,
                    title=f"NIFTY {strike:.0f} {side}",
                    subtitle=f"Nearest-expiry ATM {side} premium · {expiry}",
                    evidence_note=f"Exact Upstox {side} contract premium chart; read only.",
                    chart_height=350,
                ),
                height=465,
                scrolling=False,
            )

    observed_at = datetime.now(IST)
    strategy = assess_atm_confirmation(
        completed_candles(nifty_candles, observed_at, INTERVAL_MINUTES),
        completed_candles(ce_candles, observed_at, INTERVAL_MINUTES),
        completed_candles(pe_candles, observed_at, INTERVAL_MINUTES),
    )
    metrics = st.columns(4)
    metrics[0].metric("NIFTY + ATM strategy", strategy.signal.value)
    metrics[1].metric("Agreement", f"{strategy.agreement_score:.1f}/100")
    metrics[2].metric(
        "ATM CE move",
        "—" if strategy.ce_change_pct is None else f"{strategy.ce_change_pct:+.2f}%",
    )
    metrics[3].metric(
        "ATM PE move",
        "—" if strategy.pe_change_pct is None else f"{strategy.pe_change_pct:+.2f}%",
    )
    message = strategy.reason + " Agreement is not a guaranteed win probability."
    if strategy.signal in {ATMConfirmationSignal.CONFIRM_CE, ATMConfirmationSignal.CONFIRM_PE}:
        st.success(message)
    elif strategy.signal == ATMConfirmationSignal.WAIT:
        st.warning(message)
    else:
        st.info(message)
    if st.session_state.standalone_chart_error:
        st.warning(
            "Latest refresh failed; the last complete chart set remains visible. "
            + st.session_state.standalone_chart_error
        )

    st.divider()
    st.subheader("Separate ATM ±2 strike strategy")
    st.caption(
        "The working single-ATM strategy above is unchanged. This additional paper strategy "
        "checks ATM, one and two strikes below, and one and two strikes above. All ten option "
        "charts are read-only Upstox market data and cannot place orders."
    )
    try:
        strike_window = client.fetch_option_window_intraday_candles(
            "current_week",
            nifty_candles[-1].close,
            INTERVAL_MINUTES,
            wings=2,
        )
        # Publish only when every CE/PE series in the five-strike window succeeds.
        st.session_state.standalone_strike_window_candles = strike_window
        st.session_state.standalone_strike_window_error = ""
    except (UpstoxAPIError, ValueError) as exc:
        st.session_state.standalone_strike_window_error = str(exc)

    strike_window = tuple(st.session_state.standalone_strike_window_candles)
    if strike_window:
        render_strike_window_charts(
            nifty_candles,
            strike_window,
            observed_at=datetime.now(IST),
            interval_minutes=INTERVAL_MINUTES,
        )
    else:
        st.info("Waiting for the complete ATM ±2 CE/PE chart window from Upstox.")
    if st.session_state.standalone_strike_window_error:
        st.warning(
            "Latest ATM ±2 refresh failed; any last complete read-only chart window remains "
            "visible. " + st.session_state.standalone_strike_window_error
        )


live_atm_pair()
