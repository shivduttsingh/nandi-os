from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from nandi_oi.configuration import is_configured_value


st.set_page_config(page_title="PROFIT FIRST", page_icon="P", layout="wide")

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


st.title("PROFIT FIRST")
st.caption("Midday 1-minute reversal · research only")

st.info(
    "12:00–14:00 IST · NIFTY 1-minute move ≥ +0.05% → buy nearest ATM PE next minute · "
    "NIFTY 1-minute move ≤ -0.05% → buy nearest ATM CE next minute · one position at a time · hold ~30 minutes."
)

headline = st.columns(5)
headline[0].metric("Trades", "305")
headline[1].metric("Wins / losses", "158 / 147")
headline[2].metric("Win rate", "51.80%")
headline[3].metric("Net option points", "+943.43")
headline[4].metric("Profit factor", "1.437")

st.subheader("Jan–Jun 2026 replay")
st.dataframe(
    HISTORICAL.style.format({"Win rate %": "{:.2f}", "Net option points": "{:+.2f}"}),
    use_container_width=True,
    hide_index=True,
)

st.caption("Execution assumption: next-minute ATM option open +0.20 slippage and +0.50 additional friction per trade.")

st.divider()
st.subheader("Previous week · 24–28 Aug 2026")
st.write("Replay the same frozen setup using Nandi's read-only Upstox historical data. No retuning and no broker orders.")

token = configured_token()
if not token:
    st.warning("Nandi's read-only Upstox token is not available in this Streamlit session.")
else:
    if st.button("Replay 24–28 Aug exactly", type="primary", use_container_width=True):
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "research_profit_first_aug24_28_upstox.py"
        try:
            with st.spinner("Replaying NIFTY and ATM option candles..."):
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
                    st.dataframe(daily, use_container_width=True, hide_index=True)
                if not trades.empty:
                    with st.expander("Exact trade ledger"):
                        st.dataframe(trades, use_container_width=True, hide_index=True)
                st.success("Exact frozen-rule replay completed.")
        except Exception as exc:
            st.error(f"August replay could not be completed from Upstox: {exc}")

st.caption("PROFIT FIRST is research-only. No live or broker order is sent from this page.")
