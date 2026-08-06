from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from nandi_oi.auth import CredentialConfigurationError, LoginLockout
from nandi_oi.configuration import is_configured_value
from nandi_v2.email_alerts import SMTPEmailAlertSink, SMTPSettings, is_entry_alert
from nandi_v2.engine import decide, strike_evidence_rows
from nandi_v2.history import DecisionHistory
from nandi_v2.models import Decision, DecisionAction, MarketContext, OptionChainSnapshot
from nandi_v2.nse import NSEDataError, NSEPublicClient

IST = ZoneInfo("Asia/Kolkata")

st.set_page_config(page_title="Nandi", page_icon="N", layout="wide", initial_sidebar_state="expanded")
st.markdown(
    """
    <style>
    :root {--nandi-green:#126b3a;--nandi-green-dark:#0b4e2a;--nandi-green-soft:#edf7f1;--nandi-ink:#17271f;--nandi-muted:#65756d;--nandi-line:#dbe8e0;--nandi-warm:#f8fbf9;--nandi-red:#9f2d2d;--nandi-amber:#8a5a05;}
    .stApp{background:#fff;color:var(--nandi-ink)}.block-container{max-width:1500px;padding-top:1.5rem;padding-bottom:4rem}section[data-testid="stSidebar"]{background:var(--nandi-warm);border-right:1px solid var(--nandi-line)}h1,h2,h3{color:var(--nandi-ink);letter-spacing:-.025em}
    div[data-testid="stMetric"]{background:#fff;border:1px solid var(--nandi-line);border-radius:14px;padding:14px 16px;box-shadow:0 5px 16px rgba(18,107,58,.04)}div[data-testid="stMetricLabel"]{color:var(--nandi-muted);font-size:.75rem;font-weight:750;letter-spacing:.04em;text-transform:uppercase}
    .nandi-hero{display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;border:1px solid var(--nandi-line);border-radius:18px;padding:1.35rem 1.55rem;margin-bottom:1.25rem;background:linear-gradient(112deg,#fff 55%,#eef8f2 100%);position:relative;overflow:hidden}.nandi-hero:before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--nandi-green)}
    .nandi-eyebrow{color:var(--nandi-green);font-size:.7rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase}.nandi-title{color:var(--nandi-ink);font-size:1.85rem;font-weight:800;margin:.15rem 0}.nandi-copy{color:var(--nandi-muted);max-width:850px;font-size:.92rem}.nandi-badge{border:1px solid #b9ddc8;color:var(--nandi-green-dark);background:#fff;border-radius:999px;padding:.42rem .72rem;font-size:.7rem;font-weight:800;white-space:nowrap}
    .decision-card{border:1px solid var(--nandi-line);border-radius:18px;background:#fff;padding:1.15rem 1.25rem;min-height:430px}.decision-label{color:var(--nandi-muted);font-size:.7rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.decision-value{color:var(--nandi-ink);font-size:2rem;font-weight:850;letter-spacing:-.04em;margin:.25rem 0 .1rem}.decision-buy{color:var(--nandi-green-dark)}.decision-wait{color:var(--nandi-amber)}.decision-no{color:var(--nandi-red)}
    .status-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.65rem;margin-top:.9rem}.status-cell{border:1px solid var(--nandi-line);border-radius:12px;padding:.7rem .75rem}.status-cell-label{color:var(--nandi-muted);font-size:.68rem;font-weight:750;text-transform:uppercase;letter-spacing:.07em}.status-cell-value{color:var(--nandi-ink);font-weight:780;margin-top:.18rem}.reason{border-left:3px solid var(--nandi-green);padding:.35rem .65rem;margin:.38rem 0;color:var(--nandi-ink);background:var(--nandi-green-soft);border-radius:0 8px 8px 0}.blocker{border-left:3px solid var(--nandi-red);padding:.35rem .65rem;margin:.38rem 0;color:var(--nandi-ink);background:#fbf1f1;border-radius:0 8px 8px 0}.source-note{color:var(--nandi-muted);font-size:.78rem;line-height:1.45}.stButton>button{border-radius:10px;font-weight:750}.stButton>button[kind="primary"]{background:var(--nandi-green);border-color:var(--nandi-green)}[data-testid="stDataFrame"]{border:1px solid var(--nandi-line);border-radius:12px;overflow:hidden}
    @media(max-width:800px){.nandi-badge{display:none}.status-grid{grid-template-columns:1fr}.decision-value{font-size:1.65rem}}
    </style>
    """,
    unsafe_allow_html=True,
)

