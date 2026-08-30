from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.configuration import is_configured_value
from nandi_v2.charting import candlestick_chart_html, completed_candles
from nandi_v2.profit_first import (
    PUBLIC_DATA_URL,
    RULES,
    UpstoxProfitFirstHistory,
    run_public_backtest,
    signal_side,
)

IST = ZoneInfo("Asia/Kolkata")

st.set_page_config(page_title="PROFIT FIRST · Live + Backtest", page_icon="P", layout="wide")

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


@st.cache_data(show_spinner=False, ttl=86_400)
def cached_public_backtest(start_iso: str, end_iso: str):
    return run_public_backtest(date.fromisoformat(start_iso), date.fromisoformat(end_iso))


def render_summary(summary: dict) -> None:
    cols = st.columns(6)
    cols[0].metric("Trades", summary["trades"])
    cols[1].metric("Wins", summary["wins"])
    cols[2].metric("Losses", summary["losses"])
    cols[3].metric(
        "Win rate",
        "—" if summary["win_rate"] is None else f"{summary['win_rate']:.2f}%",
    )
    cols[4].metric("Net option points", f"{summary['net_points']:+.2f}")
    cols[5].metric(
        "Profit factor",
        "—" if summary["profit_factor"] is None else f"{summary['profit_factor']:.3f}",
    )
    extra = st.columns(3)
    extra[0].metric(
        "Expectancy / trade",
        "—" if summary["expectancy"] is None else f"{summary['expectancy']:+.2f}",
    )
    extra[1].metric(
        "Avg points / trading day",
        "—"
        if summary["avg_points_per_trading_day"] is None
        else f"{summary['avg_points_per_trading_day']:+.2f}",
    )
    extra[2].metric("Max drawdown", f"{summary['max_drawdown']:.2f} pts")


def render_results(summary: dict, trades: pd.DataFrame, daily: pd.DataFrame, monthly: pd.DataFrame) -> None:
    render_summary(summary)
    if not daily.empty:
        st.subheader("Daily result")
        st.dataframe(daily, use_container_width=True, hide_index=True)
    if not monthly.empty:
        st.subheader("Monthly result")
        st.dataframe(monthly, use_container_width=True, hide_index=True)
    if not trades.empty:
        with st.expander("Exact trade ledger"):
            st.dataframe(trades, use_container_width=True, hide_index=True)


