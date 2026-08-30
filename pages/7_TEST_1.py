from __future__ import annotations

import os
from datetime import date

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.configuration import is_configured_value
from nandi_v2.charting import candlestick_chart_html
from test1 import Test1Signal, assess_test1_continuation
from test1.public_backtest import PUBLIC_SAMPLE_PROJECT, run_public_test1_backtest


st.set_page_config(page_title="TEST 1 · Continuation", page_icon="T", layout="wide")

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


@st.cache_data(show_spinner=False, ttl=3600)
def cached_public_backtest(start_iso: str, end_iso: str):
    return run_public_test1_backtest(date.fromisoformat(start_iso), date.fromisoformat(end_iso))


st.title("TEST 1 · Evidence-Confirmed Continuation")
st.caption(
    "Independent experimental setup. Live signals and historical validation stay separate from SHIV and A+++ so TEST 1 can be judged without changing those systems."
)

st.info(
    "TEST 1 reads 1m/5m/15m NIFTY structure plus ATM CE/PE premium flow, OI, volume and 15m price acceptance. "
    "Its score is an evidence/confluence score, not a guaranteed win probability."
)

live_tab, backtest_tab = st.tabs(["Live TEST 1", "Public Backtest Lab"])

with live_tab:
    token = configured_token()
    if not token:
        st.warning("Live TEST 1 needs the read-only Upstox token. The Public Backtest Lab works without it.")
    else:
        @st.fragment(run_every="30s")
        def live_test1() -> None:
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
                st.warning(f"TEST 1 live data unavailable: {exc}")
                return

            assessment = assess_test1_continuation(n1, n5, n15, ce1, pe1)

            headline = st.columns([1.5, 1, 1, 1])
            headline[0].metric("TEST 1 signal", assessment.signal.value)
            headline[1].metric("Evidence score", f"{assessment.score:.1f}/100")
            headline[2].metric("Move consumed", f"{assessment.move_consumed_pct:.1f}%")
            headline[3].metric("Late-entry risk", assessment.late_entry_risk)

            if assessment.signal in {Test1Signal.CONFIRMED_CE, Test1Signal.CONFIRMED_PE}:
                st.success(f"{assessment.signal.value}: live evidence is aligned.")
            elif assessment.signal in {Test1Signal.PREPARE_CE, Test1Signal.PREPARE_PE}:
                st.warning(f"{assessment.signal.value}: pressure is developing, but confirmation is not complete.")
            elif assessment.signal in {Test1Signal.LATE_SKIP_CE, Test1Signal.LATE_SKIP_PE}:
                st.error(f"{assessment.signal.value}: confirmation arrived after too much of the move was consumed.")
            elif assessment.signal == Test1Signal.WAIT:
                st.info("WAIT: CE/PE evidence does not have enough clean separation.")
            else:
                st.info("TEST 1 is waiting for sufficient live data.")

            st.subheader("Live evidence stack")
            evidence = st.columns(5)
            evidence[0].metric("Price structure", f"{assessment.price_structure_score:.1f}/30")
            evidence[1].metric("ATM premium flow", f"{assessment.premium_flow_score:.1f}/25")
            evidence[2].metric("OI confirmation", f"{assessment.oi_score:.1f}/20")
            evidence[3].metric("Volume", f"{assessment.volume_score:.1f}/15")
            evidence[4].metric("Acceptance", f"{assessment.acceptance_score:.1f}/10")

            st.caption(
                f"Nearest-expiry ATM: NIFTY {pair.strike:.0f} · {pair.expiry}. "
                "TEST 1 reads 1m + 5m + 15m NIFTY and 1m ATM CE/PE flow."
            )

            components.html(
                candlestick_chart_html(
                    n15,
                    interval_minutes=15,
                    title="NIFTY 50 · TEST 1",
                    subtitle=f"Live 15m context · {assessment.signal.value} · evidence {assessment.score:.1f}/100",
                    evidence_note="TEST 1 uses the latest available candle state for early continuation evidence and does not wait for a completed 15m confirmation candle.",
                    chart_height=520,
                ),
                height=635,
                scrolling=False,
            )

            details, blockers = st.columns(2, gap="large")
            with details:
                st.subheader("Evidence")
                for reason in assessment.reasons:
                    st.write(f"• {reason}")
            with blockers:
                st.subheader("Blocks / anti-chase checks")
                if assessment.blockers:
                    for blocker in assessment.blockers:
                        st.write(f"• {blocker}")
                else:
                    st.write("No active blocker detected.")

            st.caption("Live TEST 1 remains paper/research-only.")

        live_test1()

