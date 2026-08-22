from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.market_schedule import MarketSchedule
from nandi_v2.charting import candlestick_chart_html
from nandi_v2.engine import limited_rows, nearest_atm
from nandi_v2.nse import NSEDataError, NSEPublicClient


IST = ZoneInfo("Asia/Kolkata")
DISPLAY_INTERVAL = 5


@st.cache_data(ttl=300, show_spinner=False)
def _last_nifty_candles(access_token: str):
    return UpstoxOptionChainClient(
        access_token=access_token,
        timeout_seconds=15,
    ).fetch_intraday_candles(DISPLAY_INTERVAL)


@st.cache_data(ttl=300, show_spinner=False)
def _last_nse_snapshot():
    # A new client is intentional here. Research Mode may display the exchange's
    # unchanged final snapshot, but it never feeds Shiv's live decision gate.
    return NSEPublicClient(timeout_seconds=15).fetch_option_chain("NIFTY")


@st.cache_data(ttl=300, show_spinner=False)
def _last_option_window(access_token: str, expiry: str, spot: float):
    return UpstoxOptionChainClient(
        access_token=access_token,
        timeout_seconds=15,
    ).fetch_option_window_intraday_candles(
        expiry,
        spot,
        DISPLAY_INTERVAL,
        wings=2,
    )


def _snapshot_frame(snapshot) -> pd.DataFrame:
    rows = list(limited_rows(snapshot, wings=2))
    if not rows:
        return pd.DataFrame()
    atm = nearest_atm(rows, snapshot.spot).strike
    atm_index = min(range(len(rows)), key=lambda index: abs(rows[index].strike - atm))
    records = []
    for index, row in enumerate(rows):
        offset = index - atm_index
        label = {
            -2: "ATM -2",
            -1: "ATM -1",
            0: "ATM",
            1: "ATM +1",
            2: "ATM +2",
        }.get(offset, f"ATM {offset:+d}")
        records.append(
            {
                "Position": label,
                "CE LTP": row.ce.ltp,
                "CE OI": row.ce.oi,
                "CE Volume": row.ce.volume,
                "CE Bid": row.ce.bid,
                "CE Ask": row.ce.ask,
                "Strike": row.strike,
                "PE Bid": row.pe.bid,
                "PE Ask": row.pe.ask,
                "PE Volume": row.pe.volume,
                "PE OI": row.pe.oi,
                "PE LTP": row.pe.ltp,
            }
        )
    return pd.DataFrame(records)


def _render_last_option_charts(window) -> None:
    atm = next((item for item in window if item.offset == 0), None)
    if atm is None:
        st.warning("The last ATM option pair is unavailable.")
        return
    left, right = st.columns(2, gap="large")
    with left:
        components.html(
            candlestick_chart_html(
                atm.ce_candles,
                interval_minutes=DISPLAY_INTERVAL,
                title=f"NIFTY {atm.strike:.0f} CE",
                subtitle="Last available ATM call candles · display only",
                evidence_note="STALE / DISPLAY ONLY outside market hours. No Shiv CE signal is generated from this chart.",
                chart_height=330,
            ),
            height=445,
            scrolling=False,
        )
    with right:
        components.html(
            candlestick_chart_html(
                atm.pe_candles,
                interval_minutes=DISPLAY_INTERVAL,
                title=f"NIFTY {atm.strike:.0f} PE",
                subtitle="Last available ATM put candles · display only",
                evidence_note="STALE / DISPLAY ONLY outside market hours. No Shiv PE signal is generated from this chart.",
                chart_height=330,
            ),
            height=445,
            scrolling=False,
        )


