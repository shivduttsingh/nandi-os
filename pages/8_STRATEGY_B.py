from __future__ import annotations

import os
from datetime import date

import pandas as pd
import streamlit as st

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.configuration import is_configured_value
from strategy_b import StrategyBSignal, assess_strategy_b, run_public_strategy_b_backtest


st.set_page_config(page_title="Strategy B · Realistic", page_icon="B", layout="wide")

if not st.session_state.get("logged_in", False):
    st.error("Please sign in from the Nandi home page first.")
    st.stop()


PROFILES = {
    "BALANCED": {"threshold": 82.0, "cooldown": 10},
    "STRICT": {"threshold": 88.0, "cooldown": 15},
    "ELITE": {"threshold": 92.0, "cooldown": 20},
}


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
def cached_backtest(start_iso: str, end_iso: str, threshold: float, cooldown: int):
    return run_public_strategy_b_backtest(
        date.fromisoformat(start_iso),
        date.fromisoformat(end_iso),
        threshold=threshold,
        cooldown_minutes=cooldown,
    )


st.title("Strategy B · Realistic Option-Buyer Setup")
st.caption(
    "Independent paper/research strategy. It does not modify SHIV, A+++ or TEST 1 and cannot place broker orders."
)
st.info(
    "Strategy B scores structure (20), OI (20), premium flow (15), option volume (15), breakout/retest (10), "
    "momentum (10), and two-candle confirmation (10). The score is confluence, not a guaranteed win probability."
)

live_tab, backtest_tab = st.tabs(["Live Strategy B", "Realistic Backtest"])

with live_tab:
    profile_name = st.selectbox("Live strictness", list(PROFILES), index=1, key="strategy_b_live_profile")
    threshold = PROFILES[profile_name]["threshold"]
    token = configured_token()
    if not token:
        st.warning("Live Strategy B needs the read-only Upstox token. The historical backtest works without it.")
    else:
        @st.fragment(run_every="30s")
        def live_strategy_b() -> None:
            client = market_client(token)
            try:
                n1 = tuple(client.fetch_intraday_candles(1))
                n5 = tuple(client.fetch_intraday_candles(5))
                n15 = tuple(client.fetch_intraday_candles(15))
                if not n1 or not n5 or not n15:
                    st.info("Waiting for NIFTY candles.")
                    return
                spot = n1[-1].close
                pair = client.resolve_atm_option_instruments("current_week", spot)
                ce1 = tuple(client.fetch_instrument_intraday_candles(pair.ce_instrument_key, 1))
                pe1 = tuple(client.fetch_instrument_intraday_candles(pair.pe_instrument_key, 1))
            except (UpstoxAPIError, ValueError) as exc:
                st.warning(f"Strategy B live data unavailable: {exc}")
                return

            assessment = assess_strategy_b(
                n1[-24:], n5[-10:], n15[-6:], ce1[-24:], pe1[-24:], trade_threshold=threshold
            )
            top = st.columns(5)
            top[0].metric("Signal", assessment.signal.value)
            top[1].metric("Score", f"{assessment.score:.1f}/100")
            top[2].metric("Opposite", f"{assessment.opposite_score:.1f}/100")
            top[3].metric("Gap", f"{assessment.score_gap:.1f}")
            top[4].metric("ATM strike", f"{pair.strike:.0f}")

            if assessment.signal in {StrategyBSignal.TRADE_CE, StrategyBSignal.TRADE_PE}:
                st.success(
                    f"{assessment.signal.value}: all mandatory filters are aligned. Paper model: +10 premium target / -5 premium stop."
                )
            elif assessment.signal in {StrategyBSignal.BLOCKED_CE, StrategyBSignal.BLOCKED_PE}:
                st.error(f"{assessment.signal.value}: score is high but a hard safety/quality filter rejects the entry.")
            elif assessment.signal in {StrategyBSignal.WATCH_CE, StrategyBSignal.WATCH_PE}:
                st.warning(f"{assessment.signal.value}: evidence is developing; no entry yet.")
            else:
                st.info("WAIT: Strategy B does not have enough aligned evidence.")

            parts = st.columns(7)
            labels = [
                ("Structure", assessment.structure_score, 20),
                ("OI", assessment.oi_score, 20),
                ("Premium", assessment.premium_score, 15),
                ("Volume", assessment.volume_score, 15),
                ("Breakout", assessment.breakout_score, 10),
                ("Momentum", assessment.momentum_score, 10),
                ("2-candle", assessment.confirmation_score, 10),
            ]
            for col, (label, value, max_value) in zip(parts, labels):
                col.metric(label, f"{value:.1f}/{max_value}")

            left, right = st.columns(2, gap="large")
            with left:
                st.subheader("Evidence")
                for reason in assessment.reasons:
                    st.write(f"• {reason}")
            with right:
                st.subheader("Hard blockers")
                if assessment.blockers:
                    for blocker in assessment.blockers:
                        st.write(f"• {blocker}")
                else:
                    st.write("No active blocker.")

            st.caption(
                f"Nearest-expiry ATM NIFTY {pair.strike:.0f} · {pair.expiry}. Live mode is read-only/paper-only."
            )

        live_strategy_b()