os.makedirs("data", exist_ok=True)


def secret_section(name: str) -> dict[str, Any]:
    try:
        value = st.secrets.get(name, {})
        return dict(value) if value else {}
    except Exception:
        return {}


def configured_auth() -> tuple[str | None, str | None, str]:
    auth = secret_section("auth")
    username = str(auth.get("username", ""))
    password = str(auth.get("password", ""))
    if not (is_configured_value(username) and is_configured_value(password)):
        username = os.getenv("NANDI_AUTH_USERNAME", "")
        password = os.getenv("NANDI_AUTH_PASSWORD", "")
    if is_configured_value(username) and is_configured_value(password):
        return username, password, ""
    return None, None, "Authentication is not configured. Add auth.username and auth.password to Streamlit Secrets."


APP_USERNAME, APP_PASSWORD, AUTH_ERROR = configured_auth()


@st.cache_resource
def nse_client() -> NSEPublicClient:
    return NSEPublicClient()


@st.cache_resource
def history_store() -> DecisionHistory:
    return DecisionHistory()


def header(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="nandi-hero"><div><div class="nandi-eyebrow">Nandi V2</div><div class="nandi-title">{escape(title)}</div><div class="nandi-copy">{escape(subtitle)}</div></div><div class="nandi-badge">NSE OI · TRADINGVIEW CHART · RESEARCH ONLY</div></div>', unsafe_allow_html=True)


def initialise_state() -> None:
    defaults: dict[str, Any] = {
        "logged_in": False,"latest_oi_snapshot": None,"latest_spot": None,"latest_spot_timestamp": None,
        "last_oi_fetch_at": None,"last_spot_fetch_at": None,"last_data_error": "","spot_points": [],
        "latest_raw_decision": None,"latest_confirmed_decision": None,"candidate_side": "","candidate_count": 0,
        "last_history_signature": "","last_email_status": "","force_refresh": False,"trade_threshold": 75.0,
        "email_threshold": 80.0,"oi_refresh_seconds": 60,"spot_refresh_seconds": 5,"confirmation_evaluations": 3,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


initialise_state()


def login_page() -> None:
    header("Nandi", "One explainable NIFTY decision engine. No broker orders and no hidden data fallback.")
    left, right = st.columns([1.3, 1], gap="large")
    with left:
        st.markdown("### Capital protection first\nNandi combines the latest available NSE option-chain snapshot with NIFTY price momentum. It returns only **BUY CE**, **BUY PE**, **PREPARE**, or **NO TRADE**. TradingView is used only to display the NIFTY chart.")
    with right:
        with st.container(border=True):
            st.subheader("Private sign in")
            with st.form("login_form"):
                username = st.text_input("Email or username")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign in", use_container_width=True)
            if submitted:
                try:
                    result = LoginLockout(st.session_state).authenticate(username, password, APP_USERNAME, APP_PASSWORD)
                except CredentialConfigurationError:
                    st.error(AUTH_ERROR)
                else:
                    if result.authenticated:
                        st.session_state.logged_in = True
                        st.rerun()
                    elif result.locked:
                        minutes = max(1, (result.retry_after_seconds + 59) // 60)
                        st.error(f"Too many failed attempts. Retry in about {minutes} minute(s).")
                    else:
                        st.error(f"Invalid credentials. {result.attempts_remaining} attempt(s) remaining.")
            if AUTH_ERROR:
                st.warning(AUTH_ERROR)


def tradingview_chart() -> None:
    components.html("""
    <div class="tradingview-widget-container" style="height:640px;width:100%"><div id="tradingview_nandi" style="height:calc(100% - 32px);width:100%"></div><div class="tradingview-widget-copyright"><a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank"><span class="blue-text">Track NIFTY on TradingView</span></a></div><script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script><script type="text/javascript">new TradingView.widget({"autosize":true,"symbol":"NSE:NIFTY","interval":"5","timezone":"Asia/Kolkata","theme":"light","style":"1","locale":"en","enable_publishing":false,"allow_symbol_change":false,"hide_side_toolbar":false,"studies":["RSI@tv-basicstudies"],"container_id":"tradingview_nandi"});</script></div>
    """, height=660, scrolling=False)


def now_ist() -> datetime:
    return datetime.now(IST)


def seconds_since(value: datetime | None, now: datetime) -> float:
    return float("inf") if value is None else max(0.0, (now - value).total_seconds())


def append_spot(spot: float, timestamp: datetime) -> None:
    points = list(st.session_state.spot_points)
    item = (timestamp.isoformat(), float(spot))
    if not points or points[-1] != item:
        points.append(item)
    st.session_state.spot_points = points[-240:]


def momentum_rsi(points: list[tuple[str, float]], period: int = 14) -> float | None:
    prices = [float(value) for _, value in points]
    if len(prices) < period + 1:
        return None
    changes = [b - a for a, b in zip(prices, prices[1:])][-period:]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def market_context(now: datetime) -> MarketContext:
    prices = [float(value) for _, value in st.session_state.spot_points]
    previous = prices[-2] if len(prices) >= 2 else None
    reference = prices[-21:-1] if len(prices) >= 3 else prices[:-1]
    return MarketContext(observed_at=now, previous_spot=previous, recent_high=max(reference) if reference else None, recent_low=min(reference) if reference else None, momentum_rsi=momentum_rsi(st.session_state.spot_points))


def refresh_market_data(now: datetime) -> None:
    fetch_oi = st.session_state.force_refresh or st.session_state.latest_oi_snapshot is None or seconds_since(st.session_state.last_oi_fetch_at, now) >= st.session_state.oi_refresh_seconds
    fetch_spot = st.session_state.force_refresh or st.session_state.latest_spot is None or seconds_since(st.session_state.last_spot_fetch_at, now) >= st.session_state.spot_refresh_seconds
    st.session_state.force_refresh = False
    errors: list[str] = []
    if fetch_oi:
        try:
            snapshot = nse_client().fetch_option_chain("NIFTY")
            st.session_state.latest_oi_snapshot = snapshot
            st.session_state.latest_spot = snapshot.spot
            st.session_state.latest_spot_timestamp = snapshot.timestamp
            append_spot(snapshot.spot, snapshot.timestamp)
        except NSEDataError as exc:
            errors.append(str(exc))
        finally:
            st.session_state.last_oi_fetch_at = now
    if fetch_spot:
        try:
            spot, stamp = nse_client().fetch_nifty_spot()
            st.session_state.latest_spot = spot
            st.session_state.latest_spot_timestamp = stamp
            append_spot(spot, stamp)
        except NSEDataError as exc:
            errors.append(str(exc))
        finally:
            st.session_state.last_spot_fetch_at = now
    st.session_state.last_data_error = " | ".join(dict.fromkeys(errors))


def confirm_decision(raw: Decision) -> Decision:
    if raw.action not in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}:
        st.session_state.candidate_side = ""
        st.session_state.candidate_count = 0
        return raw
    if st.session_state.candidate_side == raw.side:
        st.session_state.candidate_count += 1
    else:
        st.session_state.candidate_side = raw.side
        st.session_state.candidate_count = 1
    required = int(st.session_state.confirmation_evaluations)
    if st.session_state.candidate_count >= required:
        return raw
    action = DecisionAction.PREPARE_CE if raw.side == "CE" else DecisionAction.PREPARE_PE
    blocker = f"Waiting for confirmation evaluation {st.session_state.candidate_count}/{required}"
    return replace(raw, action=action, blockers=tuple(dict.fromkeys(raw.blockers + (blocker,))))


def smtp_settings() -> SMTPSettings:
    return SMTPSettings.from_mapping(secret_section("alerts"))


def record_and_alert(decision: Decision, snapshot: OptionChainSnapshot) -> None:
    signature = f"{decision.action.value}:{round(decision.score,1)}:{round(snapshot.spot,1)}:{snapshot.timestamp.isoformat()}"
    if signature != st.session_state.last_history_signature:
        signal_key = history_store().append(decision, snapshot.spot, snapshot.expiry)
        st.session_state.last_history_signature = signature
    else:
        signal_key = history_store().signal_key(decision, snapshot.spot, snapshot.expiry)
    if not is_entry_alert(decision, float(st.session_state.email_threshold)) or history_store().alert_exists(signal_key):
        return
    delivery = SMTPEmailAlertSink(smtp_settings()).send_decision(decision, snapshot.spot, snapshot.expiry)
    history_store().record_alert(signal_key, delivery.delivered, delivery.error)
    st.session_state.last_email_status = "Email alert delivered" if delivery.delivered else delivery.error


def action_class(action: DecisionAction) -> str:
    if action in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}:
        return "decision-buy"
    if action in {DecisionAction.PREPARE_CE, DecisionAction.PREPARE_PE}:
        return "decision-wait"
    return "decision-no"


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def decision_html(decision: Decision, snapshot: OptionChainSnapshot) -> str:
    reasons = "".join(f'<div class="reason">{escape(reason)}</div>' for reason in decision.reasons)
    blockers = "".join(f'<div class="blocker">{escape(blocker)}</div>' for blocker in decision.blockers)
    levels = decision.levels
    strike = "—" if decision.selected_strike is None else f"{decision.selected_strike:.0f}"
    data_time = snapshot.timestamp.astimezone(IST).strftime("%I:%M:%S %p")
    return f'''<div class="decision-card"><div class="decision-label">Final decision</div><div class="decision-value {action_class(decision.action)}">{escape(decision.action.value)}</div><div class="source-note">Score {decision.score:.1f}/100 · {escape(decision.market_state)}</div><div class="status-grid"><div class="status-cell"><div class="status-cell-label">CE score</div><div class="status-cell-value">{decision.ce_score:.1f}</div></div><div class="status-cell"><div class="status-cell-label">PE score</div><div class="status-cell-value">{decision.pe_score:.1f}</div></div><div class="status-cell"><div class="status-cell-label">Strike</div><div class="status-cell-value">{strike}</div></div><div class="status-cell"><div class="status-cell-label">Expiry</div><div class="status-cell-value">{escape(snapshot.expiry)}</div></div><div class="status-cell"><div class="status-cell-label">Entry</div><div class="status-cell-value">{fmt(levels.entry)}</div></div><div class="status-cell"><div class="status-cell-label">Stop</div><div class="status-cell-value">{fmt(levels.stop)}</div></div><div class="status-cell"><div class="status-cell-label">Target 1</div><div class="status-cell-value">{fmt(levels.target_1)}</div></div><div class="status-cell"><div class="status-cell-label">Target 2</div><div class="status-cell-value">{fmt(levels.target_2)}</div></div></div><div style="margin-top:.9rem">{reasons}{blockers}</div><div class="source-note" style="margin-top:.8rem">NSE OI timestamp: {data_time} IST. TradingView is chart display only.</div></div>'''


def build_live_decision(now: datetime) -> tuple[OptionChainSnapshot | None, Decision | None]:
    refresh_market_data(now)
    oi_snapshot = st.session_state.latest_oi_snapshot
    spot = st.session_state.latest_spot
    if oi_snapshot is None or spot is None:
        return None, None
    snapshot = replace(oi_snapshot, spot=float(spot))
    raw = decide(snapshot, market_context(now), trade_threshold=float(st.session_state.trade_threshold), prepare_threshold=max(60.0, float(st.session_state.trade_threshold) - 10.0))
    confirmed = confirm_decision(raw)
    st.session_state.latest_raw_decision = raw
    st.session_state.latest_confirmed_decision = confirmed
    record_and_alert(confirmed, snapshot)
    return snapshot, confirmed


@st.fragment(run_every="1s")
def live_decision_fragment() -> None:
    snapshot, decision = build_live_decision(now_ist())
    if snapshot is None or decision is None:
        st.error("Live NSE data are not available yet.")
        if st.session_state.last_data_error:
            st.caption(st.session_state.last_data_error)
        st.info("Nandi stays at NO TRADE until a valid NSE option-chain snapshot is available.")
        return
    st.markdown(decision_html(decision, snapshot), unsafe_allow_html=True)
    if st.session_state.last_data_error:
        st.warning("Latest network refresh failed; Nandi is using the last valid NSE snapshot. " + st.session_state.last_data_error)
    if st.session_state.last_email_status:
        st.caption(st.session_state.last_email_status)


@st.fragment(run_every="1s")
def live_evidence_fragment() -> None:
    snapshot = st.session_state.latest_oi_snapshot
    decision = st.session_state.latest_confirmed_decision
    spot = st.session_state.latest_spot
    if snapshot is None or decision is None or spot is None:
        st.info("Evidence will appear after the first valid NSE snapshot.")
        return
    snapshot = replace(snapshot, spot=float(spot))
    metrics = st.columns(5)
    metrics[0].metric("NIFTY", f"{snapshot.spot:,.2f}")
    metrics[1].metric("Support", fmt(decision.levels.support))
    metrics[2].metric("Resistance", fmt(decision.levels.resistance))
    metrics[3].metric("Setup score", f"{decision.score:.1f}/100")
    metrics[4].metric("Reward-risk", f"1:{decision.levels.reward_risk:.2f}" if decision.levels.reward_risk else "—")
    score_frame = pd.DataFrame([{"Evidence": name, "Score": value} for name, value in decision.breakdown.as_dict().items() if name != "Total"])
    oi_frame = pd.DataFrame(strike_evidence_rows(snapshot))
    score_tab, oi_tab, status_tab = st.tabs(["Score evidence", "Limited NSE OI table", "Data status"])
    with score_tab:
        st.bar_chart(score_frame.set_index("Evidence"))
        st.dataframe(score_frame, use_container_width=True, hide_index=True)
    with oi_tab:
        st.caption("Only ATM ±5 strikes and approved fields are shown. Bid/ask quantities and unrelated NSE columns are excluded.")
        st.dataframe(oi_frame, use_container_width=True, hide_index=True, height=455)
    with status_tab:
        spot_stamp = st.session_state.latest_spot_timestamp
        st.write({"OI source": snapshot.source,"OI expiry": snapshot.expiry,"OI timestamp": snapshot.timestamp.isoformat(),"OI age seconds": round(seconds_since(snapshot.timestamp, now_ist()), 1),"Latest spot timestamp": spot_stamp.isoformat() if spot_stamp else None,"Decision recalculation": "Every second","NSE OI network refresh": f"Every {st.session_state.oi_refresh_seconds} seconds","NSE spot network refresh": f"Every {st.session_state.spot_refresh_seconds} seconds","TradingView": "Chart display only; not a hidden data feed","Upstox": "Disabled"})


def live_page() -> None:
    header("Live Decision", "One unified decision engine using the latest available NSE option chain and NIFTY spot momentum.")
    controls = st.columns([1, 1, 3])
    if controls[0].button("Refresh NSE now", type="primary", use_container_width=True):
        st.session_state.force_refresh = True
    controls[1].metric("Trade threshold", f"{st.session_state.trade_threshold:.0f}")
    controls[2].caption("The screen recalculates every second. NSE public-site network requests are rate-limited; each timestamp is shown separately.")
    chart_col, decision_col = st.columns([1.75, 1], gap="large")
    with chart_col:
        st.subheader("NIFTY chart")
        tradingview_chart()
    with decision_col:
        st.subheader("Nandi decision")
        live_decision_fragment()
    st.subheader("Evidence")
    live_evidence_fragment()


def evidence_page() -> None:
    header("Evidence", "Inspect the exact limited OI rows and score components behind the current Nandi decision.")
    live_evidence_fragment()


def history_page() -> None:
    header("Decision History", "Every state change is stored with score, spot, expiry and timestamp for later review.")
    rows = history_store().recent(250)
    if not rows:
        st.info("No Nandi V2 decisions have been stored yet.")
        return
    frame = pd.DataFrame(rows)
    st.dataframe(frame, use_container_width=True, hide_index=True, height=620)
    st.download_button("Download decision history CSV", frame.to_csv(index=False).encode("utf-8"), file_name="nandi_v2_decision_history.csv", mime="text/csv")


def settings_page() -> None:
    header("Settings", "Control confirmation thresholds and review the NSE-only market-data configuration.")
    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Decision controls")
        st.session_state.trade_threshold = st.slider("Minimum score for BUY CE / BUY PE", 70.0, 90.0, float(st.session_state.trade_threshold), 1.0)
        st.session_state.email_threshold = st.slider("Minimum score for email alert", 80.0, 95.0, float(st.session_state.email_threshold), 1.0)
        st.session_state.confirmation_evaluations = st.slider("Consecutive evaluations required", 2, 10, int(st.session_state.confirmation_evaluations), 1)
        st.session_state.oi_refresh_seconds = st.slider("NSE OI network refresh seconds", 30, 300, int(st.session_state.oi_refresh_seconds), 10)
        st.session_state.spot_refresh_seconds = st.slider("NSE spot network refresh seconds", 5, 60, int(st.session_state.spot_refresh_seconds), 5)
    with right:
        st.subheader("Email alerts")
        smtp = smtp_settings()
        st.write({"Configured": smtp.configured,"SMTP host": smtp.host or "Not configured","Sender": smtp.sender or "Not configured","Recipient": smtp.recipient or "Not configured","Entry alert threshold": st.session_state.email_threshold})
        st.caption("Store SMTP credentials in Streamlit Secrets under [alerts]. Passwords are never entered into the dashboard or committed to GitHub.")
        st.code('[alerts]\nhost = "smtp.example.com"\nport = 587\nusername = "..."\npassword = "..."\nsender = "..."\nrecipient = "..."\nuse_tls = true', language="toml")
    st.divider()
    st.subheader("Data policy")
    st.markdown("- **NSE:** option-chain and NIFTY spot adapter.\n- **TradingView:** embedded NIFTY chart display only.\n- **Upstox and broker feeds:** disabled.\n- **No fallback:** missing or stale NSE data blocks a trade.\n- **Production path:** replace the public adapter with an authorised NSE Data feed without changing the engine interface.")


def sidebar() -> str:
    st.sidebar.markdown("## Nandi")
    st.sidebar.caption("Unified NIFTY decision engine")
    page = st.sidebar.radio("Navigation", ["Live Decision", "Evidence", "Decision History", "Settings"], label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.caption("NSE option chain · TradingView chart · no broker orders")
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.logged_in = False
        st.rerun()
    return page


if not st.session_state.logged_in:
    login_page()
    st.stop()

page = sidebar()
if page == "Live Decision":
    live_page()
elif page == "Evidence":
    evidence_page()
elif page == "Decision History":
    history_page()
else:
    settings_page()
