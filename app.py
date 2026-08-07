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
from nandi_v2.lifecycle import TradeState, TradeStatus, advance_trade_state
from nandi_v2.models import Decision, DecisionAction, MarketContext, OptionChainSnapshot
from nandi_v2.nse import NSEDataError, NSEPublicClient
from nandi_v2.replay import NandiReplay
from nandi_v2.session_gate import build_market_schedule, gate_live_signals

IST = ZoneInfo("Asia/Kolkata")

st.set_page_config(page_title="Nandi", page_icon="N", layout="wide", initial_sidebar_state="expanded")
st.markdown(
    """
    <style>
    :root{--g:#126b3a;--gd:#0b4e2a;--gs:#edf7f1;--ink:#17271f;--muted:#65756d;--line:#dbe8e0;--warm:#f8fbf9;--red:#9f2d2d;--amber:#8a5a05}
    .stApp{background:#fff;color:var(--ink)}.block-container{max-width:1500px;padding-top:1.4rem;padding-bottom:4rem}section[data-testid="stSidebar"]{background:var(--warm);border-right:1px solid var(--line)}
    h1,h2,h3{color:var(--ink);letter-spacing:-.025em}div[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:0 5px 16px rgba(18,107,58,.04)}
    .hero{display:flex;justify-content:space-between;gap:1rem;border:1px solid var(--line);border-radius:18px;padding:1.3rem 1.5rem;margin-bottom:1.2rem;background:linear-gradient(112deg,#fff 55%,#eef8f2 100%);position:relative;overflow:hidden}.hero:before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--g)}
    .eyebrow{color:var(--g);font-size:.7rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase}.title{color:var(--ink);font-size:1.85rem;font-weight:800;margin:.15rem 0}.copy{color:var(--muted);max-width:850px;font-size:.92rem}.badge{border:1px solid #b9ddc8;color:var(--gd);background:#fff;border-radius:999px;padding:.42rem .72rem;font-size:.7rem;font-weight:800;white-space:nowrap}
    .decision{border:1px solid var(--line);border-radius:18px;background:#fff;padding:1.1rem 1.2rem;min-height:445px}.label{color:var(--muted);font-size:.68rem;font-weight:800;letter-spacing:.11em;text-transform:uppercase}.value{font-size:2rem;font-weight:850;letter-spacing:-.04em;margin:.2rem 0}.buy{color:var(--gd)}.wait{color:var(--amber)}.no{color:var(--red)}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem;margin-top:.8rem}.cell{border:1px solid var(--line);border-radius:12px;padding:.65rem}.cell b{display:block;margin-top:.12rem}.reason{border-left:3px solid var(--g);padding:.35rem .6rem;margin:.34rem 0;background:var(--gs);border-radius:0 8px 8px 0}.blocker{border-left:3px solid var(--red);padding:.35rem .6rem;margin:.34rem 0;background:#fbf1f1;border-radius:0 8px 8px 0}.note{color:var(--muted);font-size:.76rem;line-height:1.45}.trade{border:1px solid var(--line);border-radius:14px;padding:.85rem 1rem;background:#fff;margin-top:.8rem}.stButton>button[kind="primary"]{background:var(--g);border-color:var(--g)}
    @media(max-width:800px){.badge{display:none}.grid{grid-template-columns:1fr}.value{font-size:1.65rem}}
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


def market_schedule():
    nse = secret_section("nse")
    holidays = nse.get("holidays", [])
    if isinstance(holidays, str):
        holidays = [item.strip() for item in holidays.split(",") if item.strip()]
    return build_market_schedule(holidays)


@st.cache_resource
def nse_client() -> NSEPublicClient:
    return NSEPublicClient()


@st.cache_resource
def history_store() -> DecisionHistory:
    return DecisionHistory()


def now_ist() -> datetime:
    return datetime.now(IST)


def restore_trade_state() -> TradeState:
    state = history_store().latest_trade_state()
    if state.active and state.opened_at is not None:
        opened = state.opened_at.astimezone(IST) if state.opened_at.tzinfo else state.opened_at.replace(tzinfo=IST)
        if opened.date() != now_ist().date():
            return TradeState(status=TradeStatus.FLAT, updated_at=now_ist(), reason="Previous-session trade state expired on restart.")
    return state


def trade_fingerprint(state: TradeState) -> tuple[Any, ...]:
    return (
        state.status.value, state.side, state.entry_spot, state.stop_spot, state.target_1,
        state.target_2, state.selected_strike, state.partial_booked,
    )


def init_state() -> None:
    restored = restore_trade_state()
    defaults: dict[str, Any] = {
        "logged_in": False,
        "latest_oi_snapshot": None,
        "latest_spot": None,
        "latest_spot_timestamp": None,
        "last_oi_fetch_at": None,
        "last_spot_fetch_at": None,
        "last_data_error": "",
        "spot_points": [],
        "latest_raw_decision": None,
        "latest_confirmed_decision": None,
        "candidate_side": "",
        "candidate_count": 0,
        "candidate_snapshot_timestamp": "",
        "last_history_signature": "",
        "last_email_status": "",
        "force_refresh": False,
        "trade_threshold": 75.0,
        "email_threshold": 80.0,
        "oi_refresh_seconds": 60,
        "spot_refresh_seconds": 5,
        "confirmation_evaluations": 3,
        "trade_state": restored,
        "trade_fingerprint": trade_fingerprint(restored),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_state()


def seconds_since(value: datetime | None, now: datetime) -> float:
    return float("inf") if value is None else max(0.0, (now - value).total_seconds())


def header(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="hero"><div><div class="eyebrow">Nandi V2</div><div class="title">{escape(title)}</div><div class="copy">{escape(subtitle)}</div></div><div class="badge">NSE OI · TRADINGVIEW CHART · RESEARCH ONLY</div></div>', unsafe_allow_html=True)


def login_page() -> None:
    header("Nandi", "One explainable NIFTY decision engine with no broker-order execution and no hidden data fallback.")
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        st.markdown("### Capital protection first\nNandi combines NSE option-chain evidence, NIFTY structure, premium behaviour, momentum, volume and risk-reward. Weak or conflicting setups stay **NO TRADE**.")
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
    <div class="tradingview-widget-container" style="height:640px;width:100%"><div id="tv_nandi" style="height:calc(100% - 32px);width:100%"></div><div class="tradingview-widget-copyright"><a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank"><span class="blue-text">NIFTY chart on TradingView</span></a></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({"autosize":true,"symbol":"NSE:NIFTY","interval":"5","timezone":"Asia/Kolkata","theme":"light","style":"1","locale":"en","enable_publishing":false,"allow_symbol_change":false,"hide_side_toolbar":false,"studies":["RSI@tv-basicstudies"],"container_id":"tv_nandi"});</script></div>
    """, height=660, scrolling=False)


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
    return 100.0 - 100.0 / (1.0 + gains / losses)


