from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from nandi_oi.configuration import is_configured_value


st.set_page_config(page_title="PROFIT FIRST · Midday Reversal", page_icon="P", layout="wide")

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


HISTORICAL = pd.DataFrame(
    [
        ["Jan 2026", 42, 19, 23, 45.24, 250.25],
        ["Feb 2026", 39, 25, 14, 64.10, 144.80],
        ["Mar 2026", 69, 26, 43, 37.68, -31.52],
        ["Apr 2026", 55, 28, 27, 50.91, -48.10],
        ["May 2026", 52, 31, 21, 59.62, 131.05],
        ["Jun 2026", 48, 29, 19, 60.42, 496.95],
    ],
    columns=["Month", "Trades", "Wins", "Losses", "Win rate %", "Net option points"],
)


st.title("PROFIT FIRST · Midday 1-Minute Reversal")
st.caption("Research-only setup. It is isolated from TEST 1 and production trading logic.")

st.info(
    "Core rule: 12:00–14:00 IST. If NIFTY moves at least +0.05% in one completed 1-minute candle, buy the nearest ATM PE on the next-minute open. "
    "If NIFTY moves at least -0.05%, buy the nearest ATM CE. Keep only one position open and exit about 30 option bars later."
)

rule_cols = st.columns(5)
rule_cols[0].metric("Signal window", "12:00–14:00")
rule_cols[1].metric("NIFTY trigger", "±0.05% / 1m")
rule_cols[2].metric("Direction", "Opposite impulse")
rule_cols[3].metric("Hold", "~30 min")
rule_cols[4].metric("Positions", "1 at a time")

st.subheader("Frozen Jan–Jun 2026 replay")
headline = st.columns(5)
headline[0].metric("Trades", "305")
headline[1].metric("Wins / losses", "158 / 147")
headline[2].metric("Win rate", "51.80%")
headline[3].metric("Net option points", "+943.43")
headline[4].metric("Profit factor", "1.437")

st.dataframe(
    HISTORICAL.style.format({"Win rate %": "{:.2f}", "Net option points": "{:+.2f}"}),
    use_container_width=True,
    hide_index=True,
)

st.caption(
    "Historical execution convention: next-minute ATM option open +0.20 points, then an additional 0.50 option-point friction per trade. "
    "The public workbook used for this proof ends on 30 Jun 2026."
)

st.divider()
st.subheader("Previous-week exact replay · 24–28 Aug 2026")
st.write(
    "This button replays the same frozen rule against read-only Upstox historical candles using the token already configured for Nandi. "
    "It does not place broker orders and does not tune the strategy to the week."
)

token = configured_token()
if not token:
    st.warning("Nandi's read-only Upstox token is not currently available in this Streamlit session, so the August P&L cannot be verified here yet.")
else:
    if st.button("Replay 24–28 Aug exactly", type="primary", use_container_width=True):
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "research_profit_first_aug24_28_upstox.py"
        if not script_path.exists():
            st.error("The exact replay script is missing from this research branch.")
        else:
            try:
                with st.spinner("Replaying NIFTY + ATM options candle by candle..."):
                    spec = importlib.util.spec_from_file_location("profit_first_aug_replay_runtime", script_path)
                    if spec is None or spec.loader is None:
                        raise RuntimeError("Could not load the replay module")
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    module.TOKEN = token
                    with tempfile.TemporaryDirectory() as td:
                        module.OUT_JSON = Path(td) / "result.json"
                        module.OUT_CSV = Path(td) / "trades.csv"
                        module.main()
                        result = json.loads(module.OUT_JSON.read_text(encoding="utf-8"))
                        trades = pd.read_csv(module.OUT_CSV) if module.OUT_CSV.stat().st_size else pd.DataFrame()

                if result.get("status") != "EXACT_FROZEN_RULE_REPLAY":
                    st.warning(result.get("message", "Exact replay did not complete."))
                else:
                    week = result["week"]
                    summary = st.columns(5)
                    summary[0].metric("Trades", week["trades"])
                    summary[1].metric("Wins", week["wins"])
                    summary[2].metric("Losses", week["losses"])
                    summary[3].metric("Win rate", f"{week['win_rate']:.2f}%" if week["win_rate"] is not None else "—")
                    summary[4].metric("Week net points", f"{week['net_points']:+.2f}")

                    daily = pd.DataFrame(result["daily"])
                    if not daily.empty:
                        st.dataframe(
                            daily.style.format({
                                "win_rate": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
                                "net_points": "{:+.2f}",
                                "expectancy": lambda x: "—" if pd.isna(x) else f"{x:+.2f}",
                                "profit_factor": lambda x: "—" if pd.isna(x) else f"{x:.3f}",
                            }),
                            use_container_width=True,
                            hide_index=True,
                        )
                    if not trades.empty:
                        with st.expander("Show exact trade ledger"):
                            st.dataframe(trades, use_container_width=True, hide_index=True)
                    st.success("Exact frozen-rule replay completed from read-only Upstox historical data.")
            except Exception as exc:
                st.error(f"August replay could not be completed from Upstox: {exc}")

st.caption("PROFIT FIRST remains a research candidate. No live or broker order is sent from this page.")
