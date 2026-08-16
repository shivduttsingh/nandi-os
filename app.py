from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.auth import CredentialConfigurationError, LoginLockout
from nandi_oi.configuration import is_configured_value
from nandi_v2.charting import candlestick_chart_html, completed_candles, merge_candles
from nandi_v2.confluence import ConfluenceDecision, apply_confluence_gate, combine_decision
from nandi_v2.email_alerts import SMTPEmailAlertSink, SMTPSettings, is_entry_alert
from nandi_v2.engine import decide, strike_evidence_rows
from nandi_v2.fundamentals import (
    FUNDAMENTAL_CATALOGUE,
    FundamentalAssessment,
    FundamentalBias,
    FundamentalFactor,
    assess_fundamentals,
    fundamental_rows,
)
from nandi_v2.history import DecisionHistory
from nandi_v2.lifecycle import TradeState, TradeStatus, advance_trade_state
from nandi_v2.models import Decision, DecisionAction, MarketContext, OptionChainSnapshot
from nandi_v2.nse import NSEDataError, NSEPublicClient
from nandi_v2.replay import NandiReplay
from nandi_v2.results import completed_trades, result_rows, trade_rows
from nandi_v2.session_gate import build_market_schedule, gate_live_signals
from nandi_v2.technical import (
    NANDI_TOP_10_INDICATORS,
    TechnicalAssessment,
    TechnicalDirection,
    assess_technicals,
    technical_rows,
)

IST = ZoneInfo("Asia/Kolkata")
TRADINGVIEW_NIFTY_URL = "https://www.tradingview.com/symbols/NSE-NIFTY/"

