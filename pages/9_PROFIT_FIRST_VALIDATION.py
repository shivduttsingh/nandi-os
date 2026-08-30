from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from nandi_v2.profit_first import PUBLIC_DATA_URL, run_public_backtest
from nandi_v2.profit_first_reporting import (
    all_period_summaries,
    read_forward_ledger,
    read_forward_runs,
    rupee_pnl,
    validation_status,
)

st.set_page_config(page_title="PROFIT FIRST · Validation", page_icon="V", layout="wide")

if not st.session_state.get("logged_in", False):
    st.error("Please sign in from the Nandi home page first.")
    st.stop()


@st.cache_data(show_spinner=False, ttl=86_400)
def cached_public_backtest(start_iso: str, end_iso: str):
    return run_public_backtest(date.fromisoformat(start_iso), date.fromisoformat(end_iso))


def with_rupees(frame: pd.DataFrame, quantity: int) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    if "net_points" in result.columns:
        result[f"rupee_pnl_at_{quantity}"] = result["net_points"].map(
            lambda value: rupee_pnl(value, quantity)
        )
    return result


def render_summary(summary: dict, quantity: int, prefix: str = "") -> None:
    cols = st.columns(7)
    cols[0].metric(f"{prefix}Trades", int(summary.get("trades", 0) or 0))
    cols[1].metric(f"{prefix}Wins", int(summary.get("wins", 0) or 0))
    cols[2].metric(f"{prefix}Losses", int(summary.get("losses", 0) or 0))
    win_rate = summary.get("win_rate")
    cols[3].metric(f"{prefix}Win rate", "—" if win_rate is None else f"{float(win_rate):.2f}%")
    points = float(summary.get("net_points", 0.0) or 0.0)
    cols[4].metric(f"{prefix}Net option points", f"{points:+.2f}")
    cols[5].metric(f"{prefix}Paper P&L", f"₹{rupee_pnl(points, quantity):+,.0f}")
    pf = summary.get("profit_factor")
    cols[6].metric(f"{prefix}Profit factor", "—" if pf is None else f"{float(pf):.3f}")


def render_periods(trades: pd.DataFrame, quantity: int) -> None:
    _, daily, weekly, monthly = all_period_summaries(trades)
    day_tab, week_tab, month_tab = st.tabs(["Daily", "Weekly", "Monthly"])
    with day_tab:
        if daily.empty:
            st.info("No completed trades in this period yet.")
        else:
            st.dataframe(with_rupees(daily, quantity), use_container_width=True, hide_index=True)
    with week_tab:
        if weekly.empty:
            st.info("No completed trades in this period yet.")
        else:
            st.dataframe(with_rupees(weekly, quantity), use_container_width=True, hide_index=True)
    with month_tab:
        if monthly.empty:
            st.info("No completed trades in this period yet.")
        else:
            st.dataframe(with_rupees(monthly, quantity), use_container_width=True, hide_index=True)


st.title("PROFIT FIRST · Validation Center")
st.caption(
    "Frozen strategy rule. Historical backtest and forward-paper evidence are reported separately so live results cannot be confused with backtest results."
)

left, right = st.columns([2, 1])
with left:
    st.info(
        "The PROFIT FIRST rule is unchanged: 12:00–14:00 IST, completed NIFTY 1-minute impulse ±0.05%, "
        "mean-reversion ATM CE/PE entry on the next minute, one position at a time, 30 option-bar hold, "
        "with the same slippage/friction model."
    )
with right:
    quantity = int(
        st.number_input(
            "Paper quantity",
            min_value=1,
            max_value=10000,
            value=130,
            step=65,
            help="Used only to convert option points to simulated rupee P&L. No broker order is sent.",
        )
    )

st.page_link("pages/8_PROFIT_FIRST.py", label="Open PROFIT FIRST live paper + Upstox backtest", icon="↗️")

forward_tab, historical_tab, rules_tab = st.tabs(
    ["Forward paper record", "Historical day/week/month", "Validation rules"]
)