with backtest_tab:
    st.subheader("Independent public-data validation")
    st.write(
        "This lab runs the unchanged TEST 1 scoring logic against a third-party open-source historical snapshot. "
        "It does not read your Upstox token and cannot place orders."
    )
    st.caption(
        "Source: Bhav open-source offline NIFTY sample (MIT). The project documents Jul 2025–Jun 2026 coverage; "
        "June 2026 contains ATM ±2 option strikes."
    )
    st.link_button("View public dataset project", PUBLIC_SAMPLE_PROJECT)

    dates = st.columns(2)
    start_date = dates[0].date_input(
        "From",
        value=date(2026, 6, 1),
        min_value=date(2025, 7, 1),
        max_value=date(2026, 6, 30),
        key="test1_public_start",
    )
    end_date = dates[1].date_input(
        "To",
        value=date(2026, 6, 30),
        min_value=date(2025, 7, 1),
        max_value=date(2026, 6, 30),
        key="test1_public_end",
    )

    st.caption(
        "Primary success benchmark: +10 NIFTY points before -5 points within the next 15 minutes. "
        "If target and stop are both touched inside the same 1-minute candle, the test counts it as a loss (conservative)."
    )

    if st.button("Run TEST 1 public backtest", type="primary", use_container_width=True):
        if start_date > end_date:
            st.error("From date must be on or before To date.")
        else:
            try:
                with st.spinner("Downloading/caching the public sample and replaying TEST 1 candle by candle..."):
                    report = cached_public_backtest(start_date.isoformat(), end_date.isoformat())
            except Exception as exc:
                st.error(f"Public backtest could not run: {exc}")
            else:
                summary = report.as_summary()
                st.session_state["test1_public_report"] = report
                st.session_state["test1_public_summary"] = summary

    report = st.session_state.get("test1_public_report")
    summary = st.session_state.get("test1_public_summary")
    if report is not None and summary is not None:
        st.divider()
        st.subheader("Result")
        top = st.columns(5)
        top[0].metric("Confirmed signals", summary["total_confirmed_signals"])
        top[1].metric("10/5 win rate", f'{summary["primary_win_rate_pct"]:.1f}%')
        top[2].metric("Wins", summary["primary_wins"])
        top[3].metric("Losses", summary["primary_losses"])
        top[4].metric("Timeouts", summary["primary_timeouts"])

        direction = st.columns(4)
        direction[0].metric("CE 10/5 win", f'{summary["ce_primary_win_rate_pct"]:.1f}%')
        direction[1].metric("PE 10/5 win", f'{summary["pe_primary_win_rate_pct"]:.1f}%')
        direction[2].metric("Tested days", summary["tested_days"])
        direction[3].metric("Late/skip clusters", summary["late_skip_clusters"])

        st.subheader("Favorable excursion within 15 minutes")
        hits = st.columns(4)
        hits[0].metric("Reached +5 pts", f'{summary["mfe_hit_rate_5pt_pct"]:.1f}%')
        hits[1].metric("Reached +10 pts", f'{summary["mfe_hit_rate_10pt_pct"]:.1f}%')
        hits[2].metric("Reached +15 pts", f'{summary["mfe_hit_rate_15pt_pct"]:.1f}%')
        hits[3].metric("Reached +20 pts", f'{summary["mfe_hit_rate_20pt_pct"]:.1f}%')

        st.subheader("Close-to-close continuation")
        continuation = st.columns(3)
        continuation[0].metric("5 minutes", f'{summary["continuation_5m_pct"]:.1f}%')
        continuation[1].metric("10 minutes", f'{summary["continuation_10m_pct"]:.1f}%')
        continuation[2].metric("15 minutes", f'{summary["continuation_15m_pct"]:.1f}%')

        st.subheader("Anti-chase evidence")
        late = st.columns(3)
        late[0].metric("Late skips avoiding 10/5 loss", summary["late_skip_avoided_10_5"])
        late[1].metric("Late skips that would win", summary["late_skip_missed_10_5"])
        late[2].metric("Late skips neutral/timeout", summary["late_skip_neutral_10_5"])

        if report.trades:
            trade_df = pd.DataFrame(
                [
                    {
                        "Time": trade.timestamp,
                        "Side": trade.direction,
                        "Score": trade.score,
                        "Strike": trade.strike,
                        "Entry NIFTY": trade.entry,
                        "MFE pts": trade.mfe_points,
                        "MAE pts": trade.mae_points,
                        "5m move": trade.move_5m,
                        "10m move": trade.move_10m,
                        "15m move": trade.move_15m,
                        "10/5 result": trade.target_10_stop_5,
                    }
                    for trade in report.trades
                ]
            )
            st.dataframe(trade_df, use_container_width=True, hide_index=True)

        st.warning(
            "This is evidence from a fixed third-party historical snapshot, not a guarantee of future performance. "
            "Public-sample results should be cross-checked against Upstox replay before TEST 1 is used with real money."
        )