st.set_page_config(page_title="Nandi", page_icon="N", layout="wide", initial_sidebar_state="expanded")
st.markdown(
    """
    <style>
    :root{--g:#126b3a;--gd:#0b4e2a;--gs:#edf7f1;--ink:#17271f;--muted:#65756d;--line:#dbe8e0;--warm:#f8fbf9;--red:#9f2d2d;--amber:#8a5a05}
    .stApp{background:#fff;color:var(--ink)}.block-container{max-width:1550px;padding-top:1.25rem;padding-bottom:4rem}section[data-testid="stSidebar"]{background:var(--warm);border-right:1px solid var(--line)}
    h1,h2,h3{color:var(--ink);letter-spacing:-.025em}div[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px}
    .hero{display:flex;justify-content:space-between;gap:1rem;border:1px solid var(--line);border-radius:18px;padding:1.2rem 1.45rem;margin-bottom:1.1rem;background:linear-gradient(112deg,#fff 55%,#eef8f2 100%);position:relative;overflow:hidden}.hero:before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--g)}
    .eyebrow{color:var(--g);font-size:.7rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase}.title{font-size:1.85rem;font-weight:800;margin:.12rem 0}.copy{color:var(--muted);max-width:880px;font-size:.92rem}.badge{border:1px solid #b9ddc8;color:var(--gd);background:#fff;border-radius:999px;padding:.42rem .72rem;font-size:.7rem;font-weight:800;white-space:nowrap}
    .decision{border:1px solid var(--line);border-radius:18px;background:#fff;padding:1.05rem 1.15rem;min-height:410px}.label{color:var(--muted);font-size:.68rem;font-weight:800;letter-spacing:.11em;text-transform:uppercase}.value{font-size:2rem;font-weight:850;letter-spacing:-.04em;margin:.2rem 0}.buy{color:var(--gd)}.wait{color:var(--amber)}.no{color:var(--red)}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.58rem;margin-top:.8rem}.cell{border:1px solid var(--line);border-radius:11px;padding:.62rem}.cell b{display:block;margin-top:.12rem}.reason{border-left:3px solid var(--g);padding:.32rem .58rem;margin:.32rem 0;background:var(--gs);border-radius:0 8px 8px 0}.blocker{border-left:3px solid var(--red);padding:.32rem .58rem;margin:.32rem 0;background:#fbf1f1;border-radius:0 8px 8px 0}.note{color:var(--muted);font-size:.76rem;line-height:1.45}.trade{border:1px solid var(--line);border-radius:12px;padding:.75rem .85rem;background:#fff;margin-top:.75rem}
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


@st.cache_resource
def nse_client() -> NSEPublicClient:
    return NSEPublicClient()


@st.cache_resource
def history_store() -> DecisionHistory:
    return DecisionHistory()


@st.cache_resource
def upstox_candle_client(access_token: str) -> UpstoxOptionChainClient:
    return UpstoxOptionChainClient(access_token=access_token, timeout_seconds=15)


def now_ist() -> datetime:
    return datetime.now(IST)


def configured_upstox_token() -> str:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not is_configured_value(token):
        token = str(secret_section("upstox").get("access_token", ""))
    return token if is_configured_value(token) else ""


def market_schedule():
    nse = secret_section("nse")
    holidays = nse.get("holidays", [])
    if isinstance(holidays, str):
        holidays = [item.strip() for item in holidays.split(",") if item.strip()]
    return build_market_schedule(holidays)


def restore_trade_state() -> TradeState:
    state = history_store().latest_trade_state()
    if state.active and state.opened_at is not None:
        opened = state.opened_at.astimezone(IST) if state.opened_at.tzinfo else state.opened_at.replace(tzinfo=IST)
        if opened.date() != now_ist().date():
            return TradeState(status=TradeStatus.FLAT, updated_at=now_ist(), reason="Previous-session trade state expired on restart.")
    return state


def trade_fingerprint(state: TradeState) -> tuple[Any, ...]:
    return (state.status.value, state.side, state.entry_spot, state.stop_spot, state.target_1, state.target_2, state.selected_strike, state.partial_booked)


def init_state() -> None:
    restored = restore_trade_state()
    defaults: dict[str, Any] = {
        "logged_in": False,
        "latest_oi_snapshot": None,
        "latest_spot": None,
        "latest_spot_timestamp": None,
        "last_oi_fetch_at": None,
        "last_spot_fetch_at": None,
        "last_upstox_candle_fetch_at": None,
        "last_technical_history_fetch_at": None,
        "last_data_error": "",
        "upstox_candle_error": "",
        "technical_history_error": "",
        "spot_points": [],
        "latest_upstox_candles": tuple(),
        "technical_history_candles": tuple(),
        "technical_history_for_date": "",
        "latest_microstructure_decision": None,
        "latest_confirmed_decision": None,
        "latest_technical_assessment": None,
        "latest_fundamental_assessment": None,
        "latest_confluence_decision": None,
        "candidate_side": "",
        "candidate_count": 0,
        "candidate_snapshot_timestamp": "",
        "last_history_signature": "",
        "last_email_status": "",
        "force_refresh": False,
        "trade_threshold": 75.0,
        "email_threshold": 80.0,
        "oi_refresh_seconds": 30,
        "spot_refresh_seconds": 3,
        "confirmation_evaluations": 3,
        "stable_interval_minutes": 15,
        "candle_refresh_seconds": 30,
        "technical_history_days": 10,
        "technical_history_retry_seconds": 300,
        "minimum_hold_minutes": 15,
        "reversal_cooldown_minutes": 5,
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
    st.markdown(f'<div class="hero"><div><div class="eyebrow">Nandi Live</div><div class="title">{escape(title)}</div><div class="copy">{escape(subtitle)}</div></div><div class="badge">NSE LIVE INPUT · RESEARCH TERMINAL</div></div>', unsafe_allow_html=True)


def login_page() -> None:
    header("Nandi", "Private NIFTY decision terminal using live NSE inputs. Missing data never becomes a guessed trade.")
    _, middle, _ = st.columns([1, 1.1, 1])
    with middle:
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
                        st.error("Too many failed attempts. Please retry later.")
                    else:
                        st.error(f"Invalid credentials. {result.attempts_remaining} attempt(s) remaining.")
            if AUTH_ERROR:
                st.warning(AUTH_ERROR)


def append_spot(spot: float, timestamp: datetime) -> None:
    points = list(st.session_state.spot_points)
    item = (timestamp.isoformat(), float(spot))
    if not points or points[-1] != item:
        points.append(item)
    st.session_state.spot_points = points[-600:]


def momentum_rsi_prices(prices: list[float], period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    changes = [b - a for a, b in zip(prices, prices[1:])][-period:]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def momentum_rsi(points: list[tuple[str, float]], period: int = 14) -> float | None:
    return momentum_rsi_prices([float(value) for _, value in points], period)


def market_context(now: datetime) -> MarketContext:
    completed = completed_candles(
        st.session_state.latest_upstox_candles,
        now,
        int(st.session_state.stable_interval_minutes),
    )
    if completed:
        reference = completed[-8:]
        return MarketContext(
            observed_at=now,
            previous_spot=completed[-1].close,
            recent_high=max(item.high for item in reference),
            recent_low=min(item.low for item in reference),
            momentum_rsi=momentum_rsi_prices([item.close for item in completed]),
        )
    prices = [float(value) for _, value in st.session_state.spot_points]
    previous = prices[-2] if len(prices) >= 2 else None
    reference = prices[-41:-1] if len(prices) >= 3 else prices[:-1]
    return MarketContext(observed_at=now, previous_spot=previous, recent_high=max(reference) if reference else None, recent_low=min(reference) if reference else None, momentum_rsi=momentum_rsi(st.session_state.spot_points))


def completed_structure_candles(now: datetime) -> tuple[Any, ...]:
    return completed_candles(
        st.session_state.latest_upstox_candles,
        now,
        int(st.session_state.stable_interval_minutes),
    )


def technical_candles() -> tuple[Any, ...]:
    return merge_candles(
        st.session_state.technical_history_candles,
        st.session_state.latest_upstox_candles,
    )


def completed_technical_candles(now: datetime) -> tuple[Any, ...]:
    return completed_candles(
        technical_candles(),
        now,
        int(st.session_state.stable_interval_minutes),
    )


def pillar_assessments(now: datetime) -> tuple[TechnicalAssessment, FundamentalAssessment]:
    technical = assess_technicals(completed_technical_candles(now))
    fundamental = assess_fundamentals(history_store().latest_fundamental_factors(), now)
    st.session_state.latest_technical_assessment = technical
    st.session_state.latest_fundamental_assessment = fundamental
    return technical, fundamental


def refresh_market_data(now: datetime) -> None:
    force = st.session_state.force_refresh
    fetch_oi = force or st.session_state.latest_oi_snapshot is None or seconds_since(st.session_state.last_oi_fetch_at, now) >= st.session_state.oi_refresh_seconds
    fetch_spot = force or st.session_state.latest_spot is None or seconds_since(st.session_state.last_spot_fetch_at, now) >= st.session_state.spot_refresh_seconds
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
            errors.append(f"OI: {exc}")
        finally:
            st.session_state.last_oi_fetch_at = now
    if fetch_spot:
        try:
            spot, stamp = nse_client().fetch_nifty_spot()
            st.session_state.latest_spot = spot
            st.session_state.latest_spot_timestamp = stamp
            append_spot(spot, stamp)
        except NSEDataError as exc:
            errors.append(f"Spot: {exc}")
        finally:
            st.session_state.last_spot_fetch_at = now
    token = configured_upstox_token()
    fetch_candles = bool(token) and (
        force
        or not st.session_state.latest_upstox_candles
        or seconds_since(st.session_state.last_upstox_candle_fetch_at, now)
        >= st.session_state.candle_refresh_seconds
    )
    if fetch_candles:
        try:
            st.session_state.latest_upstox_candles = upstox_candle_client(token).fetch_intraday_candles(
                int(st.session_state.stable_interval_minutes)
            )
            st.session_state.upstox_candle_error = ""
        except (UpstoxAPIError, ValueError) as exc:
            st.session_state.upstox_candle_error = str(exc)
        finally:
            st.session_state.last_upstox_candle_fetch_at = now
    today_key = now.date().isoformat()
    history_is_current = (
        st.session_state.technical_history_for_date == today_key
        and bool(st.session_state.technical_history_candles)
    )
    fetch_history = bool(token) and (
        force
        or (
            not history_is_current
            and seconds_since(st.session_state.last_technical_history_fetch_at, now)
            >= st.session_state.technical_history_retry_seconds
        )
    )
    if fetch_history:
        try:
            to_date = now.date() - timedelta(days=1)
            from_date = to_date - timedelta(
                days=int(st.session_state.technical_history_days) - 1
            )
            st.session_state.technical_history_candles = upstox_candle_client(
                token
            ).fetch_historical_candles(
                from_date,
                to_date,
                int(st.session_state.stable_interval_minutes),
            )
            st.session_state.technical_history_for_date = today_key
            st.session_state.technical_history_error = ""
        except (UpstoxAPIError, ValueError) as exc:
            st.session_state.technical_history_error = str(exc)
        finally:
            st.session_state.last_technical_history_fetch_at = now
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
    return replace(raw, action=action, blockers=tuple(dict.fromkeys(raw.blockers + (f"Waiting for fresh NSE confirmation {count}/{required}",))))


def smtp_settings() -> SMTPSettings:
    return SMTPSettings.from_mapping(secret_section("alerts"))


def record_and_alert(
    decision: Decision, snapshot: OptionChainSnapshot, *, allow_entry_alert: bool,
) -> None:
    signature = f"{decision.action.value}:{round(decision.score,1)}:{round(snapshot.spot,1)}:{snapshot.timestamp.isoformat()}"
    if signature != st.session_state.last_history_signature:
        signal_key = history_store().append(decision, snapshot.spot, snapshot.expiry)
        st.session_state.last_history_signature = signature
    else:
        signal_key = history_store().signal_key(decision, snapshot.spot, snapshot.expiry)
    if (
        not allow_entry_alert
        or not is_entry_alert(decision, float(st.session_state.email_threshold))
        or history_store().alert_exists(signal_key)
    ):
        return
    delivery = SMTPEmailAlertSink(smtp_settings()).send_decision(decision, snapshot.spot, snapshot.expiry)
    history_store().record_alert(signal_key, delivery.delivered, delivery.error)
    st.session_state.last_email_status = "Email alert delivered" if delivery.delivered else delivery.error


def persist_trade(previous: TradeState, current: TradeState, decision: Decision, spot: float) -> None:
    if trade_fingerprint(previous) != trade_fingerprint(current):
        history_store().append_trade_event(current, spot=spot, decision=decision)
    st.session_state.trade_fingerprint = trade_fingerprint(current)


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
    raw = decide(snapshot, context, trade_threshold=float(st.session_state.trade_threshold), prepare_threshold=max(60.0, float(st.session_state.trade_threshold) - 10.0))
    technical, fundamental = pillar_assessments(now)
    confluence = combine_decision(raw, technical, fundamental)
    combined = apply_confluence_gate(raw, confluence)
    st.session_state.latest_microstructure_decision = raw
    st.session_state.latest_confluence_decision = confluence
    previous = st.session_state.trade_state
    if gate.allowed:
        confirmed = confirm_decision(combined)
        current = advance_trade_state(
            previous,
            confirmed,
            snapshot.spot,
            now,
            minimum_hold_minutes=float(st.session_state.minimum_hold_minutes),
            reversal_cooldown_minutes=float(st.session_state.reversal_cooldown_minutes),
        )
    else:
        confirmed = replace(combined, action=DecisionAction.NO_TRADE, blockers=tuple(dict.fromkeys((gate.reason,) + combined.blockers)))
        current = replace(previous, status=TradeStatus.EXIT, updated_at=now, reason="NSE regular session is closed.") if previous.active else previous
    st.session_state.trade_state = current
    persist_trade(previous, current, confirmed, snapshot.spot)
    st.session_state.latest_confirmed_decision = confirmed
    record_and_alert(
        confirmed,
        snapshot,
        allow_entry_alert=not previous.active and current.active,
    )
    return snapshot, confirmed, gate


def action_class(action: DecisionAction) -> str:
    if action in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}:
        return "buy"
    if action in {DecisionAction.PREPARE_CE, DecisionAction.PREPARE_PE}:
        return "wait"
    return "no"


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def lifecycle_action(decision: Decision, trade: TradeState) -> tuple[str, str]:
    if trade.active:
        return f"{trade.side} ACTIVE — {trade.status.value}", "buy"
    if trade.status == TradeStatus.EXIT and trade.side in {"CE", "PE"}:
        return f"EXIT {trade.side} — COOLDOWN", "no"
    return decision.action.value, action_class(decision.action)


def decision_card(decision: Decision, snapshot: OptionChainSnapshot, gate: Any) -> str:
    reasons = "".join(f'<div class="reason">{escape(reason)}</div>' for reason in decision.reasons[:5])
    blockers = "".join(f'<div class="blocker">{escape(blocker)}</div>' for blocker in decision.blockers[:5])
    trade: TradeState = st.session_state.trade_state
    confluence: ConfluenceDecision | None = st.session_state.latest_confluence_decision
    displayed_action, displayed_class = lifecycle_action(decision, trade)
    levels = decision.levels
    agreement = confluence.agreement if confluence else "Waiting for pillar assessment"
    score_line = (
        f"Confluence {confluence.setup_score:.1f}/100 · OI {confluence.microstructure_score:.1f} · "
        f"Technical {confluence.technical_score:.1f} · Fundamental {confluence.fundamental_score:.1f}"
        if confluence else f"Setup score {decision.score:.1f}/100"
    )
    return f'''<div class="decision"><div class="label">Stable trade state</div><div class="value {displayed_class}">{escape(displayed_action)}</div><div class="note">{escape(score_line)} · {escape(gate.status.label)}</div><div class="note">{escape(agreement)}</div><div class="grid"><div class="cell"><span class="label">Strike</span><b>{fmt(decision.selected_strike)}</b></div><div class="cell"><span class="label">Expiry</span><b>{escape(snapshot.expiry)}</b></div><div class="cell"><span class="label">Entry</span><b>{fmt(levels.entry)}</b></div><div class="cell"><span class="label">Stop</span><b>{fmt(levels.stop)}</b></div><div class="cell"><span class="label">Target 1</span><b>{fmt(levels.target_1)}</b></div><div class="cell"><span class="label">Target 2</span><b>{fmt(levels.target_2)}</b></div></div><div style="margin-top:.75rem">{reasons}{blockers}</div><div class="trade"><span class="label">Lifecycle</span><b>{escape(trade.status.value)}</b><div class="note">{escape(trade.reason or 'No active trade.')}</div></div><div class="note" style="margin-top:.65rem">OI: {snapshot.timestamp.astimezone(IST).strftime('%H:%M:%S')} IST · {escape(snapshot.source)}</div></div>'''


@st.fragment(run_every="1s")
def live_engine_fragment() -> None:
    snapshot, decision, gate = build_live_decision(now_ist())
    if snapshot is None or decision is None:
        st.error("Waiting for a valid NSE option-chain snapshot.")
        if st.session_state.last_data_error:
            st.code(st.session_state.last_data_error)
        return
    st.markdown(decision_card(decision, snapshot, gate), unsafe_allow_html=True)
    if st.session_state.last_data_error:
        st.warning("A refresh failed. Nandi is retaining the last valid NSE snapshot: " + st.session_state.last_data_error)


@st.fragment(run_every="2s")
def live_chart_fragment() -> None:
    candles = tuple(st.session_state.latest_upstox_candles)
    points = list(st.session_state.spot_points)
    spot = st.session_state.latest_spot
    if candles:
        interval = int(st.session_state.stable_interval_minutes)
        completed = completed_candles(candles, now_ist(), interval)
        structure = "WAIT / RANGE"
        if len(completed) >= 2:
            previous, latest = completed[-2], completed[-1]
            if latest.close > previous.high:
                structure = "BULLISH BREAKOUT"
            elif latest.close < previous.low:
                structure = "BEARISH BREAKDOWN"
        cols = st.columns(4)
        live_value = float(spot if spot is not None else candles[-1].close)
        cols[0].metric("NIFTY live", f"{live_value:,.2f}")
        cols[1].metric("Stable timeframe", f"{interval} min")
        cols[2].metric("Completed candles", len(completed))
        cols[3].metric("Closed-candle structure", structure)
        components.html(
            candlestick_chart_html(candles, interval_minutes=interval),
            height=625,
            scrolling=False,
        )
        last_completed = completed[-1].timestamp.strftime("%I:%M %p") if completed else "Waiting"
        st.caption(
            f"Upstox V3 OHLC · last completed candle {last_completed} IST · "
            "only completed candles enter Nandi's market-structure context."
        )
        if st.session_state.upstox_candle_error:
            st.warning("Latest Upstox candle refresh failed; the last valid chart remains visible. " + st.session_state.upstox_candle_error)
        st.link_button("Open full TradingView", TRADINGVIEW_NIFTY_URL)
        return
    if spot is not None:
        cols = st.columns([1, 1, 2])
        cols[0].metric("NIFTY live", f"{spot:,.2f}")
        stamp = st.session_state.latest_spot_timestamp
        cols[1].metric("Samples", len(points))
        cols[2].caption(f"Last NSE spot update: {stamp.astimezone(IST).strftime('%H:%M:%S')} IST" if stamp else "Waiting for timestamp")
    if len(points) >= 2:
        frame = pd.DataFrame(points, columns=["Time", "NIFTY"])
        frame["Time"] = pd.to_datetime(frame["Time"])
        st.line_chart(frame.set_index("Time"), height=430)
    else:
        st.info("The fallback NSE chart starts drawing as spot samples arrive.")
    if configured_upstox_token():
        st.caption("Waiting for the Upstox 15-minute candle feed. Nandi is temporarily showing NSE spot samples.")
    else:
        st.caption("Add the read-only Upstox token to show stable 15-minute candles. Nandi is using its NSE spot fallback.")
    if st.session_state.upstox_candle_error:
        st.warning(st.session_state.upstox_candle_error)
    st.link_button("Open NIFTY on TradingView", TRADINGVIEW_NIFTY_URL)


@st.fragment(run_every="2s")
def evidence_fragment() -> None:
    snapshot = st.session_state.latest_oi_snapshot
    decision = st.session_state.latest_confirmed_decision
    if snapshot is None:
        st.info("Waiting for NSE option-chain-v3 data.")
        if st.session_state.last_data_error:
            st.code(st.session_state.last_data_error)
        return
    spot = float(st.session_state.latest_spot or snapshot.spot)
    snapshot = replace(snapshot, spot=spot)
    metrics = st.columns(6)
    metrics[0].metric("NIFTY", f"{spot:,.2f}")
    metrics[1].metric("Expiry", snapshot.expiry)
    metrics[2].metric("ATM window", "±5")
    metrics[3].metric("OI rows", len(strike_evidence_rows(snapshot)))
    metrics[4].metric("CE score", f"{decision.ce_score:.1f}" if decision else "—")
    metrics[5].metric("PE score", f"{decision.pe_score:.1f}" if decision else "—")
    oi = pd.DataFrame(strike_evidence_rows(snapshot))
    tabs = st.tabs(["Live OI ±5", "Score evidence", "Data health"])
    with tabs[0]:
        st.dataframe(oi, use_container_width=True, hide_index=True, height=455)
    with tabs[1]:
        if decision is None:
            st.info("Decision score will appear after the engine evaluates the snapshot.")
        else:
            scores = pd.DataFrame([{"Evidence": name, "Score": value} for name, value in decision.breakdown.as_dict().items() if name != "Total"])
            st.bar_chart(scores.set_index("Evidence"))
            st.dataframe(scores, use_container_width=True, hide_index=True)
    with tabs[2]:
        gate = gate_live_signals(now_ist(), market_schedule())
        st.write({
            "NSE session": gate.status.label,
            "Signal gate": "Allowed" if gate.allowed else "Blocked",
            "Option-chain source": snapshot.source,
            "Option-chain timestamp": snapshot.timestamp.isoformat(),
            "Option-chain age seconds": round(seconds_since(snapshot.timestamp, now_ist()), 1),
            "OI network refresh": f"{st.session_state.oi_refresh_seconds}s",
            "Spot network refresh": f"{st.session_state.spot_refresh_seconds}s",
            "Upstox candle refresh": f"{st.session_state.candle_refresh_seconds}s" if configured_upstox_token() else "Not configured",
            "Technical candle source": "Upstox V3 historical + intraday" if configured_upstox_token() else "Not configured",
            "Technical completed candles": len(completed_technical_candles(now_ist())),
            "Technical history window": f"{st.session_state.technical_history_days} calendar days",
            "Technical history error": st.session_state.technical_history_error or "None",
            "Stable structure interval": f"{st.session_state.stable_interval_minutes} minutes",
            "Decision recalculation": "1s",
            "TradingView rendering": "Lightweight Charts with Upstox OHLC; hosted NSE widget not used",
        })


@st.fragment(run_every="2s")
def pillar_summary_fragment() -> None:
    technical: TechnicalAssessment | None = st.session_state.latest_technical_assessment
    fundamental: FundamentalAssessment | None = st.session_state.latest_fundamental_assessment
    micro: Decision | None = st.session_state.latest_microstructure_decision
    confluence: ConfluenceDecision | None = st.session_state.latest_confluence_decision
    columns = st.columns(4)
    columns[0].metric(
        "Fundamental",
        fundamental.direction.value if fundamental else "WAITING",
        f"{fundamental.coverage:.0f}% fresh coverage" if fundamental else None,
    )
    columns[1].metric(
        "Technical",
        technical.direction.value if technical else "WAITING",
        f"{technical.coverage:.0f}% indicator coverage" if technical else None,
    )
    columns[2].metric(
        "OI & execution",
        micro.action.value if micro else "WAITING",
        f"{micro.score:.1f}/100" if micro else None,
    )
    columns[3].metric(
        "Unified gate",
        confluence.action.value if confluence else "WAITING",
        f"{confluence.setup_score:.1f}/100" if confluence else None,
    )
    if confluence:
        if confluence.blockers:
            st.warning("Unified gate: " + " | ".join(confluence.blockers[:3]))
        else:
            st.success(confluence.agreement)


def fundamental_desk_page() -> None:
    now = now_ist()
    current = {factor.key: factor for factor in history_store().latest_fundamental_factors()}
    assessment = assess_fundamentals(tuple(current.values()), now)
    header(
        "Fundamental Desk",
        "Global, macro, flows, heavyweight earnings and event-risk inputs. Every factor must show its source and freshness.",
    )
    metrics = st.columns(4)
    metrics[0].metric("Bias", assessment.direction.value)
    metrics[1].metric("Fresh coverage", f"{assessment.coverage:.0f}%")
    metrics[2].metric("Bullish weight", f"{assessment.bullish_score:.1f}")
    metrics[3].metric("Bearish weight", f"{assessment.bearish_score:.1f}")
    st.dataframe(pd.DataFrame(fundamental_rows(assessment, now)), use_container_width=True, hide_index=True)
    if assessment.blockers:
        st.warning(" | ".join(assessment.blockers))

    st.subheader("Record a sourced market view")
    st.caption(
        "This first version accepts an authenticated research snapshot. It does not invent news or silently scrape an unlicensed feed. "
        "Automated authorised providers can write to the same factor contract later."
    )
    with st.form("fundamental_snapshot_form"):
        entries: list[tuple[Any, str, float, float, str, str]] = []
        for definition in FUNDAMENTAL_CATALOGUE:
            existing = current.get(definition.key)
            with st.expander(f"{definition.category} · {definition.name}", expanded=False):
                st.caption(definition.description)
                left, middle, right = st.columns([1, 1, 1])
                options = [item.value for item in FundamentalBias]
                selected = existing.bias.value if existing else FundamentalBias.UNKNOWN.value
                bias = left.selectbox(
                    "Bias",
                    options,
                    index=options.index(selected),
                    key=f"fundamental_bias_{definition.key}",
                )
                impact = middle.slider(
                    "Impact",
                    0,
                    100,
                    int(existing.impact) if existing else 50,
                    5,
                    key=f"fundamental_impact_{definition.key}",
                )
                confidence = right.slider(
                    "Evidence confidence",
                    0,
                    100,
                    int(existing.confidence * 100) if existing else 60,
                    5,
                    key=f"fundamental_confidence_{definition.key}",
                )
                source = st.text_input(
                    "Source",
                    value=existing.source if existing else "Manual research input",
                    key=f"fundamental_source_{definition.key}",
                )
                note = st.text_input(
                    "Evidence note",
                    value=existing.note if existing else "",
                    key=f"fundamental_note_{definition.key}",
                )
                entries.append((definition, bias, float(impact), float(confidence), source, note))
        saved = st.form_submit_button("Save fundamental snapshot", type="primary", use_container_width=True)
    if saved:
        factors = tuple(
            FundamentalFactor(
                key=definition.key,
                name=definition.name,
                category=definition.category,
                bias=FundamentalBias(bias),
                impact=impact,
                confidence=confidence / 100.0,
                observed_at=now,
                max_age_minutes=definition.max_age_minutes,
                source=source.strip() or "Manual research input",
                note=note.strip(),
            )
            for definition, bias, impact, confidence, source, note in entries
        )
        history_store().append_fundamental_factors(factors, recorded_at=now)
        st.success("Fundamental snapshot saved. The unified gate will use it on the next live evaluation.")
        st.rerun()


def technical_lab_page() -> None:
    now = now_ist()
    refresh_market_data(now)
    completed = completed_technical_candles(now)
    assessment = assess_technicals(completed)
    st.session_state.latest_technical_assessment = assessment
    header(
        "Technical Lab",
        "Twenty-five transparent indicators grouped into trend, momentum, volatility, structure and participation families.",
    )
    metrics = st.columns(6)
    metrics[0].metric("Technical bias", assessment.direction.value)
    metrics[1].metric("Setup score", f"{assessment.setup_score:.1f}/100")
    metrics[2].metric("Bullish", f"{assessment.bullish_score:.1f}")
    metrics[3].metric("Bearish", f"{assessment.bearish_score:.1f}")
    metrics[4].metric("Coverage", f"{assessment.coverage:.0f}%")
    metrics[5].metric("Accuracy status", "UNVALIDATED")
    if completed:
        st.caption(
            f"Source: Upstox V3 historical + intraday OHLCV · {len(completed)} completed "
            f"{st.session_state.stable_interval_minutes}-minute candles · "
            f"{completed[0].timestamp:%d %b %H:%M} to "
            f"{completed[-1].timestamp:%d %b %H:%M} IST"
        )
    elif configured_upstox_token():
        st.info("Waiting for valid Upstox historical and intraday candles.")
    else:
        st.info("Configure the read-only Upstox token to calculate technical indicators.")
    if st.session_state.technical_history_error:
        st.warning("Historical candle refresh failed: " + st.session_state.technical_history_error)
    family, core, indicators = st.tabs(
        ["Family consensus", "Nandi Top 10", "All 25 indicators"]
    )
    with family:
        family_frame = pd.DataFrame(assessment.family_rows)
        st.dataframe(family_frame, use_container_width=True, hide_index=True)
        if not family_frame.empty:
            st.bar_chart(family_frame.set_index("Family")[["Bullish", "Bearish", "Neutral"]])
    with core:
        rows = technical_rows(assessment)
        core_rows = [row for row in rows if row["Indicator"] in NANDI_TOP_10_INDICATORS]
        st.dataframe(pd.DataFrame(core_rows), use_container_width=True, hide_index=True, height=480)
        st.caption(
            "The Top 10 is Nandi's compact cross-family operator set; it is not an official NSE popularity ranking. "
            "The confluence gate continues to use family-balanced evidence from all 25 indicators."
        )
    with indicators:
        st.dataframe(pd.DataFrame(technical_rows(assessment)), use_container_width=True, hide_index=True, height=720)
    if assessment.blockers:
        st.warning(" | ".join(assessment.blockers))
    st.caption(
        "Indicators vote inside families first, so ten correlated trend indicators cannot overwhelm every other evidence family. "
        "Unavailable volume or warm-up indicators abstain instead of becoming guessed votes. "
        "The 70–80% win-rate target remains unvalidated until walk-forward and paper-trade samples prove it."
    )


def live_page() -> None:
    header("Command Center", "One decision gate combining fundamental context, technical families, live OI evidence and a stable risk lifecycle.")
    controls = st.columns([1, 1, 1, 2])
    if controls[0].button("Refresh NSE now", type="primary", use_container_width=True):
        st.session_state.force_refresh = True
    controls[1].metric("BUY threshold", f"{st.session_state.trade_threshold:.0f}")
    controls[2].metric("Email threshold", f"{st.session_state.email_threshold:.0f}")
    controls[3].caption("Nandi recalculates every second. NSE requests are rate-limited separately to reduce blocking.")
    chart, panel = st.columns([1.65, 1], gap="large")
    with chart:
        st.subheader("Live NIFTY — NSE spot")
        live_chart_fragment()
    with panel:
        st.subheader("Nandi decision")
        live_engine_fragment()
    st.subheader("Three-pillar agreement")
    pillar_summary_fragment()
    st.subheader("Live option-chain evidence")
    evidence_fragment()


def history_page() -> None:
    header("History", "Persistent decisions and trade lifecycle transitions.")
    decisions = history_store().recent(500)
    trades = history_store().recent_trade_events(500)
    dtab, ttab = st.tabs(["Decisions", "Trade lifecycle"])
    with dtab:
        if decisions:
            st.dataframe(pd.DataFrame(decisions), use_container_width=True, hide_index=True, height=600)
        else:
            st.info("No decisions stored yet.")
    with ttab:
        if trades:
            st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True, height=600)
        else:
            st.info("No lifecycle events stored yet.")


def replay_page() -> None:
    header("Replay", "Re-run Nandi on NSE frames already captured by this live terminal.")
    days = history_store().replay_days()
    if not days:
        st.info("Replay data will appear after live NSE frames are captured.")
        return
    day = st.selectbox("Trading day", days)
    snapshots, contexts = history_store().replay_data(day)
    if len(snapshots) < 2:
        st.warning("At least two stored frames are required.")
        return
    result = NandiReplay(trade_threshold=float(st.session_state.trade_threshold), prepare_threshold=max(60.0, float(st.session_state.trade_threshold) - 10.0)).run(snapshots, contexts)
    cols = st.columns(5)
    cols[0].metric("Frames", len(result.frames)); cols[1].metric("Entries", result.entries); cols[2].metric("CE", result.ce_entries); cols[3].metric("PE", result.pe_entries); cols[4].metric("Exits", result.exits)
    frame = pd.DataFrame([{"Time": x.snapshot.timestamp, "Spot": x.snapshot.spot, "Decision": x.decision.action.value, "Score": x.decision.score, "CE": x.decision.ce_score, "PE": x.decision.pe_score, "State": x.trade_state.status.value} for x in result.frames])
    st.line_chart(frame.set_index("Time")[["Spot"]])
    st.dataframe(frame, use_container_width=True, hide_index=True, height=520)


def results_page() -> None:
    header("Results", "Completed Nandi V2 trades grouped by day, week and month.")
    trades = completed_trades(history_store().trade_events())
    if not trades:
        st.info("Daily, weekly and monthly results will appear after Nandi records its first completed trade lifecycle.")
        return
    wins = sum(item.points > 0 for item in trades)
    metrics = st.columns(5)
    metrics[0].metric("Completed trades", len(trades))
    metrics[1].metric("Win rate", f"{wins / len(trades) * 100:.1f}%")
    metrics[2].metric("Net NIFTY points", f"{sum(item.points for item in trades):+.2f}")
    metrics[3].metric("CE trades", sum(item.side == "CE" for item in trades))
    metrics[4].metric("PE trades", sum(item.side == "PE" for item in trades))
    daily, weekly, monthly = st.tabs(["Daily", "Weekly", "Monthly"])
    for tab, period in ((daily, "daily"), (weekly, "weekly"), (monthly, "monthly")):
        with tab:
            rows = result_rows(trades, period)
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("Completed trade ledger"):
        st.dataframe(pd.DataFrame(trade_rows(trades)), use_container_width=True, hide_index=True)
    st.caption("Results use NIFTY underlying points from persisted ACTIVE-to-EXIT lifecycle events. They do not estimate option-premium P&L.")


def settings_page() -> None:
    header("Settings", "Tune live refresh and setup gates without changing the engine code.")
    left, right = st.columns(2, gap="large")
    with left:
        st.session_state.trade_threshold = st.slider("Minimum BUY score", 65.0, 90.0, float(st.session_state.trade_threshold), 1.0)
        st.session_state.email_threshold = st.slider("Minimum email score", 80.0, 95.0, float(st.session_state.email_threshold), 1.0)
        st.session_state.confirmation_evaluations = st.slider("Fresh OI snapshots required", 1, 5, int(st.session_state.confirmation_evaluations), 1)
        st.session_state.oi_refresh_seconds = st.slider("NSE OI refresh seconds", 15, 120, int(st.session_state.oi_refresh_seconds), 5)
        st.session_state.spot_refresh_seconds = st.slider("NSE spot refresh seconds", 2, 30, int(st.session_state.spot_refresh_seconds), 1)
        st.session_state.minimum_hold_minutes = st.slider("Minimum stable hold minutes", 5, 30, int(st.session_state.minimum_hold_minutes), 1)
        st.session_state.reversal_cooldown_minutes = st.slider("Reversal cooldown minutes", 0, 15, int(st.session_state.reversal_cooldown_minutes), 1)
    with right:
        smtp = smtp_settings()
        st.subheader("Email alerts")
        st.write({"Configured": smtp.configured, "Host": smtp.host or "Not configured", "Recipient": smtp.recipient or "Not configured"})
        st.caption("Email sends only on a confirmed BUY at or above the configured email score.")
        st.subheader("Live data")
        st.markdown(
            "**NSE:** option-chain-v3 and live index spot.  \n"
            "**Upstox:** read-only 15-minute NIFTY OHLC candles for stable structure.  \n"
            "**TradingView:** Lightweight Charts rendering plus external full-chart link."
        )
        st.write({
            "Upstox candle feed": "Configured" if configured_upstox_token() else "Not configured",
            "Stable timeframe": f"{st.session_state.stable_interval_minutes} minutes",
            "Forming candle": "Displayed — excluded from completed-candle structure",
            "Unified entry gate": "Fundamental + technical + OI required",
            "Technical model": "25 indicators grouped into five non-duplicative families",
            "Fundamental model": "Sourced and freshness-gated; unknown inputs block new entries",
        })


def sidebar() -> str:
    st.sidebar.markdown("## Nandi")
    st.sidebar.caption("Two-pillar NIFTY decision system")
    page = st.sidebar.radio(
        "Navigation",
        [
            "Command Center",
            "Fundamental Desk",
            "Technical Lab",
            "OI & Execution",
            "History",
            "Replay",
            "Results",
            "Settings",
        ],
        label_visibility="collapsed",
    )
    st.sidebar.divider()
    gate = gate_live_signals(now_ist(), market_schedule())
    st.sidebar.caption(f"NSE: {gate.status.label}")
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.logged_in = False
        st.rerun()
    return page


if not st.session_state.logged_in:
    login_page()
    st.stop()

page = sidebar()
if page == "Command Center":
    live_page()
elif page == "Fundamental Desk":
    fundamental_desk_page()
elif page == "Technical Lab":
    technical_lab_page()
elif page == "OI & Execution":
    header("OI & Execution", "Live ATM ±5 NSE option-chain rows, microstructure score evidence and source health.")
    evidence_fragment()
elif page == "History":
    history_page()
elif page == "Replay":
    replay_page()
elif page == "Results":
    results_page()
else:
    settings_page()
