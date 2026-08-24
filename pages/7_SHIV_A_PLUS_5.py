from __future__ import annotations

import os

import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.configuration import is_configured_value
from nandi_v2.charting import candlestick_chart_html
from shiv import A5Signal, assess_a5_continuation


st.set_page_config(page_title="SHIV · A+++++", page_icon="S", layout="wide")

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


st.title("SHIV · A+++++")
st.caption(
    "Live evidence-confirmed continuation setup. This engine intentionally reads the current "
    "market state instead of waiting for a completed 15-minute confirmation candle."
)

st.info(
    "A+++++ score is a live confluence score, not a promised win probability. "
    "CONFIRMED requires price structure, ATM premium flow, OI/volume evidence and price acceptance; "
    "late moves are blocked instead of chased."
)

token = configured_token()
if not token:
    st.warning("Add the read-only Upstox access token to run SHIV A+++++.")
    st.stop()


@st.fragment(run_every="30s")
def live_a5() -> None:
    client = market_client(token)
    try:
        n1 = tuple(client.fetch_intraday_candles(1))
        n5 = tuple(client.fetch_intraday_candles(5))
        n15 = tuple(client.fetch_intraday_candles(15))
        if not n1 or not n5 or not n15:
            st.info("Waiting for NIFTY live candles.")
            return

        spot = n1[-1].close
        pair = client.resolve_atm_option_instruments("current_week", spot)
        ce1 = tuple(client.fetch_instrument_intraday_candles(pair.ce_instrument_key, 1))
        pe1 = tuple(client.fetch_instrument_intraday_candles(pair.pe_instrument_key, 1))
    except (UpstoxAPIError, ValueError) as exc:
        st.warning(f"Live A+++++ data unavailable: {exc}")
        return

    assessment = assess_a5_continuation(n1, n5, n15, ce1, pe1)

    headline = st.columns([1.5, 1, 1, 1])
    headline[0].metric("A+++++ signal", assessment.signal.value)
    headline[1].metric("Evidence score", f"{assessment.score:.1f}/100")
    headline[2].metric("Move consumed", f"{assessment.move_consumed_pct:.1f}%")
    headline[3].metric("Late-entry risk", assessment.late_entry_risk)

    if assessment.signal in {A5Signal.CONFIRMED_CE, A5Signal.CONFIRMED_PE}:
        st.success(
            f"{assessment.signal.value}: live price, premium, OI/volume and acceptance evidence are aligned."
        )
    elif assessment.signal in {A5Signal.PREPARE_CE, A5Signal.PREPARE_PE}:
        st.warning(f"{assessment.signal.value}: pressure is developing, but confirmation is not complete.")
    elif assessment.signal in {A5Signal.LATE_SKIP_CE, A5Signal.LATE_SKIP_PE}:
        st.error(f"{assessment.signal.value}: evidence arrived after too much of the move was consumed.")
    elif assessment.signal == A5Signal.WAIT:
        st.info("WAIT: live evidence does not have enough clean directional separation.")
    else:
        st.info("A+++++ is waiting for sufficient live data.")

    st.subheader("Live evidence stack")
    evidence = st.columns(5)
    evidence[0].metric("Price structure", f"{assessment.price_structure_score:.1f}/30")
    evidence[1].metric("ATM premium flow", f"{assessment.premium_flow_score:.1f}/25")
    evidence[2].metric("OI confirmation", f"{assessment.oi_score:.1f}/20")
    evidence[3].metric("Volume", f"{assessment.volume_score:.1f}/15")
    evidence[4].metric("Acceptance", f"{assessment.acceptance_score:.1f}/10")

    st.caption(
        f"Nearest-expiry ATM: NIFTY {pair.strike:.0f} · {pair.expiry}. "
        "The strategy reads 1m + 5m + 15m NIFTY structure and 1m ATM CE/PE premium/OI flow."
    )

    components.html(
        candlestick_chart_html(
            n15,
            interval_minutes=15,
            title="NIFTY 50 · SHIV A+++++",
            subtitle=(
                f"Live 15m context · signal {assessment.signal.value} · "
                f"evidence {assessment.score:.1f}/100"
            ),
            evidence_note=(
                "A+++++ uses the latest available candle state for early continuation evidence. "
                "It does not wait for a completed 15m confirmation candle."
            ),
            chart_height=520,
        ),
        height=635,
        scrolling=False,
    )

    details, blockers = st.columns(2, gap="large")
    with details:
        st.subheader("Why SHIV sees this")
        if assessment.reasons:
            for reason in assessment.reasons:
                st.write(f"• {reason}")
        else:
            st.write("Waiting for evidence.")
    with blockers:
        st.subheader("Blocks / anti-chase checks")
        if assessment.blockers:
            for blocker in assessment.blockers:
                st.write(f"• {blocker}")
        else:
            st.write("No active blocker detected.")

    st.caption(
        "Paper/research use: validate this strategy with timestamp-accurate intraday replay before "
        "treating any score bucket as a measured hit rate."
    )


live_a5()