def render_after_hours(access_token: str) -> None:
    now = datetime.now(IST)
    status = MarketSchedule().status(now)

    st.markdown(
        """
        <style>
        :root{--shiv:#126b3a;--ink:#14251c;--muted:#66756d;--line:#dce8e1;--soft:#f6faf7;--amber:#8a5a05}
        .stApp{background:#fff;color:var(--ink)}
        .block-container{max-width:1500px;padding-top:1.2rem;padding-bottom:4rem}
        .closed-hero{border:1px solid var(--line);border-radius:20px;padding:1.35rem 1.5rem;background:linear-gradient(112deg,#fff 55%,#edf7f1 100%);margin-bottom:1rem}
        .kicker{font-size:.72rem;letter-spacing:.14em;font-weight:800;color:var(--shiv);text-transform:uppercase}
        .title{font-size:2rem;font-weight:850;letter-spacing:-.04em;margin:.15rem 0}
        .copy{color:var(--muted);max-width:1020px}
        .stale{border:1px solid #ead8ac;background:#fffaf0;border-radius:14px;padding:.8rem 1rem;color:#604300;margin:.75rem 0 1rem}
        div[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="closed-hero">
          <div class="kicker">Shiv Advanced V2 · Research Mode</div>
          <div class="title">Market closed. Data visible, V2 decisions locked.</div>
          <div class="copy">Advanced V2 is installed. Shiv still displays the last available NIFTY and option-market data after hours, while regime/time/volatility/M-W/contract-selection and paper-entry logic stay locked until fresh regular-session data resumes.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    session_metrics = st.columns(4)
    session_metrics[0].metric("Session", status.label)
    session_metrics[1].metric("Current IST", now.strftime("%H:%M"))
    session_metrics[2].metric("Next NSE open", status.next_open.strftime("%d %b · %H:%M"))
    session_metrics[3].metric("Live V2 gate", "LOCKED")

    st.info(f"{status.reason} Stale/last-session data remains visible below, but Shiv V2 will not trade or confirm a side from it.")

    snapshot = None
    try:
        snapshot = _last_nse_snapshot()
    except NSEDataError as exc:
        st.warning(f"The last NSE option-chain snapshot is temporarily unavailable: {exc}")

    if snapshot is not None:
        exchange_timestamp = snapshot.raw_timestamp or snapshot.timestamp.astimezone(IST).strftime("%d-%b-%Y %H:%M:%S")
        st.markdown(
            f'<div class="stale"><b>STALE / DISPLAY ONLY</b> · NSE exchange timestamp: {exchange_timestamp} · expiry: {snapshot.expiry}. OI shown below is the last published OI level, not a fresh after-hours OI change.</div>',
            unsafe_allow_html=True,
        )
        market_metrics = st.columns(4)
        market_metrics[0].metric("Last NIFTY", f"{float(snapshot.spot):,.2f}")
        market_metrics[1].metric("Nearest expiry", snapshot.expiry)
        market_metrics[2].metric("Data state", "STALE")
        market_metrics[3].metric("Signal state", "DISABLED")

        st.subheader("Last ATM ±2 option-chain snapshot")
        frame = _snapshot_frame(snapshot)
        if frame.empty:
            st.warning("No nearby option rows are available in the last NSE snapshot.")
        else:
            st.dataframe(frame, use_container_width=True, hide_index=True)
            st.caption("LTP, OI, volume and quotes are last-published values. After-hours changes are intentionally not calculated or presented as fresh movement.")

    data_tab, chart_tab, status_tab = st.tabs(["Last market data", "Last charts", "Advanced V2 status"])

    with data_tab:
        if snapshot is None:
            st.info("Waiting for the last NSE snapshot. The charts tab may still be available from Upstox.")
        else:
            st.write(
                "The table above remains visible so you can review where NIFTY, ATM strikes, OI levels and option premiums finished the session. "
                "The live Shiv V2 decision engine remains completely locked until regular-market data becomes fresh again."
            )

    with chart_tab:
        st.subheader("Last read-only NIFTY session chart")
        try:
            candles = _last_nifty_candles(access_token)
        except (UpstoxAPIError, ValueError) as exc:
            st.warning(f"The after-hours NIFTY chart is temporarily unavailable: {exc}")
        else:
            components.html(
                candlestick_chart_html(
                    candles,
                    interval_minutes=DISPLAY_INTERVAL,
                    title="NIFTY 50",
                    subtitle="Last available Upstox V3 session candles · research view",
                    evidence_note="STALE / DISPLAY ONLY after hours. These candles do not create a live Shiv CE/PE signal.",
                    chart_height=500,
                ),
                height=615,
                scrolling=False,
            )

        if snapshot is not None:
            try:
                window = _last_option_window(access_token, snapshot.expiry, float(snapshot.spot))
            except (UpstoxAPIError, ValueError) as exc:
                st.warning(f"The last ATM option charts are temporarily unavailable: {exc}")
            else:
                st.subheader("Last ATM CE / PE charts")
                _render_last_option_charts(window)

    with status_tab:
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown(
                """
                **Adaptive decision engine ready**

                - Regime-specific thresholds
                - 1m / 3m / 5m / 15m NIFTY confirmation
                - Opening / midday / closing time-of-day rules
                - ATR / IV / expiry context
                - M/W structure confirmation
                - ATM + ATM ±2 + OI evidence
                """
            )
        with right:
            st.markdown(
                """
                **Execution and validation ready**

                - Intelligent ATM/near-ATM contract selector
                - False-breakout / premium-divergence filter
                - Setup expiry and MISSED / DO NOT CHASE states
                - Adaptive regime/volatility paper exits
                - Outcome-based calibration
                - Chronological walk-forward validation
                """
            )
        st.caption("Setup quality, M/W confidence and contract score are evidence measures, not probabilities. Historical win-rate metrics require completed outcomes.")

    st.caption("At the next regular NSE session, Shiv automatically returns to the full live Advanced V2 terminal when fresh data is available.")