with backtest_tab:
    st.subheader("Option-premium replay")
    st.write(
        "Signals use only candles available at the signal minute. Execution occurs at the next 1-minute ATM option open, "
        "with 0.25 premium-point entry slippage and another 0.50 points of round-trip friction."
    )
    st.caption(
        "Benchmark: +10 premium points before -5 premium points, maximum 15 minutes. If both target and stop appear inside "
        "the same 1-minute candle, it is counted as a loss."
    )

    profile = st.selectbox("Backtest profile", list(PROFILES), index=1, key="strategy_b_bt_profile")
    selected = PROFILES[profile]
    dates = st.columns(2)
    start_date = dates[0].date_input(
        "From", value=date(2026, 6, 1), min_value=date(2025, 7, 1), max_value=date(2026, 6, 30), key="strategy_b_start"
    )
    end_date = dates[1].date_input(
        "To", value=date(2026, 6, 30), min_value=date(2025, 7, 1), max_value=date(2026, 6, 30), key="strategy_b_end"
    )

    if st.button("Run Strategy B backtest", type="primary", use_container_width=True):
        if start_date > end_date:
            st.error("From date must be on or before To date.")
        else:
            try:
                with st.spinner("Replaying Strategy B against the public NIFTY + option snapshot..."):
                    report = cached_backtest(
                        start_date.isoformat(), end_date.isoformat(), selected["threshold"], selected["cooldown"]
                    )
            except Exception as exc:
                st.error(f"Strategy B backtest could not run: {exc}")
            else:
                st.session_state["strategy_b_report"] = report
                st.session_state["strategy_b_summary"] = report.as_summary()

    report = st.session_state.get("strategy_b_report")
    summary = st.session_state.get("strategy_b_summary")
    if report is not None and summary is not None:
        st.divider()
        top = st.columns(6)
        top[0].metric("Trades", summary["trades"])
        top[1].metric("Target win rate", f'{summary["target_win_rate_pct"]:.1f}%')
        top[2].metric("Net points", f'{summary["net_points_after_friction"]:+.1f}')
        top[3].metric("Expectancy", f'{summary["expectancy_points_per_trade"]:+.2f}')
        top[4].metric("Profit factor", f'{summary["profit_factor"]:.2f}')
        top[5].metric("Max DD", f'{summary["max_drawdown_points"]:.1f} pts')

        detail = st.columns(5)
        detail[0].metric("Wins", summary["target_wins"])
        detail[1].metric("Losses", summary["stop_losses"])
        detail[2].metric("Timeouts", summary["timeouts"])
        detail[3].metric("CE win", f'{summary["ce_target_win_rate_pct"]:.1f}%')
        detail[4].metric("PE win", f'{summary["pe_target_win_rate_pct"]:.1f}%')

        if report.trades:
            frame = pd.DataFrame(
                [
                    {
                        "Signal": trade.signal_time,
                        "Entry": trade.entry_time,
                        "Side": trade.direction,
                        "Strike": trade.strike,
                        "Score": trade.score,
                        "Entry premium": trade.entry_premium,
                        "Exit premium": trade.exit_premium,
                        "Result": trade.outcome,
                        "Net pts": trade.net_points,
                        "Hold min": trade.hold_minutes,
                    }
                    for trade in report.trades
                ]
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)

        st.warning(
            "This is historical evidence, not a promise of future performance. A high score means evidence alignment; it is not a 90% or 100% probability label."
        )
