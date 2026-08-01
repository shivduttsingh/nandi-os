from __future__ import annotations

import os
import json
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from nandi_oi import NandiOIEngine, UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.auth import CredentialConfigurationError, LoginLockout
from nandi_oi.evidence import decision_history_rows, expiry_comparison_rows, live_evidence
from nandi_oi.backtest import NandiBacktester
from nandi_oi.historical import UpstoxHistoricalClient
from nandi_oi.paper import PaperJournal
from nandi_oi.rsi_backtest import RsiLevelBacktester, RsiTouchAnalyzer, TIMEFRAMES


st.set_page_config(page_title="Nandi OI", page_icon="📈", layout="wide")
st.markdown("""
<style>
:root { --nandi-green:#137a46; --nandi-green-dark:#075c32; --nandi-mint:#eaf8ef; --nandi-line:#d8e9df; }
.stApp { background: linear-gradient(135deg, #ffffff 0%, #f6fcf8 58%, #eaf8ef 100%); color:#173226; }
section[data-testid="stSidebar"] { background:#ffffff; border-right:1px solid var(--nandi-line); }
section[data-testid="stSidebar"] h1 { color:var(--nandi-green-dark); }
div[data-testid="stMetric"] { background:#ffffff; border:1px solid var(--nandi-line); border-radius:14px; padding:14px; box-shadow:0 4px 16px rgba(19,122,70,.06); }
div[data-testid="stMetric"] label { color:#52705e; }
.stButton > button[kind="primary"] { background:var(--nandi-green); border-color:var(--nandi-green); }
.stButton > button[kind="primary"]:hover { background:var(--nandi-green-dark); border-color:var(--nandi-green-dark); }
[data-testid="stDataFrame"] { border:1px solid var(--nandi-line); border-radius:12px; overflow:hidden; }
</style>
""", unsafe_allow_html=True)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

APP_USERNAME: str | None = None
APP_PASSWORD: str | None = None
AUTH_CONFIGURATION_ERROR = ""
try:
    APP_USERNAME = str(st.secrets["auth"]["username"])
    APP_PASSWORD = str(st.secrets["auth"]["password"])
except Exception:
    AUTH_CONFIGURATION_ERROR = (
        "Authentication is not configured. Add auth.username and auth.password "
        "to Streamlit Secrets before using Nandi."
    )

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "oi_engine" not in st.session_state:
    st.session_state.oi_engine = NandiOIEngine()
if "upstox_client" not in st.session_state:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    try:
        token = token or st.secrets.get("upstox", {}).get("access_token", "")
    except Exception:
        pass
    st.session_state.upstox_client = UpstoxOptionChainClient(access_token=token)
if "latest_snapshot" not in st.session_state:
    st.session_state.latest_snapshot = None
if "latest_decision" not in st.session_state:
    st.session_state.latest_decision = None
if "decision_log" not in st.session_state:
    st.session_state.decision_log = []

journal = PaperJournal()


def capture() -> None:
    snapshot = st.session_state.upstox_client.fetch_snapshot()
    decision = st.session_state.oi_engine.add_snapshot(snapshot)
    st.session_state.latest_snapshot = snapshot
    st.session_state.latest_decision = decision
    st.session_state.decision_log.append({
        "time": snapshot.timestamp.isoformat(timespec="seconds"), "spot": snapshot.spot,
        "bullish": decision.bullish_score, "bearish": decision.bearish_score,
        "decision": decision.action, "confidence": decision.confidence,
        "reasons": " | ".join(decision.reasons or decision.blockers),
    })


