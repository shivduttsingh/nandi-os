from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from nandi_oi.configuration import is_configured_value
from nandi_v2.profit_first import UpstoxProfitFirstHistory
from nandi_v2.profit_first_reporting import (
    all_period_summaries,
    read_forward_ledger,
    read_forward_runs,
    rupee_pnl,
    validation_status,
)

IST = ZoneInfo("Asia/Kolkata")

st.set_page_config(page_title="PROFIT FIRST · Validation", page_icon="V", layout="wide")

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
    "Frozen strategy rule. Historical backtest and forward-paper evidence are reported separately. All market data in this validation flow comes from Upstox."
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

st.success("Nandi PROFIT FIRST data source: UPSTOX ONLY")
st.page_link("pages/8_PROFIT_FIRST.py", label="Open PROFIT FIRST live paper + Upstox backtest", icon="↗️")

forward_tab, historical_tab, rules_tab = st.tabs(
    ["Forward paper record", "Historical day/week/month", "Validation rules"]
)

with forward_tab:
    st.subheader("Persistent forward-paper record")
    st.caption(
        "This section reads durable forward evidence produced from Upstox after each trading day. Historical backtests are kept separate."
    )
    ledger = read_forward_ledger()
    runs = read_forward_runs()
    overall, _, _, _ = all_period_summaries(ledger)

    if runs.empty:
        st.warning(
            "No end-of-day forward collector run has been recorded yet. The scheduled worker records each weekday after the market closes."
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
    st.subheader("Upstox historical backtest with daily, weekly and monthly breakdown")
    st.caption(
        "Historical NIFTY 1-minute and nearest-expiry ATM CE/PE option candles are read from Upstox only. "
        "Expired-option history requires Upstox Plus. Each run is limited to 32 calendar days, so test longer history month by month."
    )
    token = configured_token()
    if not token:
        st.warning(
            "The read-only Upstox token is not available in this Streamlit session. Add or refresh the Upstox token to run historical backtests."
        )
    else:
        last_complete_date = datetime.now(IST).date() - timedelta(days=1)
        minimum = date(2020, 1, 1)
        default_start = max(minimum, last_complete_date.replace(day=1))
        dates = st.columns(2)
        start_date = dates[0].date_input(
            "From",
            value=default_start,
            min_value=minimum,
            max_value=last_complete_date,
            key="pf_validation_upstox_from",
        )
        end_date = dates[1].date_input(
            "To",
            value=last_complete_date,
            min_value=minimum,
            max_value=last_complete_date,
            key="pf_validation_upstox_to",
        )
        st.caption(
            f"Last completed calendar date available for selection: {last_complete_date}. "
            "Weekends and exchange holidays inside a range naturally return no candles for those dates."
        )

        if st.button("Run Upstox historical validation", type="primary", use_container_width=True):
            if start_date > end_date:
                st.error("From date must be on or before To date.")
            elif (end_date - start_date).days > 31:
                st.error("Choose a range of 32 calendar days or less. Test longer history month by month.")
            elif start_date == end_date and start_date.weekday() >= 5:
                st.warning(
                    f"{start_date} is a weekend, so NSE has no NIFTY/option candles to backtest. Choose a trading day."
                )
            else:
                try:
                    with st.spinner("Reading NIFTY + nearest-expiry ATM option history from Upstox..."):
                        history = UpstoxProfitFirstHistory(token)
                        summary, trades, _, _ = history.run_backtest(start_date, end_date)
                except Exception as exc:
                    st.error(f"Upstox historical backtest failed: {exc}")
                else:
                    st.session_state["pf_validation_summary"] = summary
                    st.session_state["pf_validation_trades"] = trades
                    st.session_state["pf_validation_range"] = f"{start_date} to {end_date}"

        summary = st.session_state.get("pf_validation_summary")
        trades = st.session_state.get("pf_validation_trades")
        result_range = st.session_state.get("pf_validation_range")
        if summary is not None and isinstance(trades, pd.DataFrame):
            st.divider()
            st.caption(f"Result source: Upstox · range: {result_range}")
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
**Single data source:** Upstox is the only market-data source used for PROFIT FIRST live, historical and forward-paper testing inside Nandi.

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