def market_context(now: datetime) -> MarketContext:
    prices = [float(value) for _, value in st.session_state.spot_points]
    previous = prices[-2] if len(prices) >= 2 else None
    reference = prices[-21:-1] if len(prices) >= 3 else prices[:-1]
    return MarketContext(
        observed_at=now,
        previous_spot=previous,
        recent_high=max(reference) if reference else None,
        recent_low=min(reference) if reference else None,
        momentum_rsi=momentum_rsi(st.session_state.spot_points),
    )


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
        st.session_state.candidate_snapshot_timestamp = ""
        return raw
    snapshot_key = raw.data_timestamp.isoformat() if raw.data_timestamp else ""
    if snapshot_key == st.session_state.candidate_snapshot_timestamp:
        count = st.session_state.candidate_count
    elif st.session_state.candidate_side == raw.side:
        count = st.session_state.candidate_count + 1
    else:
        count = 1
    st.session_state.candidate_side = raw.side
    st.session_state.candidate_count = count
    st.session_state.candidate_snapshot_timestamp = snapshot_key
    required = int(st.session_state.confirmation_evaluations)
    if count >= required:
        return raw
    action = DecisionAction.PREPARE_CE if raw.side == "CE" else DecisionAction.PREPARE_PE
    return replace(raw, action=action, blockers=tuple(dict.fromkeys(raw.blockers + (f"Waiting for confirmation snapshot {count}/{required}",))))


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


def persist_trade_state(previous: TradeState, current: TradeState, decision: Decision, spot: float) -> None:
    old = trade_fingerprint(previous)
    new = trade_fingerprint(current)
    st.session_state.trade_fingerprint = new
    if new != old:
        history_store().append_trade_event(current, spot=spot, decision=decision)


