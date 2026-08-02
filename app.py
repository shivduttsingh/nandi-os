from __future__ import annotations

import os
import json
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from nandi_oi import NandiOIEngine, UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.auth import CredentialConfigurationError, LoginLockout
from nandi_oi.configuration import is_configured_value
from nandi_oi.evidence import decision_history_rows, expiry_comparison_rows, live_evidence
from nandi_oi.backtest import NandiBacktester
from nandi_oi.historical import UpstoxHistoricalClient
from nandi_oi.market_schedule import MarketSchedule
from nandi_oi.paper import PaperJournal
from nandi_oi.rsi_backtest import RsiLevelBacktester, RsiTouchAnalyzer, TIMEFRAMES
from nandi_oi.store import NandiStore
from nandi_oi.unified_backtest import UnifiedBacktester


st.set_page_config(page_title="Nandi", page_icon="N", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
:root {
  --nandi-green:#126b3a;
  --nandi-green-dark:#0b4e2a;
  --nandi-green-soft:#edf7f1;
  --nandi-ink:#16271e;
  --nandi-muted:#64746c;
  --nandi-line:#dbe8e0;
  --nandi-warm:#f8fbf9;
}
.stApp { background:#ffffff; color:var(--nandi-ink); }
.block-container { max-width:1480px; padding-top:2rem; padding-bottom:4rem; }
section[data-testid="stSidebar"] { background:var(--nandi-warm); border-right:1px solid var(--nandi-line); }
section[data-testid="stSidebar"] .block-container { padding-top:1.45rem; }
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color:var(--nandi-muted); }
h1,h2,h3 { color:var(--nandi-ink); letter-spacing:-.025em; }
h2 { font-size:1.35rem!important; margin-top:1.7rem!important; }
p,div,label { line-height:1.5; }
div[data-testid="stMetric"] {
  background:#ffffff; border:1px solid var(--nandi-line); border-radius:14px;
  padding:16px 18px; box-shadow:0 6px 18px rgba(18,107,58,.045);
}
div[data-testid="stMetricLabel"] { color:var(--nandi-muted); font-size:.78rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }
div[data-testid="stMetricValue"] { color:var(--nandi-ink); font-weight:760; }
.stButton > button, .stDownloadButton > button { border-radius:10px; border-color:var(--nandi-line); min-height:2.7rem; font-weight:700; }
.stButton > button[kind="primary"] { background:var(--nandi-green); border-color:var(--nandi-green); color:#fff; }
.stButton > button[kind="primary"]:hover { background:var(--nandi-green-dark); border-color:var(--nandi-green-dark); }
[data-testid="stDataFrame"] { border:1px solid var(--nandi-line); border-radius:12px; overflow:hidden; }
div[data-testid="stExpander"] { border:1px solid var(--nandi-line); border-radius:12px; background:#fff; }
div[data-baseweb="tab-list"] { gap:.25rem; border-bottom:1px solid var(--nandi-line); }
button[data-baseweb="tab"] { border-radius:8px 8px 0 0; padding:.65rem 1rem; }
button[data-baseweb="tab"][aria-selected="true"] { background:var(--nandi-green-soft); color:var(--nandi-green-dark); }
.nandi-hero {
  display:flex; align-items:flex-start; justify-content:space-between; gap:1.5rem;
  padding:1.55rem 1.7rem; margin:0 0 1.55rem; border:1px solid var(--nandi-line);
  border-radius:18px; background:linear-gradient(112deg,#ffffff 50%,#eef8f2 100%);
  box-shadow:0 10px 28px rgba(18,107,58,.055); position:relative; overflow:hidden;
}
.nandi-hero:before { content:""; position:absolute; left:0; top:0; bottom:0; width:5px; background:var(--nandi-green); }
.nandi-eyebrow { font-size:.72rem; font-weight:800; letter-spacing:.14em; text-transform:uppercase; color:var(--nandi-green); }
.nandi-title { font-size:2rem; font-weight:780; letter-spacing:-.04em; color:var(--nandi-ink); margin:.24rem 0 .26rem; }
.nandi-subtitle { max-width:740px; color:var(--nandi-muted); font-size:.96rem; }
.nandi-status { display:inline-flex; align-items:center; white-space:nowrap; border:1px solid #b9ddc8; background:#ffffff; color:var(--nandi-green-dark); border-radius:999px; padding:.46rem .78rem; font-size:.72rem; font-weight:800; letter-spacing:.06em; }
.nandi-brand { display:flex; align-items:center; gap:.75rem; margin:.2rem 0 1.35rem; }
.nandi-mark { display:grid; place-items:center; width:40px; height:40px; border-radius:12px; background:var(--nandi-green); color:#fff; font-weight:850; font-size:1.1rem; }
.nandi-brand-name { font-size:1.35rem; font-weight:780; color:var(--nandi-ink); line-height:1.05; letter-spacing:-.03em; }
.nandi-brand-copy { color:var(--nandi-muted); font-size:.68rem; letter-spacing:.1em; font-weight:750; }
.nandi-login-heading { font-size:1.6rem; line-height:1.16; letter-spacing:-.035em; font-weight:780; color:var(--nandi-ink); margin:0 0 .6rem; }
.nandi-login-copy { color:var(--nandi-muted); max-width:590px; margin-bottom:1.25rem; }
.nandi-principles { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.7rem; margin-top:1rem; }
.nandi-principle { border:1px solid var(--nandi-line); border-radius:12px; background:#fff; padding:.85rem .9rem; }
.nandi-principle-label { color:var(--nandi-green); font-size:.67rem; font-weight:800; letter-spacing:.1em; }
.nandi-principle-copy { color:var(--nandi-muted); font-size:.8rem; margin-top:.24rem; }
.nandi-section-label { color:var(--nandi-green); font-size:.7rem; letter-spacing:.12em; font-weight:800; margin-bottom:.2rem; }
.nandi-research-card { border:1px solid var(--nandi-line); border-radius:14px; padding:1rem 1.1rem; background:#fff; margin:1.2rem 0 1rem; }
.nandi-research-title { font-size:1rem; font-weight:760; color:var(--nandi-ink); margin:.1rem 0; }
.nandi-research-copy { font-size:.88rem; color:var(--nandi-muted); }
.nandi-chip-row { display:flex; flex-wrap:wrap; gap:.45rem; margin-top:.8rem; }
.nandi-chip { border:1px solid #c8e2d2; color:var(--nandi-green-dark); background:var(--nandi-green-soft); border-radius:999px; padding:.24rem .55rem; font-size:.7rem; font-weight:750; }
.nandi-session-card { display:flex; align-items:center; justify-content:space-between; gap:1.2rem; border:1px solid var(--nandi-line); border-radius:14px; padding:1rem 1.1rem; background:#fff; margin:0 0 1rem; }
.nandi-session-title { font-size:1rem; font-weight:760; color:var(--nandi-ink); margin:.08rem 0; }
.nandi-session-copy { color:var(--nandi-muted); font-size:.86rem; }
.nandi-session-time { color:var(--nandi-green-dark); text-align:right; font-size:.78rem; font-weight:760; white-space:nowrap; }
.nandi-setup-step { border:1px solid var(--nandi-line); border-radius:13px; padding:1rem 1.05rem; background:#fff; min-height:125px; }
.nandi-setup-number { color:var(--nandi-green); font-weight:850; font-size:.72rem; letter-spacing:.1em; }
.nandi-setup-title { color:var(--nandi-ink); font-weight:760; margin:.22rem 0; }
.nandi-setup-copy { color:var(--nandi-muted); font-size:.85rem; }
@media (max-width: 760px) { .nandi-session-card { align-items:flex-start; flex-direction:column; } .nandi-session-time { text-align:left; } }
@media (max-width: 760px) {
  .nandi-hero { padding:1.25rem; }
  .nandi-status { display:none; }
  .nandi-title { font-size:1.65rem; }
  .nandi-principles { grid-template-columns:1fr; }
}
</style>
""", unsafe_allow_html=True)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


def configured_market_schedule() -> MarketSchedule:
    """Load optional NSE holiday dates without trusting the cloud server timezone."""
    holiday_values = os.getenv("NANDI_NSE_HOLIDAYS", "").split(",")
    try:
        secret_values = st.secrets.get("nse", {}).get("holidays", [])
        if isinstance(secret_values, str):
            holiday_values.extend(secret_values.split(","))
        else:
            holiday_values.extend(secret_values)
    except Exception:
        pass
    return MarketSchedule.from_iso_dates(holiday_values)


MARKET_SCHEDULE = configured_market_schedule()
STORE = NandiStore()

APP_USERNAME: str | None = None
APP_PASSWORD: str | None = None
AUTH_CONFIGURATION_ERROR = ""
try:
    secret_username = str(st.secrets["auth"]["username"])
    secret_password = str(st.secrets["auth"]["password"])
    if is_configured_value(secret_username) and is_configured_value(secret_password):
        APP_USERNAME = secret_username
        APP_PASSWORD = secret_password
except Exception:
    pass
if not APP_USERNAME or not APP_PASSWORD:
    local_username = os.getenv("NANDI_AUTH_USERNAME", "")
    local_password = os.getenv("NANDI_AUTH_PASSWORD", "")
    if is_configured_value(local_username) and is_configured_value(local_password):
        APP_USERNAME = local_username
        APP_PASSWORD = local_password
    else:
        AUTH_CONFIGURATION_ERROR = (
            "Authentication is not configured. Add real auth.username and auth.password values to Streamlit Secrets "
            "or NANDI_AUTH_USERNAME and NANDI_AUTH_PASSWORD to local .env."
        )

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "oi_engine" not in st.session_state:
    st.session_state.oi_engine = NandiOIEngine()
if "upstox_client" not in st.session_state:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    try:
        if not is_configured_value(token):
            token = str(st.secrets.get("upstox", {}).get("access_token", ""))
    except Exception:
        pass
    st.session_state.upstox_client = UpstoxOptionChainClient(
        access_token=token if is_configured_value(token) else ""
    )
if "latest_snapshot" not in st.session_state:
    st.session_state.latest_snapshot = None
if "latest_decision" not in st.session_state:
    st.session_state.latest_decision = None
if "decision_log" not in st.session_state:
    st.session_state.decision_log = []


def saved_rsi_strategies() -> dict[str, dict[str, float | int]]:
    """Keep saved RSI settings available to both the focused and unified labs."""
    if "rsi_saved_strategies" not in st.session_state:
        st.session_state.rsi_saved_strategies = STORE.rsi_strategies() or {
            "RSI 14 — 24/72": {"length": 14, "lower": 24.0, "upper": 72.0}
        }
    return st.session_state.rsi_saved_strategies

journal = PaperJournal()


def capture() -> None:
    snapshot = st.session_state.upstox_client.fetch_snapshot()
    decision = st.session_state.oi_engine.add_snapshot(snapshot)
    STORE.save_analysis(snapshot, decision, source="dashboard-manual")
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
        st.markdown(
            """
            <div class="nandi-login-heading">A calm workspace for explainable NIFTY options research.</div>
            <div class="nandi-login-copy">Nandi connects option-chain activity, premium behaviour and NIFTY price structure so every paper-trade decision can be checked against its evidence.</div>
            <div class="nandi-principles">
              <div class="nandi-principle"><div class="nandi-principle-label">LIVE RESEARCH</div><div class="nandi-principle-copy">Current-week NIFTY chain around ATM ±5 strikes.</div></div>
              <div class="nandi-principle"><div class="nandi-principle-label">EXPLAINED</div><div class="nandi-principle-copy">Charts and written reasons for every decision.</div></div>
              <div class="nandi-principle"><div class="nandi-principle-label">PAPER ONLY</div><div class="nandi-principle-copy">Research discipline, never a broker order.</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        with st.container(border=True):
            st.subheader("Private sign in")
            st.caption("Your credentials are checked only against the values in Streamlit Secrets.")
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
    st.markdown(
        f"""
        <div class="nandi-hero">
          <div>
            <div class="nandi-eyebrow">Nandi Intelligence</div>
            <div class="nandi-title">{title}</div>
            <div class="nandi-subtitle">{subtitle}</div>
          </div>
          <div class="nandi-status">PAPER RESEARCH ONLY</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def market_session_panel():
    """Show the exact session decision used to permit live captures."""
    status = MARKET_SCHEDULE.status()
    if status.is_open:
        next_label = f"Live capture allowed until {status.next_open.strftime('%I:%M %p IST')}"
    else:
        next_label = f"Next session: {status.next_open.strftime('%a, %d %b · %I:%M %p IST')}"
    st.markdown(
        f"""
        <div class="nandi-session-card">
          <div>
            <div class="nandi-section-label">NSE EQUITY DERIVATIVES</div>
            <div class="nandi-session-title">{status.label}</div>
            <div class="nandi-session-copy">{status.reason}</div>
          </div>
          <div class="nandi-session-time">{status.observed_at.strftime('%d %b %Y · %I:%M:%S %p IST')}<br>{next_label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return status



def evidence_dashboard_data(evidence: dict) -> None:
    """Render stored or live evidence without changing the strategy decision."""
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


def evidence_dashboard(snapshot, decision) -> None:
    """Render explainable charts from the already-computed OI V1 decision."""
    evidence_dashboard_data(live_evidence(snapshot, decision))


def saved_decision_explanation(saved: dict, *, include_charts: bool = False) -> None:
    """Make the worker's saved result understandable after the browser was closed."""
    recorded_at = str(saved.get("decided_at", "")).replace("T", " ")
    source = str(saved.get("source", "worker")).replace("-", " ")
    action = saved.get("action", "NO TRADE")
    st.caption(f"Saved {recorded_at} IST • source: {source} • result: {action}")
    reasons = saved.get("reasons", [])
    blockers = saved.get("blockers", [])
    if reasons:
        st.markdown("**Evidence Nandi found**")
        for reason in reasons:
            st.success(str(reason))
    if blockers:
        st.markdown("**Why Nandi did not approve a paper trade**")
        for blocker in blockers:
            st.warning(str(blocker))
    if not reasons and not blockers:
        st.info("This saved snapshot has no additional decision text yet. Capture more confirming snapshots.")
    if include_charts:
        evidence = saved.get("evidence", {})
        if isinstance(evidence, dict) and evidence:
            st.markdown('<div class="nandi-section-label">SAVED EVIDENCE</div>', unsafe_allow_html=True)
            st.subheader("Charts behind this saved decision")
            evidence_dashboard_data(evidence)


def command_center() -> None:
    header("Nandi Command Center", "Unified NIFTY option-chain probability strategy")
    market_status = market_session_panel()
    decision = st.session_state.latest_decision
    snapshot = st.session_state.latest_snapshot
    latest = STORE.latest_analysis()
    shown_spot = snapshot.spot if snapshot else (latest["spot"] if latest else None)
    shown_action = decision.action if decision else (latest["action"] if latest else "NO DATA")
    shown_bullish = decision.bullish_score if decision else (latest["bullish_score"] if latest else None)
    shown_bearish = decision.bearish_score if decision else (latest["bearish_score"] if latest else None)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NIFTY", f"{shown_spot:,.2f}" if shown_spot is not None else "Waiting")
    c2.metric("Decision", shown_action)
    c3.metric("Bullish", f"{shown_bullish:.1f}" if shown_bullish is not None else "—")
    c4.metric("Bearish", f"{shown_bearish:.1f}" if shown_bearish is not None else "—")
    st.markdown(
        """
        <div class="nandi-research-card">
          <div class="nandi-section-label">RESEARCH DISCIPLINE</div>
          <div class="nandi-research-title">Nandi waits for evidence; it does not force a trade.</div>
          <div class="nandi-research-copy">V1 needs a quality score of at least 80, a 20-point directional lead, price and premium confirmation, plus three persistent snapshots.</div>
          <div class="nandi-chip-row"><span class="nandi-chip">OI direction</span><span class="nandi-chip">Option premium</span><span class="nandi-chip">NIFTY structure</span><span class="nandi-chip">3-snapshot check</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not market_status.is_open:
        st.caption("Live capture is paused outside the NSE session. The current cloud dashboard does not collect data while the browser is closed.")
    trades = journal.trades_today()
    st.metric("Paper trades today", f"{len(trades)} / 3")

    st.subheader("Always-on research status")
    health = STORE.worker_status()
    if not health:
        st.info("No background worker has reported yet. The Streamlit cloud page is dashboard-only; start local Nandi to analyse while you are away.")
    else:
        heartbeat = datetime.fromisoformat(health["last_heartbeat"])
        now = MARKET_SCHEDULE.status().observed_at.replace(tzinfo=None)
        seconds_ago = max(0, int((now - heartbeat).total_seconds()))
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Worker", health["status"])
        w2.metric("Market state", health["market_state"].replace("_", " "))
        w3.metric("Last heartbeat", f"{seconds_ago}s ago")
        w4.metric("Last snapshot", health["last_snapshot"][-8:] if health.get("last_snapshot") else "Waiting")
        if health.get("last_error"):
            st.error(f"Worker issue: {health['last_error']}")
    if latest:
        st.subheader("Latest saved analysis")
        st.caption("This is the most recent result kept by Nandi, including work done while the dashboard was closed.")
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Saved action", latest["action"])
        a2.metric("Saved NIFTY", f"{latest['spot']:,.2f}")
        a3.metric("Setup quality", f"{latest['setup_quality']:.1f}/100")
        a4.metric("Expiry", latest["expiry"] or "—")
        saved_decision_explanation(latest)
        with st.expander("View exact charts behind the saved analysis"):
            saved_decision_explanation(latest, include_charts=True)


def live_engine() -> None:
    header("Live OI Engine", "Upstox NIFTY current-week chain • ATM ±5 strikes")
    market_status = market_session_panel()
    if not market_status.is_open:
        st.warning("Live option-chain capture is paused. Nandi will allow the next snapshot only in the displayed NSE session.")
    if st.button(
        "Capture Upstox Snapshot", type="primary", use_container_width=True,
        disabled=not market_status.is_open,
    ):
        try:
            capture()
            st.success("Snapshot captured and analysed")
        except UpstoxAPIError as exc:
            st.error(str(exc))

    auto = st.toggle("Auto-capture every 30 seconds", value=False, disabled=not market_status.is_open)
    if auto:
        @st.fragment(run_every="30s")
        def auto_capture_panel() -> None:
            try:
                capture()
                st.caption(f"Last automatic capture: {MARKET_SCHEDULE.status().observed_at.strftime('%I:%M:%S %p IST')}")
            except UpstoxAPIError as exc:
                st.error(str(exc))
        auto_capture_panel()

    snapshot = st.session_state.latest_snapshot
    decision = st.session_state.latest_decision
    if not snapshot or not decision:
        latest = STORE.latest_analysis()
        if latest:
            st.info("There is no new in-browser capture yet. Nandi's most recently saved worker analysis is shown below.")
            saved_decision_explanation(latest, include_charts=True)
        else:
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
            st.success(reason)
    if decision.blockers:
        st.subheader("Why Nandi is waiting")
        for blocker in decision.blockers:
            st.warning(blocker)

    rows = [{"Strike": x.strike, "Side": x.side, "OI": x.oi, "ΔOI": x.change_oi,
             "LTP": x.ltp, "ΔLTP": x.change_ltp, "Activity": x.activity,
             "Volume": x.volume, "Spread %": round(x.spread_pct, 2)} for x in snapshot.legs]
    frame = pd.DataFrame(rows)
    nearest = sorted(frame.Strike.unique(), key=lambda x: abs(x - snapshot.spot))[:11]
    st.dataframe(frame[frame.Strike.isin(nearest)].sort_values(["Strike", "Side"]), use_container_width=True, hide_index=True)
    st.markdown('<div class="nandi-section-label">DECISION EXPLAINER</div>', unsafe_allow_html=True)
    st.subheader("Evidence behind this decision")
    st.caption("Every chart uses the same snapshot data that produced the decision above. The display explains the strategy; it does not change the OI V1 rules.")
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

    st.subheader("Background analysis saved today")
    saved_rows = STORE.recent_decisions(limit=1000, trading_day=MARKET_SCHEDULE.status().observed_at.date())
    if saved_rows:
        background = pd.DataFrame(saved_rows)
        background["decided_at"] = pd.to_datetime(background["decided_at"])
        chronological = background.sort_values("decided_at")
        score_tab, market_tab = st.tabs(["Evidence scores", "NIFTY and setup quality"])
        with score_tab:
            st.line_chart(chronological.set_index("decided_at")[["bullish_score", "bearish_score", "setup_quality"]])
        with market_tab:
            st.line_chart(chronological.set_index("decided_at")[["spot"]])
            st.line_chart(chronological.set_index("decided_at")[["setup_quality"]])
        r1, r2, r3 = st.columns(3)
        r1.metric("Saved snapshots", len(background))
        r2.metric("Approved setups", int((background["action"] != "NO TRADE").sum()))
        r3.metric("WAIT decisions", int((background["action"] == "NO TRADE").sum()))
        st.dataframe(background, use_container_width=True, hide_index=True)
    else:
        st.info("No background analysis has been saved for this date.")

    st.subheader("Nandi alerts")
    alerts = STORE.recent_alerts(limit=50)
    if alerts:
        st.dataframe(pd.DataFrame(alerts), use_container_width=True, hide_index=True)
    else:
        st.info("No approved-setup or daily-report alerts have been recorded yet.")

    saved_report = STORE.latest_daily_report()
    st.subheader("Latest automatic closing report")
    if saved_report:
        st.success(saved_report["summary"])
    else:
        st.info("The local worker creates this only after an NSE market session closes.")


def local_setup() -> None:
    header("Run Nandi locally", "Free, paper-only background research on your own computer")
    st.info("The hosted dashboard is for viewing. To keep research running while you are away, run the local worker on a computer that stays on during market hours.")
    first, second, third = st.columns(3)
    with first:
        st.markdown("""
        <div class="nandi-setup-step"><div class="nandi-setup-number">STEP 1</div><div class="nandi-setup-title">Install Docker Desktop</div><div class="nandi-setup-copy">This is free for personal use and keeps Nandi's dashboard and worker together.</div></div>
        """, unsafe_allow_html=True)
    with second:
        st.markdown("""
        <div class="nandi-setup-step"><div class="nandi-setup-number">STEP 2</div><div class="nandi-setup-title">Create your local .env</div><div class="nandi-setup-copy">Copy .env.example, then add your private login and read-only Upstox token. The file is never committed.</div></div>
        """, unsafe_allow_html=True)
    with third:
        st.markdown("""
        <div class="nandi-setup-step"><div class="nandi-setup-number">STEP 3</div><div class="nandi-setup-title">Start Nandi</div><div class="nandi-setup-copy">One command runs the local dashboard and market-hours worker. It places no broker orders.</div></div>
        """, unsafe_allow_html=True)
    st.subheader("Commands to run in the project folder")
    st.code("docker compose run --rm worker python worker.py --check\ndocker compose up -d --build", language="bash")
    st.caption("The check does not contact Upstox or print your token. After startup, open http://localhost:8501.")
    st.subheader("What Nandi will do")
    st.write("It follows IST, runs on weekdays from 09:15 to 15:30 (excluding the NSE holidays you configure), saves each paper-research snapshot locally, and retries safely if Upstox is temporarily unavailable.")
    st.write("Detailed setup, status commands and safe restart instructions are in `ALWAYS_ON.md` in the project.")


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


def daily_strategy_analysis(result) -> None:
    """One chart-led daily workspace for all historical strategies and OI evidence."""
    dates = result.available_dates()
    if not dates:
        st.warning("This saved result was created before daily evidence was available. Run the full backtest again.")
        return

    st.markdown('<div class="nandi-section-label">ONE DAILY WORKSPACE</div>', unsafe_allow_html=True)
    st.subheader("Daily strategy analysis")
    st.caption(
        "Choose one trading day and one strategy. The charts, the OI evidence, the technical analysis and the actual "
        "paper result stay together here—there is no need to open separate strategy pages."
    )
    first, second = st.columns(2)
    selected_day = first.selectbox(
        "Trading day to inspect", list(reversed(dates)),
        format_func=lambda item: item.strftime("%A, %d %b %Y"),
    )
    labels = [run.label for run in result.runs]
    selected_label = second.selectbox("Strategy to inspect", labels)
    run = result.strategy_run(selected_label)
    background = run.background

    st.markdown('<div class="nandi-section-label">STRATEGY BACKGROUND</div>', unsafe_allow_html=True)
    st.subheader(run.label)
    source, technical, entry, risk = st.columns(4)
    source.markdown("**Data used**")
    source.caption(background.data_used)
    technical.markdown("**Technical analysis**")
    technical.caption(background.technical_analysis)
    entry.markdown("**Entry / confirmation**")
    entry.caption(background.entry_rule)
    risk.markdown("**Paper risk rule**")
    risk.caption(background.paper_risk)
    st.info(background.purpose)

    day_rows = result.daily_strategy_rows(selected_day)
    selected_outcome = next(
        (row for row in day_rows if row["Strategy"] == run.strategy and row["Contract"] == run.contract),
        None,
    )
    if selected_outcome:
        o1, o2, o3, o4, o5 = st.columns(5)
        o1.metric("Historical snapshots", selected_outcome["Historical snapshots"])
        o2.metric("Paper trades", selected_outcome["Paper trades"])
        o3.metric("Wins", selected_outcome["Wins"])
        o4.metric("Net premium points", selected_outcome["Net premium points"])
        o5.metric("Daily result", selected_outcome["Daily result"])

    chart_rows = result.daily_chart_rows(
        selected_day,
        rsi_length=background.rsi_length,
        rsi_lower=background.rsi_lower,
        rsi_upper=background.rsi_upper,
    )
    if not chart_rows:
        st.warning("No nearest-weekly historical snapshots were returned for this day.")
        return
    chart = pd.DataFrame(chart_rows).set_index("Timestamp")
    st.markdown('<div class="nandi-section-label">SAME DATA NANDI REPLAYED</div>', unsafe_allow_html=True)
    st.subheader("NIFTY price structure and OI walls")
    st.caption("NIFTY spot is compared with its historical recent high/low. The CE and PE OI-wall lines show where the largest nearby option positioning sat on each snapshot.")
    price, walls = st.columns(2)
    price.line_chart(chart[["NIFTY spot", "Recent high", "Recent low"]])
    walls.line_chart(chart[["CE OI wall", "PE OI wall"]])

    st.subheader("OI flow and final evidence score")
    st.caption("These are calculated from the actual five-minute option-chain snapshots used in the replay—not invented chart values.")
    oi, score = st.columns(2)
    oi.bar_chart(chart[["Nearby CE ΔOI", "Nearby PE ΔOI"]])
    score.line_chart(chart[["OI bullish score", "OI bearish score", "Nandi bullish score", "Nandi bearish score"]])

    st.subheader(f"RSI({background.rsi_length}) and option confirmation")
    st.caption("RSI is shown for the currently selected strategy. The horizontal reference lines are its saved lower and upper levels; premium lines are the ATM option values Nandi checked.")
    rsi_column = f"RSI({background.rsi_length})"
    rsi, premium = st.columns(2)
    rsi.line_chart(chart[[rsi_column, "RSI lower level", "RSI upper level"]])
    premium.line_chart(chart[["ATM CE premium", "ATM PE premium"]])

    st.markdown('<div class="nandi-section-label">TECHNICAL CONTEXT</div>', unsafe_allow_html=True)
    st.subheader("Trend, volatility and momentum indicators")
    st.caption(
        "EMA, SMA, Bollinger Bands, MACD and rate of change are calculated from the same five-minute NIFTY spot points. "
        "They are visible for technical analysis, but they are not silently added to the tested Nandi OI V1 approval rule."
    )
    trend, bands = st.columns(2)
    trend.line_chart(chart[["NIFTY spot", "EMA 9", "EMA 21", "SMA 20"]])
    bands.line_chart(chart[["NIFTY spot", "Bollinger upper", "Bollinger middle", "Bollinger lower"]])
    macd, roc = st.columns(2)
    macd.line_chart(chart[["MACD line", "MACD signal", "MACD histogram"]])
    roc.bar_chart(chart[["ROC 10 %"]])

    st.markdown('<div class="nandi-section-label">EXACT CALCULATION</div>', unsafe_allow_html=True)
    st.subheader("Choose one chart point and inspect the full Nandi calculation")
    selected_timestamp = st.selectbox(
        "Snapshot time", list(chart.index), index=len(chart.index) - 1,
        format_func=lambda item: item.strftime("%d %b %Y · %I:%M %p"),
    )
    evidence_row = next(row for row in chart_rows if row["Timestamp"] == selected_timestamp)
    calc, gates = st.columns(2)
    calc.markdown("**Weighted bullish and bearish score**")
    calc.dataframe(pd.DataFrame(result.calculation_rows(evidence_row)), use_container_width=True, hide_index=True)
    gates.markdown("**Approval gates at this exact snapshot**")
    gates.dataframe(pd.DataFrame(result.approval_rows(evidence_row)), use_container_width=True, hide_index=True)
    st.caption(
        f"Final calculation at {selected_timestamp.strftime('%I:%M %p')}: "
        f"bullish {evidence_row['Nandi bullish score']:.1f}/100 • bearish {evidence_row['Nandi bearish score']:.1f}/100 • "
        f"action: {evidence_row['Final action']}."
    )

    st.subheader("Raw OI option-chain rows behind this calculation")
    st.caption("This is the exact nearest-weekly ATM ±5 option table Nandi used for the selected chart point. OI activity is calculated from change in OI and option-premium change.")
    st.dataframe(
        pd.DataFrame(result.option_chain_rows(selected_timestamp.to_pydatetime())),
        use_container_width=True, hide_index=True,
    )
    with st.expander("Where this data came from and how Nandi used it"):
        st.dataframe(
            pd.DataFrame(result.data_provenance_rows(selected_timestamp.to_pydatetime(), run)),
            use_container_width=True, hide_index=True,
        )

    decisions = chart[chart["Final action"] != "NO TRADE"]
    if decisions.empty:
        st.info("No full Nandi OI V1 entry was approved on this day. That is a valid daily result: the five evidence gates did not all align.")
    else:
        st.markdown("**Full Nandi OI V1 approved events on this day**")
        st.dataframe(
            decisions.reset_index()[[
                "Timestamp", "Final action", "Nandi bullish score", "Nandi bearish score",
                "OI bullish score", "OI bearish score", "Price bullish", "Price bearish",
                "Premium bullish", "Premium bearish", "Persistence bullish", "Persistence bearish",
            ]],
            use_container_width=True, hide_index=True,
        )

    st.subheader("Every strategy tested on this trading day")
    st.caption("This table keeps the tests comparable while still showing the technical method used by every strategy.")
    daily_frame = pd.DataFrame(day_rows)
    st.dataframe(daily_frame, use_container_width=True, hide_index=True)
    st.download_button(
        "Download Daily Nandi Strategy Analysis",
        daily_frame.to_csv(index=False).encode("utf-8"),
        file_name=f"nandi_daily_strategy_analysis_{selected_day}.csv",
        mime="text/csv", use_container_width=True,
    )


def unified_backtest_lab() -> None:
    header("Unified Backtest", "Every implemented Nandi strategy • one comparable historical report")
    st.info(
        "Nandi now keeps every strategy in one daily chart-led report. It tests OI flow, price structure, OI walls, "
        "option premium/liquidity and persistence, then the final Nandi OI V1 rule and each saved RSI setup. "
        "The daily view explains the data, indicator and paper-risk rule behind every result."
    )
    st.caption(
        "Historical option data is replayed as five-minute snapshots without future-data access. "
        "The optional RSI audit uses NIFTY one-minute candles across all selected timeframes."
    )

    period = st.selectbox(
        "Unified test period", ["Single day", "One week", "One month", "Custom dates"],
    )
    latest = date.today() - timedelta(days=1)
    if period == "Single day":
        selected_date = st.date_input("Unified trading date", value=latest, max_value=latest)
        start = end = selected_date
    elif period == "One week":
        end = st.date_input("Unified week ending date", value=latest, max_value=latest)
        start = end - timedelta(days=6)
    elif period == "One month":
        end = st.date_input("Unified month ending date", value=latest, max_value=latest)
        start = end - timedelta(days=29)
    else:
        first, second = st.columns(2)
        start = first.date_input("Unified start date", value=latest - timedelta(days=29), max_value=latest)
        end = second.date_input("Unified end date", value=latest, min_value=start, max_value=latest)

    saved = saved_rsi_strategies()
    strategy_names = list(saved)
    selected_names = st.multiselect(
        "Saved RSI strategies to include", strategy_names, default=strategy_names,
        help="Manage or add these saved settings in RSI Strategy Lab.",
    )
    audit_timeframes = st.multiselect(
        "RSI touch-audit timeframes", list(TIMEFRAMES), default=list(TIMEFRAMES),
        format_func=lambda value: "1 hour" if value == 60 else f"{value} min",
    )
    left, right = st.columns(2)
    left.metric("Start date", start.strftime("%d %b %Y"))
    right.metric("End date", end.strftime("%d %b %Y"))
    st.caption("Requires Upstox Plus historical access. No broker order is sent.")

    if st.button("Run Full Nandi Backtest", type="primary", use_container_width=True):
        progress_bar = st.progress(0.0, text="Preparing full Nandi historical replay…")
        try:
            if not selected_names:
                raise ValueError("Select at least one saved RSI strategy")
            if not audit_timeframes:
                raise ValueError("Select at least one RSI touch-audit timeframe")
            token = st.session_state.upstox_client.access_token
            client = UpstoxHistoricalClient(access_token=token, timeout_seconds=20)
            progress_bar.progress(0.05, text="Loading NIFTY one-minute RSI history…")
            minute_closes = client.spot_candles(start - timedelta(days=45), end, interval_minutes=1)

            def weekly_progress(done: int, total: int, label: str) -> None:
                progress_bar.progress(
                    0.05 + 0.43 * done / max(total, 1),
                    text=f"Nearest-weekly options {done}/{total}: {label}",
                )

            weekly = client.build_snapshots(start, end, progress=weekly_progress, expiry_mode="weekly")
            progress_bar.progress(0.50, text="Loading nearest-monthly option history…")

            def monthly_progress(done: int, total: int, label: str) -> None:
                progress_bar.progress(
                    0.50 + 0.43 * done / max(total, 1),
                    text=f"Nearest-monthly options {done}/{total}: {label}",
                )

            monthly = client.build_snapshots(start, end, progress=monthly_progress, expiry_mode="monthly")
            selected_strategies = {name: saved[name] for name in selected_names}
            result = UnifiedBacktester(rsi_timeframes=audit_timeframes).run(
                weekly, monthly, selected_strategies, one_minute_closes=minute_closes,
            )
            st.session_state.unified_backtest_result = result
            progress_bar.progress(1.0, text="Full Nandi backtest completed")
            st.success("All selected Nandi strategies were replayed. No broker order was placed.")
        except (UpstoxAPIError, ValueError) as exc:
            progress_bar.empty()
            st.error(str(exc))

    result = st.session_state.get("unified_backtest_result")
    if not result:
        st.write("Run the full replay to inspect every OI evidence gate, final OI V1 and every saved RSI setup by trading day, with the underlying charts and strategy background.")
        return

    daily_strategy_analysis(result)

    st.divider()
    st.markdown('<div class="nandi-section-label">WHOLE PERIOD COMPARISON</div>', unsafe_allow_html=True)

    summary = pd.DataFrame(result.summary_rows())
    st.subheader("All strategy results across the selected period")
    st.caption(
        "Use this to inspect historical evidence, not to choose a guaranteed winner. "
        "A result with fewer trades has less evidence than a result observed across many trades."
    )
    st.dataframe(summary, use_container_width=True, hide_index=True)
    chart_summary = summary.copy()
    chart_summary["Strategy run"] = chart_summary["Strategy"] + " — " + chart_summary["Contract"]
    win_tab, points_tab, drawdown_tab = st.tabs(["Win rate", "Net premium points", "Drawdown"])
    with win_tab:
        st.bar_chart(chart_summary.set_index("Strategy run")[["Win rate %"]])
    with points_tab:
        st.bar_chart(chart_summary.set_index("Strategy run")[["Net premium points"]])
    with drawdown_tab:
        st.bar_chart(chart_summary.set_index("Strategy run")[["Maximum drawdown"]])

    equity_rows = result.equity_rows()
    if equity_rows:
        equity = pd.DataFrame(equity_rows)
        equity["Closed at"] = pd.to_datetime(equity["Closed at"])
        curve = equity.pivot(index="Closed at", columns="Strategy run", values="Cumulative premium points")
        st.subheader("Separate cumulative premium curves")
        st.caption("Each curve is kept separate; summing them would falsely assume the same capital can enter every strategy at once.")
        st.line_chart(curve)

    touch_rows = result.rsi_touch_rows()
    if touch_rows:
        touches = pd.DataFrame(touch_rows)
        st.subheader("RSI touch audit across every selected timeframe")
        st.caption("This confirms how often each saved RSI strategy saw an independent lower or upper zone entry on the NIFTY chart.")
        st.dataframe(touches, use_container_width=True, hide_index=True)
        st.bar_chart(touches.pivot(index="Timeframe", columns="Strategy", values="Total touches"))

    ledger_rows = result.ledger_rows()
    st.subheader("All paper-trade ledger rows")
    if ledger_rows:
        ledger = pd.DataFrame(ledger_rows)
        st.dataframe(ledger, use_container_width=True, hide_index=True)
        st.download_button(
            "Download Full Nandi Trade Ledger", ledger.to_csv(index=False).encode("utf-8"),
            file_name=f"nandi_unified_ledger_{result.start_date}_{result.end_date}.csv",
            mime="text/csv", use_container_width=True,
        )
    else:
        st.info("No paper trades were triggered by the selected strategies in this period. The no-trade counts above are still evidence.")
    st.download_button(
        "Download Full Nandi Strategy Comparison", summary.to_csv(index=False).encode("utf-8"),
        file_name=f"nandi_unified_summary_{result.start_date}_{result.end_date}.csv",
        mime="text/csv", use_container_width=True,
    )


def rsi_backtest_lab() -> None:
    header("RSI Strategy Lab", "Editable RSI period and levels • multi-timeframe touch analysis")
    st.info("The old 20% stop / 30% target is removed. RSI trades use a 5% premium stop; the target is the opposite editable RSI level. Nandi V1 OI remains unchanged.")

    st.subheader("Strategy settings")
    saved = saved_rsi_strategies()
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
            STORE.save_rsi_strategy(strategy_name, length, lower, upper)
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
                STORE.save_rsi_strategy(
                    str(name), int(setup["length"]), float(setup["lower"]), float(setup["upper"]),
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
    st.code(
        '[upstox]\naccess_token = "PASTE_YOUR_ANALYTICS_TOKEN_HERE"\n\n'
        '[nse]\nholidays = ["YYYY-MM-DD"]',
        language="toml",
    )
    st.write("Add this to your Streamlit app Secrets. Never paste the token into source code or chat.")
    st.write("For local always-on Nandi, put the same read-only token in your private `.env` file instead. Sample values beginning with `YOUR_` or `PASTE_` are rejected.")
    st.write("Nandi always uses IST. Add official NSE trading-holiday dates to `nse.holidays` so captures are paused on exchange holidays too.")
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
    "Unified Backtest": unified_backtest_lab,
    "RSI Strategy Lab": rsi_backtest_lab,
    "Local Setup": local_setup,
    "Settings": settings,
}
if not st.session_state.logged_in:
    login_page()
else:
    with st.sidebar:
        st.markdown(
            """
            <div class="nandi-brand">
              <div class="nandi-mark">N</div>
              <div>
                <div class="nandi-brand-name">Nandi</div>
                <div class="nandi-brand-copy">MARKET INTELLIGENCE</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        page = st.radio("Navigation", list(pages), label_visibility="collapsed")
        st.divider()
        st.caption("RESEARCH STATUS")
        st.success("Paper mode active")
        st.caption(MARKET_SCHEDULE.status().observed_at.strftime("%d %b %Y · %I:%M %p IST"))
        if st.button("Logout", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()
    pages[page]()
