from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.market_schedule import MarketSchedule
from nandi_v2.charting import candlestick_chart_html


IST = ZoneInfo("Asia/Kolkata")


@st.cache_data(ttl=300, show_spinner=False)
def _last_nifty_candles(access_token: str):
    return UpstoxOptionChainClient(
        access_token=access_token,
        timeout_seconds=15,
    ).fetch_intraday_candles(5)


def render_after_hours(access_token: str) -> None:
    now = datetime.now(IST)
    status = MarketSchedule().status(now)

    st.markdown(
        """
        <style>
        :root{--shiv:#126b3a;--ink:#14251c;--muted:#66756d;--line:#dce8e1;--soft:#f6faf7}
        .stApp{background:#fff;color:var(--ink)}
        .block-container{max-width:1500px;padding-top:1.2rem;padding-bottom:4rem}
        .closed-hero{border:1px solid var(--line);border-radius:20px;padding:1.35rem 1.5rem;background:linear-gradient(112deg,#fff 55%,#edf7f1 100%);margin-bottom:1rem}
        .kicker{font-size:.72rem;letter-spacing:.14em;font-weight:800;color:var(--shiv);text-transform:uppercase}
        .title{font-size:2rem;font-weight:850;letter-spacing:-.04em;margin:.15rem 0}
        .copy{color:var(--muted);max-width:960px}
        .status-card{border:1px solid var(--line);border-radius:16px;padding:1rem;background:#fff}
        div[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="closed-hero">
          <div class="kicker">Shiv · Research Mode</div>
          <div class="title">Market closed. Live decisions are locked.</div>
          <div class="copy">Shiv Advanced V1 is installed. Outside the regular NSE session, the system never converts stale option-chain data into CE/PE signals. Research charts remain read-only and the live decision engine resumes when fresh session data is available.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    metrics = st.columns(4)
    metrics[0].metric("Session", status.label)
    metrics[1].metric("Current IST", now.strftime("%H:%M"))
    metrics[2].metric("Next NSE open", status.next_open.strftime("%d %b · %H:%M"))
    metrics[3].metric("Live trade gate", "LOCKED")

    st.info(f"{status.reason} No new paper entry, persistence update, or CE/PE confirmation is generated in Research Mode.")

    st.subheader("Shiv Advanced V1 status")
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(
            """
            **Live engine ready**

            - Market-regime classification
            - 1m / 3m / 5m / 15m NIFTY confirmation
            - ATM + ATM ±2 option-premium evidence
            - OI / execution confirmation
            - Developing → Ready → Confirm → Strong → A+ stages
            """
        )
    with right:
        st.markdown(
            """
            **Risk and validation ready**

            - Persistence / anti-flip filter
            - No-trade engine
            - Pullback / breakout entry timing
            - Dynamic paper stop and target ladder
            - Similar-setup results only from recorded paper outcomes
            """
        )

    st.subheader("Last read-only NIFTY session chart")
    try:
        candles = _last_nifty_candles(access_token)
    except (UpstoxAPIError, ValueError) as exc:
        st.warning(f"The after-hours NIFTY chart is temporarily unavailable: {exc}")
    else:
        components.html(
            candlestick_chart_html(
                candles,
                interval_minutes=5,
                title="NIFTY 50",
                subtitle="Last available Upstox V3 session candles · research view",
                evidence_note="After-hours display only. These candles do not create a live Shiv CE/PE signal while the NSE session is closed.",
                chart_height=500,
            ),
            height=615,
            scrolling=False,
        )

    st.caption("At 09:15 IST on the next trading session, Shiv switches back to the full live Advanced V1 terminal automatically.")