for key, default in {
    "pf_live_position": None,
    "pf_live_closed": [],
    "pf_live_last_signal": "",
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


st.title("PROFIT FIRST · Midday Reversal")
st.caption("Same frozen strategy engine for live paper testing and historical backtesting.")

st.info(
    "12:00–14:00 IST · completed NIFTY 1-minute move ≥ +0.05% → buy ATM PE next minute · "
    "move ≤ -0.05% → buy ATM CE next minute · ATM is frozen from the signal candle close · "
    "one position at a time · exit after 30 option bars."
)

live_tab, backtest_tab, proof_tab = st.tabs(["Live paper test", "Backtest lab", "Validated proof"])


with live_tab:
    st.subheader("Live paper test")
    st.caption(
        "Read-only Upstox data. No broker order is sent. The paper tracker runs while this page is open."
    )
    token = configured_token()
    if not token:
        st.warning("The read-only Upstox token is not available in this Streamlit session.")
    else:

        @st.fragment(run_every="20s")
        def live_profit_first() -> None:
            client = market_client(token)
            now = datetime.now(IST)
            try:
                nifty = tuple(client.fetch_intraday_candles(1))
            except (UpstoxAPIError, ValueError) as exc:
                st.warning(f"Live NIFTY data unavailable: {exc}")
                return

            if not nifty:
                st.info("Waiting for NIFTY 1-minute candles.")
                return

            components.html(
                candlestick_chart_html(
                    nifty,
                    interval_minutes=1,
                    title="NIFTY 50 · PROFIT FIRST",
                    subtitle="Live read-only NIFTY 50 · strategy evidence chart",
                    evidence_note="Only completed 1-minute candles can create a PROFIT FIRST signal.",
                    chart_height=440,
                ),
                height=555,
                scrolling=False,
            )

            done = completed_candles(nifty, now, 1)
            if len(done) < 2:
                st.info("Waiting for at least two completed NIFTY candles.")
                return

            latest = done[-1]
            previous = done[-2]
            r1 = (latest.close / previous.close - 1.0) * 100.0
            minute = latest.timestamp.hour * 60 + latest.timestamp.minute
            side = (
                signal_side(r1)
                if RULES.signal_start_minute <= minute <= RULES.signal_end_minute
                else None
            )

            status = st.columns(5)
            status[0].metric("Latest completed candle", latest.timestamp.strftime("%H:%M"))
            status[1].metric("NIFTY close", f"{latest.close:,.2f}")
            status[2].metric("1m move", f"{r1:+.3f}%")
            status[3].metric("Trigger", f"±{RULES.trigger_pct:.2f}%")
            status[4].metric("Signal", f"BUY {side}" if side else "WAIT")

            position = st.session_state.pf_live_position

            if position:
                try:
                    option_candles = tuple(
                        client.fetch_instrument_intraday_candles(position["instrument_key"], 1)
                    )
                except (UpstoxAPIError, ValueError) as exc:
                    st.warning(f"Open paper trade could not refresh: {exc}")
                    option_candles = tuple()

                if option_candles:
                    entry_dt = datetime.fromisoformat(position["entry_dt"])
                    by_time = {candle.timestamp: index for index, candle in enumerate(option_candles)}
                    if entry_dt in by_time:
                        p = by_time[entry_dt]
                        target_index = p + RULES.hold_bars - 1
                        completed_options = completed_candles(option_candles, now, 1)
                        completed_times = {candle.timestamp for candle in completed_options}
                        latest_option = option_candles[-1]
                        open_pnl = latest_option.close - position["entry"] - RULES.friction

                        if target_index < len(option_candles):
                            target = option_candles[target_index]
                            if target.timestamp in completed_times:
                                pnl = target.close - position["entry"] - RULES.friction
                                closed = {
                                    **position,
                                    "exit_dt": target.timestamp.isoformat(),
                                    "exit": target.close,
                                    "pnl": pnl,
                                }
                                history = list(st.session_state.pf_live_closed)
                                history.append(closed)
                                st.session_state.pf_live_closed = history[-100:]
                                st.session_state.pf_live_position = None
                                position = None
                                st.success(
                                    f"Paper trade closed: {closed['side']} {closed['strike']:.0f} · "
                                    f"{pnl:+.2f} option points."
                                )

                        if position:
                            pcols = st.columns(5)
                            pcols[0].metric("Paper position", f"BUY {position['side']}")
                            pcols[1].metric("Strike", f"{position['strike']:.0f}")
                            pcols[2].metric("Entry", f"{position['entry']:.2f}")
                            pcols[3].metric("Latest premium", f"{latest_option.close:.2f}")
                            pcols[4].metric("Open paper P&L", f"{open_pnl:+.2f} pts")

            if st.session_state.pf_live_position is None and side:
                signal_key = latest.timestamp.isoformat()
                age_seconds = (
                    now.replace(tzinfo=None)
                    - (latest.timestamp + timedelta(minutes=1))
                ).total_seconds()
                fresh = -10 <= age_seconds <= 100
                if signal_key != st.session_state.pf_live_last_signal and fresh:
                    try:
                        pair = client.resolve_atm_option_instruments("current_week", latest.close)
                        instrument_key = (
                            pair.pe_instrument_key if side == "PE" else pair.ce_instrument_key
                        )
                        option_candles = tuple(
                            client.fetch_instrument_intraday_candles(instrument_key, 1)
                        )
                        entry_dt = latest.timestamp + timedelta(minutes=1)
                        entry_candle = next(
                            (candle for candle in option_candles if candle.timestamp == entry_dt),
                            None,
                        )
                        if entry_candle is not None:
                            st.session_state.pf_live_position = {
                                "signal_dt": latest.timestamp.isoformat(),
                                "entry_dt": entry_dt.isoformat(),
                                "side": side,
                                "strike": float(pair.strike),
                                "expiry": pair.expiry,
                                "instrument_key": instrument_key,
                                "signal_spot": latest.close,
                                "spot_r1_pct": r1,
                                "entry": entry_candle.open + RULES.entry_slippage,
                            }
                            st.session_state.pf_live_last_signal = signal_key
                            st.success(
                                f"LIVE PAPER ENTRY: BUY {side} · ATM {pair.strike:.0f} · "
                                f"simulated entry {entry_candle.open + RULES.entry_slippage:.2f}"
                            )
                        else:
                            st.info("Signal is armed; waiting for the next-minute option candle.")
                    except (UpstoxAPIError, ValueError) as exc:
                        st.warning(f"Signal detected but ATM option data is unavailable: {exc}")

            if not (RULES.signal_start_minute <= minute <= RULES.signal_end_minute):
                st.caption("Strategy is outside its 12:00–14:00 signal window.")

            closed = pd.DataFrame(st.session_state.pf_live_closed)
            if not closed.empty:
                st.subheader("Live paper trades from this session")
                st.metric("Session net option points", f"{closed['pnl'].sum():+.2f}")
                st.dataframe(closed, use_container_width=True, hide_index=True)

        live_profit_first()


with backtest_tab:
    st.subheader("Backtest lab")
    source = st.radio(
        "Data source",
        ["Public 1-minute workbook", "Recent Upstox history"],
        horizontal=True,
    )

    if source == "Public 1-minute workbook":
        st.caption(
            "Exact public sample used for the original research. Coverage: 3 Jul 2025–30 Jun 2026. "
            "No Upstox token is required."
        )
        dates = st.columns(2)
        start_date = dates[0].date_input(
            "From",
            value=date(2026, 1, 1),
            min_value=date(2025, 7, 3),
            max_value=date(2026, 6, 30),
            key="pf_public_from",
        )
        end_date = dates[1].date_input(
            "To",
            value=date(2026, 6, 30),
            min_value=date(2025, 7, 3),
            max_value=date(2026, 6, 30),
            key="pf_public_to",
        )
        st.caption(f"Dataset: {PUBLIC_DATA_URL}")
        if st.button("Run PROFIT FIRST public backtest", type="primary", use_container_width=True):
            if start_date > end_date:
                st.error("From date must be on or before To date.")
            else:
                try:
                    with st.spinner("Replaying the frozen rule candle by candle..."):
                        result = cached_public_backtest(start_date.isoformat(), end_date.isoformat())
                    render_results(*result)
                except Exception as exc:
                    st.error(f"Public backtest failed: {exc}")

    else:
        st.caption(
            "Uses Nandi's read-only Upstox historical NIFTY and option candles. "
            "Expired-option history requires Upstox Plus. One run is limited to 32 calendar days."
        )
        token = configured_token()
        if not token:
            st.warning("The read-only Upstox token is not available in this Streamlit session.")
        else:
            today = datetime.now(IST).date()
            minimum = today - timedelta(days=180)
            default_start = max(minimum, today - timedelta(days=7))
            dates = st.columns(2)
            start_date = dates[0].date_input(
                "From",
                value=default_start,
                min_value=minimum,
                max_value=today,
                key="pf_upstox_from",
            )
            end_date = dates[1].date_input(
                "To",
                value=today,
                min_value=minimum,
                max_value=today,
                key="pf_upstox_to",
            )
            if st.button("Run recent Upstox backtest", type="primary", use_container_width=True):
                try:
                    with st.spinner("Reading NIFTY + nearest-expiry ATM options from Upstox..."):
                        history = UpstoxProfitFirstHistory(token)
                        result = history.run_backtest(start_date, end_date)
                    render_results(*result)
                except Exception as exc:
                    st.error(f"Upstox backtest failed: {exc}")


with proof_tab:
    st.subheader("Corrected six-month proof")
    st.caption(
        "The earlier +943.43 research replay used the next-minute NIFTY close to choose ATM. "
        "That was a look-ahead leak. The corrected causal replay freezes ATM from the signal candle close."
    )
    proof = st.columns(6)
    proof[0].metric("Jan–Jun trades", "305")
    proof[1].metric("Wins / losses", "158 / 147")
    proof[2].metric("Win rate", "51.80%")
    proof[3].metric("Net option points", "+942.63")
    proof[4].metric("Expectancy", "+3.09 / trade")
    proof[5].metric("Profit factor", "1.437")

    proof_rows = pd.DataFrame(
        [
            ["Jan 2026", 42, 19, 23, 45.24, 250.25],
            ["Feb 2026", 39, 25, 14, 64.10, 144.80],
            ["Mar 2026", 69, 26, 43, 37.68, -31.52],
            ["Apr 2026", 55, 28, 27, 50.91, -48.10],
            ["May 2026", 52, 31, 21, 59.62, 131.05],
            ["Jun 2026", 48, 29, 19, 60.42, 496.15],
        ],
        columns=["Month", "Trades", "Wins", "Losses", "Win rate %", "Net option points"],
    )
    st.dataframe(proof_rows, use_container_width=True, hide_index=True)

    st.markdown(
        """
**Frozen execution rule**

- Signal only from completed NIFTY 1-minute candles between **12:00 and 14:00 IST**.
- **+0.05% or more NIFTY impulse → buy ATM PE.**
- **−0.05% or more NIFTY impulse → buy ATM CE.**
- ATM strike is selected from the **signal candle close**, so there is no next-minute look-ahead.
- Entry uses the next-minute option open **+0.20 points slippage**.
- Only one trade may be open.
- Exit after **30 option bars**, with another **0.50 points friction** deducted.
        """
    )

st.caption("PROFIT FIRST is paper/research-only. Historical performance does not guarantee future profit.")