def build_live_decision(now: datetime) -> tuple[OptionChainSnapshot | None, Decision | None, Any]:
    gate = gate_live_signals(now, market_schedule())
    refresh_market_data(now)
    oi_snapshot = st.session_state.latest_oi_snapshot
    spot = st.session_state.latest_spot
    if oi_snapshot is None or spot is None:
        return None, None, gate
    snapshot = replace(oi_snapshot, spot=float(spot))
    context = market_context(now)
    history_store().append_market_frame(snapshot, context)
    raw = decide(
        snapshot,
        context,
        trade_threshold=float(st.session_state.trade_threshold),
        prepare_threshold=max(60.0, float(st.session_state.trade_threshold) - 10.0),
    )
    previous_trade = st.session_state.trade_state
    if gate.allowed:
        confirmed = confirm_decision(raw)
        current_trade = advance_trade_state(previous_trade, confirmed, snapshot.spot, now)
    else:
        st.session_state.candidate_side = ""
        st.session_state.candidate_count = 0
        st.session_state.candidate_snapshot_timestamp = ""
        confirmed = replace(raw, action=DecisionAction.NO_TRADE, blockers=tuple(dict.fromkeys((gate.reason,) + raw.blockers)))
        if previous_trade.active:
            current_trade = replace(previous_trade, status=TradeStatus.EXIT, updated_at=now, reason="NSE regular session is closed.")
        else:
            current_trade = previous_trade
    st.session_state.trade_state = current_trade
    persist_trade_state(previous_trade, current_trade, confirmed, snapshot.spot)
    st.session_state.latest_raw_decision = raw
    st.session_state.latest_confirmed_decision = confirmed
    record_and_alert(confirmed, snapshot)
    return snapshot, confirmed, gate


def action_class(action: DecisionAction) -> str:
    if action in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}:
        return "buy"
    if action in {DecisionAction.PREPARE_CE, DecisionAction.PREPARE_PE}:
        return "wait"
    return "no"


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def decision_card(decision: Decision, snapshot: OptionChainSnapshot, gate: Any) -> str:
    reasons = "".join(f'<div class="reason">{escape(reason)}</div>' for reason in decision.reasons)
    blockers = "".join(f'<div class="blocker">{escape(blocker)}</div>' for blocker in decision.blockers)
    levels = decision.levels
    trade: TradeState = st.session_state.trade_state
    return f'''<div class="decision"><div class="label">Final decision</div><div class="value {action_class(decision.action)}">{escape(decision.action.value)}</div><div class="note">Score {decision.score:.1f}/100 · {escape(decision.market_state)} · {escape(gate.status.label)}</div><div class="grid"><div class="cell"><span class="label">CE score</span><b>{decision.ce_score:.1f}</b></div><div class="cell"><span class="label">PE score</span><b>{decision.pe_score:.1f}</b></div><div class="cell"><span class="label">Strike</span><b>{fmt(decision.selected_strike)}</b></div><div class="cell"><span class="label">Expiry</span><b>{escape(snapshot.expiry)}</b></div><div class="cell"><span class="label">Entry</span><b>{fmt(levels.entry)}</b></div><div class="cell"><span class="label">Stop</span><b>{fmt(levels.stop)}</b></div><div class="cell"><span class="label">Target 1</span><b>{fmt(levels.target_1)}</b></div><div class="cell"><span class="label">Target 2</span><b>{fmt(levels.target_2)}</b></div></div><div style="margin-top:.8rem">{reasons}{blockers}</div><div class="trade"><span class="label">Trade lifecycle</span><b>{escape(trade.status.value)}</b><div class="note">{escape(trade.reason or 'No active trade.')}</div><div class="note">Lifecycle state is persisted across app restarts.</div></div><div class="note" style="margin-top:.7rem">NSE OI timestamp: {snapshot.timestamp.astimezone(IST).strftime('%I:%M:%S %p')} IST · TradingView is chart display only.</div></div>'''


@st.fragment(run_every="1s")
def live_decision_fragment() -> None:
    snapshot, decision, gate = build_live_decision(now_ist())
    if snapshot is None or decision is None:
        st.error("Live NSE data are not available yet.")
        if st.session_state.last_data_error:
            st.caption(st.session_state.last_data_error)
        return
    st.markdown(decision_card(decision, snapshot, gate), unsafe_allow_html=True)
    if st.session_state.last_data_error:
        st.warning("Latest network refresh failed; Nandi is using the last valid NSE snapshot. " + st.session_state.last_data_error)
    if st.session_state.last_email_status:
        st.caption(st.session_state.last_email_status)