with forward_tab:
    st.subheader("Persistent forward-paper record")
    st.caption(
        "This section reads the durable evidence files produced after each trading day. It does not reuse the historical proof table."
    )
    ledger = read_forward_ledger()
    runs = read_forward_runs()
    overall, _, _, _ = all_period_summaries(ledger)

    if runs.empty:
        st.warning(
            "No end-of-day forward collector run has been recorded yet. After this feature is merged, the scheduled GitHub worker records each weekday after the market closes."
        )
    else:
        latest = runs.sort_values("test_date").iloc[-1]
        st.caption(
            f"Last collector date: {latest['test_date']} · status: {latest['status']} · recorded: {latest['recorded_at']}"
        )

    render_summary(overall, quantity)
    status, reasons = validation_status(overall)
    if status == "PASS":
        st.success("Forward validation status: PASS")
    elif status == "FAIL":
        st.error("Forward validation status: FAIL · " + "; ".join(reasons))
    else:
        st.info("Forward validation status: COLLECTING · " + "; ".join(reasons))

    render_periods(ledger, quantity)

    if not runs.empty:
        with st.expander("Collector run history, including zero-trade days"):
            runs_view = runs.copy()
            if "net_points" in runs_view.columns:
                runs_view[f"rupee_pnl_at_{quantity}"] = pd.to_numeric(
                    runs_view["net_points"], errors="coerce"
                ).fillna(0.0).map(lambda value: rupee_pnl(value, quantity))
            st.dataframe(runs_view, use_container_width=True, hide_index=True)

    if not ledger.empty:
        with st.expander("Exact forward trade ledger"):
            trade_view = ledger.copy()
            trade_view["pnl"] = pd.to_numeric(trade_view["pnl"], errors="coerce")
            trade_view[f"rupee_pnl_at_{quantity}"] = trade_view["pnl"].fillna(0.0).map(
                lambda value: rupee_pnl(value, quantity)
            )
            st.dataframe(trade_view, use_container_width=True, hide_index=True)

with historical_tab:
    st.subheader("Historical backtest with daily, weekly and monthly breakdown")
    st.caption(
        "Public one-minute NIFTY + ATM-option sample. Coverage: 3 Jul 2025–30 Jun 2026. "
        "This remains historical evidence, not forward evidence."
    )
    dates = st.columns(2)
    start_date = dates[0].date_input(
        "From",
        value=date(2026, 1, 1),
        min_value=date(2025, 7, 3),
        max_value=date(2026, 6, 30),
        key="pf_validation_public_from",
    )
    end_date = dates[1].date_input(
        "To",
        value=date(2026, 6, 30),
        min_value=date(2025, 7, 3),
        max_value=date(2026, 6, 30),
        key="pf_validation_public_to",
    )
    st.caption(f"Dataset: {PUBLIC_DATA_URL}")

    if st.button("Run frozen historical validation", type="primary", use_container_width=True):
        if start_date > end_date:
            st.error("From date must be on or before To date.")
        else:
            try:
                with st.spinner("Replaying the frozen rule candle by candle..."):
                    summary, trades, _, _ = cached_public_backtest(
                        start_date.isoformat(), end_date.isoformat()
                    )
            except Exception as exc:
                st.error(f"Historical backtest failed: {exc}")
            else:
                st.session_state["pf_validation_summary"] = summary
                st.session_state["pf_validation_trades"] = trades

    summary = st.session_state.get("pf_validation_summary")
    trades = st.session_state.get("pf_validation_trades")
    if summary is not None and isinstance(trades, pd.DataFrame):
        st.divider()
        render_summary(summary, quantity)
        render_periods(trades, quantity)
        with st.expander("Exact historical trade ledger"):
            view = trades.copy()
            view[f"rupee_pnl_at_{quantity}"] = pd.to_numeric(
                view["pnl"], errors="coerce"
            ).fillna(0.0).map(lambda value: rupee_pnl(value, quantity))
            st.dataframe(view, use_container_width=True, hide_index=True)

with rules_tab:
    st.subheader("How Nandi will judge this strategy")
    st.markdown(
        """
**Backtest and forward results stay separate.** A profitable historical result does not make the strategy live-validated.

**Forward evidence gate:**
- At least **100 completed forward-paper trades** before Nandi can call the sample mature.
- Target **70%+ forward win rate**.
- Target **profit factor ≥ 1.50**.
- Net forward option points must remain **positive**.

Until 100 forward trades, the status is **COLLECTING**. After that it is mechanically **PASS** or **FAIL** from the frozen metrics. No rule tuning is allowed inside the same forward sample.
        """
    )
    st.warning(
        "Simulated rupee P&L is option points × selected paper quantity. Brokerage, taxes and live execution differences are not separately estimated beyond the strategy's built-in slippage/friction assumptions."
    )

st.caption("Research and paper testing only. No broker orders are placed.")