def login_page() -> None:
    header("Nandi", "Private OI Paper Research System")
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        st.header("Unified NIFTY Option-Chain Intelligence")
        st.write("OI direction, premium behaviour, NIFTY confirmation, paper trades and daily evidence—one focused system.")
        st.success("Paper mode only • Maximum three quality trades daily")
    with right:
        with st.container(border=True):
            st.subheader("Sign in")
            with st.form("login_form"):
                username = st.text_input("Email or Username")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign In", use_container_width=True)
            if submitted:
                try:
                    result = LoginLockout(st.session_state).authenticate(
                        username, password, APP_USERNAME, APP_PASSWORD,
                    )
                except CredentialConfigurationError:
                    st.error(AUTH_CONFIGURATION_ERROR)
                else:
                    if result.authenticated:
                        st.session_state.logged_in = True
                        st.rerun()
                    elif result.locked:
                        minutes = max(1, (result.retry_after_seconds + 59) // 60)
                        st.error(f"Too many failed attempts. Try again in about {minutes} minute(s).")
                    else:
                        st.error(
                            "Invalid username or password. "
                            f"{result.attempts_remaining} attempt(s) remaining."
                        )
            if AUTH_CONFIGURATION_ERROR:
                st.warning(AUTH_CONFIGURATION_ERROR)


def header(title: str, subtitle: str) -> None:
    st.title(title)
    st.caption(subtitle)



def evidence_dashboard(snapshot, decision) -> None:
    """Render explainable charts from the already-computed OI V1 decision."""
    evidence = live_evidence(snapshot, decision)
    oi_tab, premium_tab, structure_tab, score_tab = st.tabs(
        ["OI activity", "Option premium", "NIFTY structure", "Decision score"]
    )
    with oi_tab:
        st.caption("Every OI activity label is derived from the existing change-in-OI and change-in-premium values.")
        oi_frame = pd.DataFrame(evidence["oi"])
        st.bar_chart(oi_frame.pivot(index="Strike", columns="Side", values="OI change"))
        st.dataframe(oi_frame, use_container_width=True, hide_index=True)
    with premium_tab:
        st.caption("ATM premiums validate whether the selected option side is gaining momentum with usable liquidity.")
        premium_frame = pd.DataFrame(evidence["premium"])
        if not premium_frame.empty:
            st.bar_chart(premium_frame.set_index("Contract")[["Premium", "Premium change"]])
            st.dataframe(premium_frame, use_container_width=True, hide_index=True)
    with structure_tab:
        structure_frame = pd.DataFrame(evidence["structure"])
        st.bar_chart(structure_frame.set_index("Structure")[["Spot", "Recent high", "Recent low"]])
        st.dataframe(structure_frame, use_container_width=True, hide_index=True)
    with score_tab:
        st.caption("Scores are displayed for explanation only; OI V1 approval thresholds and weights are unchanged.")
        score_frame = pd.DataFrame(evidence["score"])
        st.bar_chart(score_frame.set_index("Evidence"))
        st.dataframe(score_frame, use_container_width=True, hide_index=True)


def command_center() -> None:
    header("Nandi Command Center", "Unified NIFTY option-chain probability strategy")
    decision = st.session_state.latest_decision
    snapshot = st.session_state.latest_snapshot
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NIFTY", f"{snapshot.spot:,.2f}" if snapshot else "Waiting")
    c2.metric("Decision", decision.action if decision else "NO DATA")
    c3.metric("Bullish", f"{decision.bullish_score:.1f}" if decision else "—")
    c4.metric("Bearish", f"{decision.bearish_score:.1f}" if decision else "—")
    st.info("Nandi V1 approves only scores ≥80, a ≥20-point directional lead, price/premium confirmation and three persistent snapshots.")
    trades = journal.trades_today()
    st.metric("Paper trades today", f"{len(trades)} / 3")


def live_engine() -> None:
    header("Live OI Engine", "Upstox NIFTY current-week chain • ATM ±5 strikes")
    if st.button("Capture Upstox Snapshot", type="primary", use_container_width=True):
        try:
            capture()
            st.success("Snapshot captured and analysed")
        except UpstoxAPIError as exc:
            st.error(str(exc))

    auto = st.toggle("Auto-capture every 30 seconds", value=False)
    if auto:
        @st.fragment(run_every="30s")
        def auto_capture_panel() -> None:
            try:
                capture()
                st.caption(f"Last automatic capture: {datetime.now().strftime('%I:%M:%S %p')}")
            except UpstoxAPIError as exc:
                st.error(str(exc))
        auto_capture_panel()

    snapshot = st.session_state.latest_snapshot
    decision = st.session_state.latest_decision
    if not snapshot or not decision:
        st.warning("Add the Upstox Analytics Token in Settings/Streamlit Secrets, then capture three snapshots.")
        return

    a, b, c, d = st.columns(4)
    a.metric("Final action", decision.action)
    b.metric("Confidence", f"{decision.confidence:.1f}%")
    c.metric("Bullish", f"{decision.bullish_score:.1f}")
    d.metric("Bearish", f"{decision.bearish_score:.1f}")
    if decision.reasons:
        st.subheader("Confirmed evidence")
        for reason in decision.reasons:
            st.write(f"✅ {reason}")
    if decision.blockers:
        st.subheader("Why Nandi is waiting")
        for blocker in decision.blockers:
            st.write(f"⏳ {blocker}")

    rows = [{"Strike": x.strike, "Side": x.side, "OI": x.oi, "ΔOI": x.change_oi,
             "LTP": x.ltp, "ΔLTP": x.change_ltp, "Activity": x.activity,
             "Volume": x.volume, "Spread %": round(x.spread_pct, 2)} for x in snapshot.legs]
    frame = pd.DataFrame(rows)
    nearest = sorted(frame.Strike.unique(), key=lambda x: abs(x - snapshot.spot))[:11]
    st.dataframe(frame[frame.Strike.isin(nearest)].sort_values(["Strike", "Side"]), use_container_width=True, hide_index=True)
    st.subheader("Evidence behind this decision")
    evidence_dashboard(snapshot, decision)


def paper_trades() -> None:
    header("Paper Trades", "Maximum three research trades per day • no broker orders")
    decision = st.session_state.latest_decision
    snapshot = st.session_state.latest_snapshot
    if decision and decision.approved and snapshot:
        side = "CE" if decision.action == "BUY CE" else "PE"
        leg = next((x for x in snapshot.legs if x.side == side and x.strike == decision.selected_strike), None)
        if leg:
            st.success(f"Approved: {decision.action} {leg.strike:.0f} at observed premium ₹{leg.ltp:.2f}")
            stop = st.number_input("Paper stop price", value=round(leg.ltp * 0.80, 2), min_value=0.05)
            target = st.number_input("Paper target price", value=round(leg.ltp * 1.30, 2), min_value=0.05)
            if st.button("Open Paper Trade", type="primary"):
                try:
                    journal.open_trade(decision.action, leg.strike, snapshot.expiry, leg.ltp, stop, target, decision.confidence)
                    st.success("Paper trade opened")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    else:
        st.info("A paper trade can open only after Nandi produces an approved BUY CE or BUY PE decision.")

    trades = journal.all()
    if trades:
        frame = pd.DataFrame([vars(x) for x in trades])
        st.dataframe(frame, use_container_width=True, hide_index=True)
        open_trade = next((x for x in trades if x.status == "OPEN"), None)
        if open_trade:
            exit_price = st.number_input("Exit premium", min_value=0.05, value=float(open_trade.entry_price))
            reason = st.selectbox("Exit reason", ["Target", "Stop loss", "OI reversal", "Price confirmation lost", "Manual research exit"])
            if st.button("Close Paper Trade"):
                journal.close_trade(open_trade.trade_id, exit_price, reason)
                st.success("Paper trade closed")
                st.rerun()


def daily_report() -> None:
    header("Daily Report", "Paper results and decision-quality review")
    trades = journal.all()
    closed = [x for x in trades if x.status == "CLOSED"]
    wins = [x for x in closed if x.pnl_points > 0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Closed trades", len(closed))
    c2.metric("Win rate", f"{len(wins)/len(closed)*100:.1f}%" if closed else "—")
    c3.metric("Net premium points", f"{sum(x.pnl_points for x in closed):.2f}")
    c4.metric("No-trade decisions", sum(x["decision"] == "NO TRADE" for x in st.session_state.decision_log))
    if st.session_state.decision_log:
        history_frame = pd.DataFrame(decision_history_rows(st.session_state.decision_log))
        st.subheader("Decision history")
        st.caption("Scores and confidence are recorded after each captured snapshot; this chart does not alter any prior decision.")
        st.line_chart(history_frame.set_index("time")[["bullish", "bearish", "confidence"]])
        st.dataframe(history_frame, use_container_width=True, hide_index=True)


def backtest_lab() -> None:
    header("Backtest Lab", "Daily, weekly, monthly or custom dates • same Nandi OI decision engine")
    st.info("Research simulation only. Historical candles cannot reproduce 30-second bid/ask snapshots exactly, so Nandi replays five-minute premium, volume and OI candles without future-data access.")

    period = st.selectbox("Test period", ["Single day", "One week", "One month", "Custom dates"])
    latest = date.today() - timedelta(days=1)
    if period == "Single day":
        selected = st.date_input("Trading date", value=latest, max_value=latest)
        start = end = selected
    elif period == "One week":
        end = st.date_input("Week ending date", value=latest, max_value=latest)
        start = end - timedelta(days=6)
    elif period == "One month":
        end = st.date_input("Month ending date", value=latest, max_value=latest)
        start = end - timedelta(days=29)
    else:
        first, second = st.columns(2)
        start = first.date_input("Start date", value=latest - timedelta(days=29), max_value=latest)
        end = second.date_input("End date", value=latest, min_value=start, max_value=latest)

    left, right = st.columns(2)
    left.metric("Start date", start.strftime("%d %b %Y"))
    right.metric("End date", end.strftime("%d %b %Y"))
    st.caption("Choose any completed historical period available through Upstox Plus. Recent unexpired weekly contracts may not be available yet.")

    if st.button("Run Backtest", type="primary", use_container_width=True):
        progress_bar = st.progress(0.0, text="Checking Upstox Plus historical access…")

        def update_progress(done: int, total: int, label: str) -> None:
            progress_bar.progress(done / max(total, 1), text=f"Loading {done}/{total}: {label}")

        try:
            token = st.session_state.upstox_client.access_token
            client = UpstoxHistoricalClient(access_token=token, timeout_seconds=20)
            snapshots = client.build_snapshots(start, end, progress=update_progress)
            result = NandiBacktester(stop_pct=0.20, target_pct=0.30).run(snapshots)
            st.session_state.backtest_result = result
            progress_bar.progress(1.0, text="Backtest completed")
            st.success("Historical replay completed without placing any broker order.")
        except (UpstoxAPIError, ValueError) as exc:
            progress_bar.empty()
            st.error(str(exc))

    result = st.session_state.get("backtest_result")
    if not result:
        st.write("Run the test to generate performance evidence and a downloadable trade ledger.")
        st.warning("The expired-instruments endpoints require Upstox Plus. Your existing token remains read-only.")
        return

    st.subheader("Nandi V1 backtest results")
    st.caption("V1 enters on the next five-minute candle and uses candle high/low with a 20% stop and 30% target.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trades", len(result.trades))
    c2.metric("Win rate", f"{result.win_rate:.1f}%")
    c3.metric("Net premium points", f"{result.net_points:+.2f}")
    c4.metric("Maximum drawdown", f"{result.max_drawdown:.2f}")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Wins", result.wins)
    c6.metric("Losses", result.losses)
    c7.metric("Snapshots replayed", result.snapshots)
    c8.metric("NO TRADE decisions", result.no_trade_decisions)

    if result.equity_curve:
        st.subheader("Cumulative premium points")
        st.line_chart(pd.DataFrame({"Cumulative points": result.equity_curve}))
    rows = result.rows()
    if rows:
        frame = pd.DataFrame(rows)
        st.subheader("V1 loss and setup analysis")
        frame["Result"] = frame["pnl_points"].apply(lambda value: "Win" if value > 0 else "Loss")
        frame["Entry hour"] = pd.to_datetime(frame["opened_at"]).dt.hour
        by_action = frame.groupby("action", as_index=False).agg(
            Trades=("action", "size"), Wins=("Result", lambda values: (values == "Win").sum()),
            Net_points=("pnl_points", "sum"), Average_points=("pnl_points", "mean"),
        )
        by_action["Win_rate_%"] = (by_action["Wins"] / by_action["Trades"] * 100).round(1)
        st.dataframe(by_action, use_container_width=True, hide_index=True)

        by_hour = frame.groupby("Entry hour", as_index=False).agg(
            Trades=("action", "size"), Wins=("Result", lambda values: (values == "Win").sum()),
            Net_points=("pnl_points", "sum"),
        )
        by_hour["Win_rate_%"] = (by_hour["Wins"] / by_hour["Trades"] * 100).round(1)
        with st.expander("Performance by entry hour"):
            st.dataframe(by_hour, use_container_width=True, hide_index=True)

        st.subheader("V1 historical trade ledger")
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.download_button(
            "Download Backtest CSV", frame.to_csv(index=False).encode("utf-8"),
            file_name=f"nandi_backtest_{result.start_date}_{result.end_date}.csv", mime="text/csv",
            use_container_width=True,
        )
    else:
        st.warning("The strategy produced no approved entries in the selected period.")


def rsi_backtest_lab() -> None:
    header("RSI Strategy Lab", "Editable RSI period and levels • multi-timeframe touch analysis")
    st.info("The old 20% stop / 30% target is removed. RSI trades use a 5% premium stop; the target is the opposite editable RSI level. Nandi V1 OI remains unchanged.")

    if "rsi_saved_strategies" not in st.session_state:
        st.session_state.rsi_saved_strategies = {
            "RSI 14 — 24/72": {"length": 14, "lower": 24.0, "upper": 72.0}
        }

    st.subheader("Strategy settings")
    saved = st.session_state.rsi_saved_strategies
    selected_name = st.selectbox("Load saved RSI strategy", list(saved))
    selected = saved[selected_name]
    strategy_name = st.text_input("Strategy name", value=selected_name)
    first, second, third = st.columns(3)
    length = int(first.number_input("RSI period", min_value=2, max_value=100, value=int(selected["length"]), step=1))
    lower = float(second.number_input("Lower RSI level", min_value=0.0, max_value=99.0, value=float(selected["lower"]), step=1.0))
    upper = float(third.number_input("Upper RSI level", min_value=1.0, max_value=100.0, value=float(selected["upper"]), step=1.0))
    timeframes = st.multiselect(
        "Timeframes to analyse",
        list(TIMEFRAMES),
        default=list(TIMEFRAMES),
        format_func=lambda value: "1 hour" if value == 60 else f"{value} min",
    )

    save_col, download_col = st.columns(2)
    if save_col.button("Save this RSI strategy", use_container_width=True):
        if not strategy_name.strip():
            st.error("Enter a strategy name.")
        elif lower >= upper:
            st.error("Lower RSI must be below upper RSI.")
        else:
            saved[strategy_name.strip()] = {"length": length, "lower": lower, "upper": upper}
            st.session_state.rsi_saved_strategies = saved
            st.success(f"Saved: {strategy_name.strip()}")
    download_col.download_button(
        "Download all saved strategies",
        json.dumps(saved, indent=2).encode("utf-8"),
        file_name="nandi_rsi_strategies.json",
        mime="application/json",
        use_container_width=True,
    )
    uploaded = st.file_uploader("Import saved RSI strategies", type=["json"])
    if uploaded is not None and st.button("Import strategies"):
        try:
            imported = json.loads(uploaded.getvalue().decode("utf-8"))
            for name, setup in imported.items():
                RsiTouchAnalyzer(
                    length=int(setup["length"]), lower=float(setup["lower"]),
                    upper=float(setup["upper"]), timeframes=(5,),
                )
            st.session_state.rsi_saved_strategies.update(imported)
            st.success("Strategies imported. Refresh this page to select them.")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            st.error(f"Invalid strategy file: {exc}")

    period = st.selectbox("RSI test period", ["Single day", "One week", "One month", "Custom dates"])
    latest = date.today() - timedelta(days=1)
    if period == "Single day":
        selected = st.date_input("RSI trading date", value=latest, max_value=latest)
        start = end = selected
    elif period == "One week":
        end = st.date_input("RSI week ending date", value=latest, max_value=latest)
        start = end - timedelta(days=6)
    elif period == "One month":
        end = st.date_input("RSI month ending date", value=latest, max_value=latest)
        start = end - timedelta(days=29)
    else:
        first, second = st.columns(2)
        start = first.date_input("RSI start date", value=latest - timedelta(days=29), max_value=latest)
        end = second.date_input("RSI end date", value=latest, min_value=start, max_value=latest)

    left, right = st.columns(2)
    left.metric("Start date", start.strftime("%d %b %Y"))
    right.metric("End date", end.strftime("%d %b %Y"))

    if st.button("Count RSI touches", type="primary", use_container_width=True):
        progress_bar = st.progress(0.0, text="Loading one-minute NIFTY candles…")
        try:
            if not timeframes:
                raise ValueError("Select at least one timeframe")
            token = st.session_state.upstox_client.access_token
            client = UpstoxHistoricalClient(access_token=token, timeout_seconds=20)
            warmup_start = start - timedelta(days=45)
            closes = client.spot_candles(warmup_start, end, interval_minutes=1)
            result = RsiTouchAnalyzer(length, lower, upper, timeframes).run(closes, start, end)
            st.session_state.rsi_trading_days = sorted({
                timestamp.date() for timestamp in closes
                if start <= timestamp.date() <= end
            })
            progress_bar.progress(0.20, text="Loading nearest-weekly option contracts…")

            def weekly_progress(done: int, total: int, label: str) -> None:
                progress_bar.progress(
                    0.20 + 0.35 * done / max(total, 1),
                    text=f"Weekly {done}/{total}: {label}",
                )

            weekly_snapshots = client.build_snapshots(
                start, end, progress=weekly_progress, expiry_mode="weekly",
            )
            weekly_result = RsiLevelBacktester(length, lower, upper, stop_pct=0.05).run(weekly_snapshots)
            progress_bar.progress(0.55, text="Loading nearest-monthly option contracts…")

            def monthly_progress(done: int, total: int, label: str) -> None:
                progress_bar.progress(
                    0.55 + 0.35 * done / max(total, 1),
                    text=f"Monthly {done}/{total}: {label}",
                )

            monthly_snapshots = client.build_snapshots(
                start, end, progress=monthly_progress, expiry_mode="monthly",
            )
            monthly_result = RsiLevelBacktester(length, lower, upper, stop_pct=0.05).run(monthly_snapshots)
            st.session_state.rsi_touch_result = result
            st.session_state.rsi_weekly_result = weekly_result
            st.session_state.rsi_monthly_result = monthly_result
            st.session_state.rsi_weekly_snapshots = weekly_snapshots
            st.session_state.rsi_monthly_snapshots = monthly_snapshots
            progress_bar.progress(1.0, text="RSI analysis completed")
            st.success("Touches counted; nearest-weekly and nearest-monthly option trades replayed.")
        except (UpstoxAPIError, ValueError) as exc:
            progress_bar.empty()
            st.error(str(exc))

    result = st.session_state.get("rsi_touch_result")
    if not result:
        st.write("Choose your settings and run the analysis to count historical RSI touches.")
        return

    summary = pd.DataFrame(result.summary_rows())
    st.subheader(f"RSI({result.length}) touch summary — {result.lower:g}/{result.upper:g}")
    st.caption("A touch is counted only when RSI newly crosses into a zone. Zone-candle totals show how long RSI remained there.")
    st.dataframe(summary, use_container_width=True, hide_index=True)
    st.caption("Touch counts are shown by timeframe to make the RSI evidence comparable before a replay is evaluated.")
    st.bar_chart(summary.set_index("Timeframe")[["Lower touches", "Upper touches", "Total touches"]])
    c1, c2, c3 = st.columns(3)
    c1.metric("Lower touches", int(summary["Lower touches"].sum()))
    c2.metric("Upper touches", int(summary["Upper touches"].sum()))
    c3.metric("All independent touches", int(summary["Total touches"].sum()))

    weekly_result = st.session_state.get("rsi_weekly_result")
    monthly_result = st.session_state.get("rsi_monthly_result")
    if weekly_result and monthly_result:
        st.subheader("Weekly versus monthly option replay")
        st.caption(
            f"BUY CE at RSI ≤{result.lower:g}; exit at RSI ≥{result.upper:g}. "
            f"BUY PE at RSI ≥{result.upper:g}; exit at RSI ≤{result.lower:g}. "
            "RSI comes from NIFTY. Every option trade has a 5% premium stop. "
            "Entry occurs on the next five-minute candle."
        )
        comparison = pd.DataFrame([
            {
                "Contract": "Nearest weekly", "Trades": len(weekly_result.trades),
                "Wins": weekly_result.wins, "Win rate %": round(weekly_result.win_rate, 1),
                "Net premium points": weekly_result.net_points,
                "Maximum drawdown": weekly_result.max_drawdown,
            },
            {
                "Contract": "Nearest monthly", "Trades": len(monthly_result.trades),
                "Wins": monthly_result.wins, "Win rate %": round(monthly_result.win_rate, 1),
                "Net premium points": monthly_result.net_points,
                "Maximum drawdown": monthly_result.max_drawdown,
            },
        ])
        comparison = pd.DataFrame(expiry_comparison_rows(weekly_result, monthly_result))
        st.dataframe(comparison, use_container_width=True, hide_index=True)
        st.caption("Weekly and monthly results use the same RSI signal settings. Differences reflect contract selection and replay data, not changed strategy rules.")
        st.bar_chart(comparison.set_index("Contract")[["Win rate %", "Net premium points", "Maximum drawdown"]])

        weekly_snapshots = st.session_state.get("rsi_weekly_snapshots", [])
        monthly_snapshots = st.session_state.get("rsi_monthly_snapshots", [])
        weekly_expiry = {item.timestamp.date(): item.expiry for item in weekly_snapshots}
        monthly_expiry = {item.timestamp.date(): item.expiry for item in monthly_snapshots}
        weekly_trades: dict[date, list] = {}
        monthly_trades: dict[date, list] = {}
        for trade in weekly_result.trades:
            weekly_trades.setdefault(trade.opened_at.date(), []).append(trade)
        for trade in monthly_result.trades:
            monthly_trades.setdefault(trade.opened_at.date(), []).append(trade)

        trading_days = st.session_state.get("rsi_trading_days", [])
        daily_rows = []
        for trading_day in trading_days:
            week = weekly_trades.get(trading_day, [])
            month = monthly_trades.get(trading_day, [])
            daily_rows.append({
                "Trading date": trading_day.isoformat(),
                "Weekly expiry used": weekly_expiry.get(trading_day, "Unavailable"),
                "Weekly setup": ", ".join(trade.action for trade in week) or "NO SETUP",
                "Weekly trades": len(week),
                "Weekly wins": sum(trade.pnl_points > 0 for trade in week),
                "Weekly outcome": (
                    "NO TRADE" if not week else
                    "WIN" if sum(trade.pnl_points for trade in week) > 0 else
                    "LOSS" if sum(trade.pnl_points for trade in week) < 0 else "FLAT"
                ),
                "Weekly exits": ", ".join(trade.exit_reason for trade in week) or "—",
                "Weekly net points": round(sum(trade.pnl_points for trade in week), 2),
                "Monthly expiry used": monthly_expiry.get(trading_day, "Unavailable"),
                "Monthly setup": ", ".join(trade.action for trade in month) or "NO SETUP",
                "Monthly trades": len(month),
                "Monthly wins": sum(trade.pnl_points > 0 for trade in month),
                "Monthly outcome": (
                    "NO TRADE" if not month else
                    "WIN" if sum(trade.pnl_points for trade in month) > 0 else
                    "LOSS" if sum(trade.pnl_points for trade in month) < 0 else "FLAT"
                ),
                "Monthly exits": ", ".join(trade.exit_reason for trade in month) or "—",
                "Monthly net points": round(sum(trade.pnl_points for trade in month), 2),
            })
        daily_frame = pd.DataFrame(daily_rows)
        st.subheader("Daily contract and setup check")
        st.caption("Every trading day is shown, including NO SETUP days. No trade is forced.")
        st.dataframe(daily_frame, use_container_width=True, hide_index=True)
        st.download_button(
            "Download Daily Weekly/Monthly Report",
            daily_frame.to_csv(index=False).encode("utf-8"),
            file_name=f"nandi_rsi_daily_expiry_report_{result.start_date}_{result.end_date}.csv",
            mime="text/csv", use_container_width=True,
        )

        with st.expander("Weekly trade ledger"):
            weekly_rows = weekly_result.rows()
            if weekly_rows:
                st.dataframe(pd.DataFrame(weekly_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No weekly-option trades occurred.")
        with st.expander("Monthly trade ledger"):
            monthly_rows = monthly_result.rows()
            if monthly_rows:
                st.dataframe(pd.DataFrame(monthly_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No monthly-option trades occurred.")

    rows = result.touch_rows()
    if rows:
        frame = pd.DataFrame(rows)
        st.subheader("Every RSI touch")
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.download_button(
            "Download RSI Touch CSV", frame.to_csv(index=False).encode("utf-8"),
            file_name=f"nandi_rsi_touches_{result.start_date}_{result.end_date}.csv",
            mime="text/csv", use_container_width=True,
        )
    else:
        st.warning("No RSI touches occurred with these settings in the selected period.")


def settings() -> None:
    header("Settings", "Secure Upstox configuration")
    st.code('[upstox]\naccess_token = "PASTE_YOUR_ANALYTICS_TOKEN_HERE"', language="toml")
    st.write("Add this to your Streamlit app Secrets. Never paste the token into source code or chat.")
    st.write("Underlying: `NSE_INDEX|Nifty 50`")
    st.write("Live OI V1 expiry: `current_week` (automatic rollover)")
    st.write("RSI historical comparison: nearest weekly and nearest monthly expiry")
    st.write("V1 snapshot requirement: 3")
    st.write("V1 approval score: 80/100 (quality score, not guaranteed probability)")
    st.write("V1 minimum directional lead: 20 points")
    st.write("Nandi OI V1 paper risk: 20% stop / 30% target")
    st.write("RSI Strategy Lab: 5% premium stop / opposite RSI level target")


pages = {
    "Command Center": command_center,
    "Live OI Engine": live_engine,
    "Paper Trades": paper_trades,
    "Daily Report": daily_report,
    "Backtest Lab": backtest_lab,
    "RSI Strategy Lab": rsi_backtest_lab,
    "Settings": settings,
}
if not st.session_state.logged_in:
    login_page()
else:
    with st.sidebar:
        st.title("Nandi")
        st.caption("OI Paper Research System")
        page = st.radio("Navigation", list(pages), label_visibility="collapsed")
        st.divider()
        st.success("Paper mode only")
        st.write(datetime.now().strftime("%d %b %Y · %I:%M %p"))
        if st.button("Logout", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()
    pages[page]()