@st.fragment(run_every="2s")
def evidence_fragment() -> None:
    snapshot = st.session_state.latest_oi_snapshot
    decision = st.session_state.latest_confirmed_decision
    spot = st.session_state.latest_spot
    if snapshot is None or decision is None or spot is None:
        st.info("Evidence appears after the first valid NSE snapshot.")
        return
    snapshot = replace(snapshot, spot=float(spot))
    cols = st.columns(5)
    cols[0].metric("NIFTY", f"{snapshot.spot:,.2f}")
    cols[1].metric("Support", fmt(decision.levels.support))
    cols[2].metric("Resistance", fmt(decision.levels.resistance))
    cols[3].metric("Setup score", f"{decision.score:.1f}/100")
    cols[4].metric("Reward-risk", f"1:{decision.levels.reward_risk:.2f}" if decision.levels.reward_risk else "—")
    scores = pd.DataFrame([{"Evidence": name, "Score": value} for name, value in decision.breakdown.as_dict().items() if name != "Total"])
    oi = pd.DataFrame(strike_evidence_rows(snapshot))
    score_tab, oi_tab, status_tab = st.tabs(["Score evidence", "Limited NSE OI table", "Data status"])
    with score_tab:
        st.bar_chart(scores.set_index("Evidence"))
        st.dataframe(scores, use_container_width=True, hide_index=True)
    with oi_tab:
        st.caption("ATM ±5 strikes only. The table intentionally excludes unrelated NSE columns.")
        st.dataframe(oi, use_container_width=True, hide_index=True, height=455)
    with status_tab:
        spot_stamp = st.session_state.latest_spot_timestamp
        gate = gate_live_signals(now_ist(), market_schedule())
        st.write({
            "NSE session": gate.status.label,
            "Signal gate": "Allowed" if gate.allowed else "Blocked",
            "OI source": snapshot.source,
            "OI timestamp": snapshot.timestamp.isoformat(),
            "OI age seconds": round(seconds_since(snapshot.timestamp, now_ist()), 1),
            "Latest spot timestamp": spot_stamp.isoformat() if spot_stamp else None,
            "Decision recalculation": "Every second",
            "NSE OI network refresh": f"Every {st.session_state.oi_refresh_seconds} seconds",
            "NSE spot network refresh": f"Every {st.session_state.spot_refresh_seconds} seconds",
            "TradingView": "Chart display only",
            "Upstox": "Disabled",
        })


def live_page() -> None:
    header("Live Decision", "Unified NIFTY decision engine: NSE option-chain evidence with an explicit trade lifecycle and session safety gate.")
    controls = st.columns([1, 1, 3])
    if controls[0].button("Refresh NSE now", type="primary", use_container_width=True):
        st.session_state.force_refresh = True
    controls[1].metric("Trade threshold", f"{st.session_state.trade_threshold:.0f}")
    controls[2].caption("The UI recalculates continuously; network calls are rate-limited and each NSE timestamp is displayed separately.")
    chart, panel = st.columns([1.7, 1], gap="large")
    with chart:
        st.subheader("NIFTY chart")
        tradingview_chart()
    with panel:
        st.subheader("Nandi decision")
        live_decision_fragment()
    st.subheader("Evidence")
    evidence_fragment()


def history_page() -> None:
    header("History", "Persistent decisions and trade-lifecycle transitions for audit and review.")
    decision_rows = history_store().recent(500)
    trade_rows = history_store().recent_trade_events(500)
    decisions_tab, trades_tab = st.tabs(["Decisions", "Trade lifecycle"])
    with decisions_tab:
        if not decision_rows:
            st.info("No V2 decisions stored yet.")
        else:
            frame = pd.DataFrame(decision_rows)
            st.dataframe(frame, use_container_width=True, hide_index=True, height=570)
            st.download_button("Download decision history CSV", frame.to_csv(index=False).encode("utf-8"), file_name="nandi_v2_decision_history.csv", mime="text/csv")
    with trades_tab:
        if not trade_rows:
            st.info("No trade lifecycle transitions stored yet.")
        else:
            frame = pd.DataFrame(trade_rows)
            st.dataframe(frame, use_container_width=True, hide_index=True, height=570)
            st.download_button("Download trade lifecycle CSV", frame.to_csv(index=False).encode("utf-8"), file_name="nandi_v2_trade_lifecycle.csv", mime="text/csv")


def replay_page() -> None:
    header("Replay", "Re-run the unified V2 engine on NSE snapshots that Nandi has already captured and stored.")
    days = history_store().replay_days()
    if not days:
        st.info("Replay data will appear after Nandi captures live NSE option-chain frames. No historical data are invented or substituted.")
        return
    selected_day = st.selectbox("Trading day", days)
    snapshots, contexts = history_store().replay_data(selected_day)
    if len(snapshots) < 2:
        st.warning("At least two stored frames are required for replay.")
        return
    result = NandiReplay(
        trade_threshold=float(st.session_state.trade_threshold),
        prepare_threshold=max(60.0, float(st.session_state.trade_threshold) - 10.0),
    ).run(snapshots, contexts)
    metrics = st.columns(5)
    metrics[0].metric("Frames", len(result.frames))
    metrics[1].metric("Entries", result.entries)
    metrics[2].metric("CE entries", result.ce_entries)
    metrics[3].metric("PE entries", result.pe_entries)
    metrics[4].metric("Exits", result.exits)
    rows = [
        {
            "Time": frame.snapshot.timestamp.isoformat(),
            "Spot": frame.snapshot.spot,
            "Decision": frame.decision.action.value,
            "Score": frame.decision.score,
            "CE": frame.decision.ce_score,
            "PE": frame.decision.pe_score,
            "Trade status": frame.trade_state.status.value,
            "Reason": frame.trade_state.reason,
        }
        for frame in result.frames
    ]
    replay_frame = pd.DataFrame(rows)
    replay_frame["Time"] = pd.to_datetime(replay_frame["Time"])
    st.line_chart(replay_frame.set_index("Time")[["Spot"]])
    st.line_chart(replay_frame.set_index("Time")[["CE", "PE", "Score"]])
    st.dataframe(replay_frame, use_container_width=True, hide_index=True, height=520)
    st.download_button("Download replay CSV", replay_frame.to_csv(index=False).encode("utf-8"), file_name=f"nandi_v2_replay_{selected_day}.csv", mime="text/csv")
    st.caption("Replay is deterministic research evidence. It does not place orders and does not claim the setup score is a probability of profit.")


def settings_page() -> None:
    header("Settings", "Decision thresholds, NSE refresh policy and email-alert configuration.")
    left, right = st.columns(2, gap="large")
    with left:
        st.session_state.trade_threshold = st.slider("Minimum BUY score", 70.0, 90.0, float(st.session_state.trade_threshold), 1.0)
        st.session_state.email_threshold = st.slider("Minimum email score", 80.0, 95.0, float(st.session_state.email_threshold), 1.0)
        st.session_state.confirmation_evaluations = st.slider("Fresh NSE snapshots required", 2, 6, int(st.session_state.confirmation_evaluations), 1)
        st.session_state.oi_refresh_seconds = st.slider("NSE OI refresh seconds", 30, 300, int(st.session_state.oi_refresh_seconds), 10)
        st.session_state.spot_refresh_seconds = st.slider("NSE spot refresh seconds", 5, 60, int(st.session_state.spot_refresh_seconds), 5)
    with right:
        smtp = smtp_settings()
        st.subheader("Email alerts")
        st.write({"Configured": smtp.configured, "SMTP host": smtp.host or "Not configured", "Sender": smtp.sender or "Not configured", "Recipient": smtp.recipient or "Not configured"})
        st.caption("Credentials belong in Streamlit Secrets under [alerts]; never commit passwords or tokens.")
        st.code('[alerts]\nhost="smtp.example.com"\nport=587\nusername="..."\npassword="..."\nsender="..."\nrecipient="..."\nuse_tls=true', language="toml")
    st.divider()
    st.markdown("**Data policy:** NSE adapter for market data; TradingView for chart display only; Upstox/broker data disabled; stale/missing data and closed NSE sessions cannot produce a live BUY signal.")


def sidebar() -> str:
    st.sidebar.markdown("## Nandi")
    st.sidebar.caption("Unified NIFTY decision engine")
    page = st.sidebar.radio("Navigation", ["Live Decision", "Evidence", "History", "Replay", "Settings"], label_visibility="collapsed")
    st.sidebar.divider()
    gate = gate_live_signals(now_ist(), market_schedule())
    st.sidebar.caption(f"NSE: {gate.status.label}")
    st.sidebar.caption("NSE OI · TradingView chart · no broker orders")
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
    header("Evidence", "Current score components, limited OI table and source-freshness status.")
    evidence_fragment()
elif page == "History":
    history_page()
elif page == "Replay":
    replay_page()
else:
    settings_page()
