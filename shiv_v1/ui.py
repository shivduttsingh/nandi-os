from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_oi.models import IntradayCandle, OptionStrikeCandles
from nandi_v2.atm_strategy import assess_atm_confirmation
from nandi_v2.charting import candlestick_chart_html, completed_candles
from nandi_v2.engine import decide, limited_rows, nearest_atm
from nandi_v2.models import MarketContext
from nandi_v2.nse import NSEDataError, NSEPublicClient
from nandi_v2.strike_window_strategy import assess_strike_window_confirmation, strike_offset_label

from .engine import (
    Direction,
    SetupStage,
    SimilarityStats,
    assess_timeframe,
    build_shiv_decision,
    classify_market_regime,
    combine_timeframes,
    infer_candidate_side,
    manage_paper_exit,
    next_persistence,
)
from .history import ShivResearchStore


IST = ZoneInfo("Asia/Kolkata")
CORE_TIMEFRAMES = (1, 3, 5, 15)
PRIMARY_TIMEFRAMES = (1, 2, 3, 5, 10, 15, 30, 60)


@st.cache_resource
def research_store() -> ShivResearchStore:
    return ShivResearchStore()


@st.cache_resource
def nse_client() -> NSEPublicClient:
    return NSEPublicClient()


@st.cache_data(ttl=25, show_spinner=False)
def fetch_nse_snapshot() -> object:
    return nse_client().fetch_option_chain("NIFTY")


@st.cache_data(ttl=45, show_spinner=False)
def fetch_nifty_candles(token: str, interval_minutes: int) -> tuple[IntradayCandle, ...]:
    return UpstoxOptionChainClient(access_token=token, timeout_seconds=15).fetch_intraday_candles(interval_minutes)


@st.cache_data(ttl=45, show_spinner=False)
def fetch_option_window(
    token: str,
    expiry: str,
    spot_anchor: float,
    interval_minutes: int,
) -> tuple[OptionStrikeCandles, ...]:
    return UpstoxOptionChainClient(access_token=token, timeout_seconds=15).fetch_option_window_intraday_candles(
        expiry,
        spot_anchor,
        interval_minutes,
        wings=2,
    )


def _rsi(candles: tuple[IntradayCandle, ...], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    closes = [item.close for item in candles]
    changes = [right - left for left, right in zip(closes[:-1], closes[1:])][-period:]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _completed_window(
    series: tuple[OptionStrikeCandles, ...],
    observed_at: datetime,
    interval_minutes: int,
) -> tuple[OptionStrikeCandles, ...]:
    return tuple(
        OptionStrikeCandles(
            strike=item.strike,
            expiry=item.expiry,
            offset=item.offset,
            ce_candles=completed_candles(item.ce_candles, observed_at, interval_minutes),
            pe_candles=completed_candles(item.pe_candles, observed_at, interval_minutes),
        )
        for item in series
    )


def _oi_engine(snapshot: object, primary: tuple[IntradayCandle, ...], observed_at: datetime):
    if not primary:
        return None, "NONE", 0.0
    reference = primary[-8:]
    context = MarketContext(
        observed_at=observed_at,
        previous_spot=primary[-1].close,
        recent_high=max(item.high for item in reference),
        recent_low=min(item.low for item in reference),
        momentum_rsi=_rsi(primary),
    )
    raw = decide(snapshot, context, trade_threshold=65.0, prepare_threshold=55.0, minimum_edge=5.0)
    edge = abs(raw.ce_score - raw.pe_score)
    oi_side = "NONE"
    if edge >= 5.0:
        oi_side = "CE" if raw.ce_score > raw.pe_score else "PE"
    return raw, oi_side, max(raw.ce_score, raw.pe_score)


def _spread_for_side(snapshot: object, side: str) -> float | None:
    if side not in {"CE", "PE"}:
        return None
    rows = limited_rows(snapshot, wings=5)
    if not rows:
        return None
    row = nearest_atm(rows, snapshot.spot)
    leg = row.ce if side == "CE" else row.pe
    if leg.bid <= 0 or leg.ask <= 0 or leg.ask < leg.bid:
        return None
    midpoint = (leg.bid + leg.ask) / 2.0
    return (leg.ask - leg.bid) / midpoint * 100.0 if midpoint > 0 else None


def _persistence(candidate_side: str, snapshot_timestamp: datetime) -> int:
    state_side = st.session_state.get("shiv_persistence_side", "")
    state_count = int(st.session_state.get("shiv_persistence_count", 0))
    state_key = st.session_state.get("shiv_persistence_key", "")
    fresh_key = snapshot_timestamp.isoformat()
    if fresh_key == state_key:
        return state_count
    side, count = next_persistence(state_side, state_count, candidate_side)
    st.session_state.shiv_persistence_side = side
    st.session_state.shiv_persistence_count = count
    st.session_state.shiv_persistence_key = fresh_key
    return count


def _active_option_candles(
    window: tuple[OptionStrikeCandles, ...], strike: float, side: str,
) -> tuple[IntradayCandle, ...]:
    selected = next((item for item in window if abs(item.strike - strike) < 0.01), None)
    if selected is None:
        return tuple()
    return selected.ce_candles if side == "CE" else selected.pe_candles


def _close_paper_trade(active: dict[str, object], current: float, reason: str, now: datetime) -> None:
    research_store().record_trade(
        opened_at=active["opened_at"],
        closed_at=now,
        signature=str(active["signature"]),
        interval_minutes=int(active["interval_minutes"]),
        side=str(active["side"]),
        strike=float(active["strike"]),
        entry_price=float(active["entry_price"]),
        exit_price=float(current),
        exit_reason=reason,
        setup_quality=float(active["setup_quality"]),
    )
    st.session_state.shiv_last_closed_at = now
    st.session_state.shiv_paper_trade = None


def _update_paper_tracker(decision, window: tuple[OptionStrikeCandles, ...], now: datetime) -> dict[str, object] | None:
    active = st.session_state.get("shiv_paper_trade")
    if active:
        candles = _active_option_candles(window, float(active["strike"]), str(active["side"]))
        if candles:
            current = float(candles[-1].close)
            high = max(float(active["high_since_entry"]), float(candles[-1].high))
            active["high_since_entry"] = high
            current_stop = float(active["stop"])
            if current <= current_stop:
                _close_paper_trade(active, current, "PAPER TRAIL/STOP", now)
                return None
            plan = manage_paper_exit(float(active["entry_price"]), current, high)
            active["current_price"] = current
            active["unrealized_points"] = round(current - float(active["entry_price"]), 2)
            active["exit_status"] = plan.status
            active["stop"] = max(current_stop, float(plan.stop))
            if plan.status == "EXIT — TARGET 2":
                _close_paper_trade(active, current, plan.status, now)
                return None
            st.session_state.shiv_paper_trade = active
        return active

    cooldown_at = st.session_state.get("shiv_last_closed_at")
    in_cooldown = isinstance(cooldown_at, datetime) and now - cooldown_at < timedelta(minutes=5)
    if (
        not in_cooldown
        and decision.actionable
        and decision.entry_plan.status == "ENTRY READY"
        and decision.entry_plan.entry is not None
        and decision.entry_plan.strike is not None
    ):
        active = {
            "opened_at": now,
            "signature": decision.signature,
            "interval_minutes": int(st.session_state.shiv_primary_timeframe),
            "side": decision.side,
            "strike": float(decision.entry_plan.strike),
            "entry_price": float(decision.entry_plan.entry),
            "current_price": float(decision.entry_plan.entry),
            "high_since_entry": float(decision.entry_plan.entry),
            "stop": float(decision.entry_plan.stop or decision.entry_plan.entry - 3.5),
            "unrealized_points": 0.0,
            "exit_status": "HOLD",
            "setup_quality": float(decision.setup_quality),
        }
        st.session_state.shiv_paper_trade = active
    return active


def _style() -> None:
    st.markdown(
        """
        <style>
        :root{--shiv:#126b3a;--ink:#14251c;--muted:#66756d;--line:#dce8e1;--soft:#f6faf7;--warn:#8a5a05;--bad:#9f2d2d}
        .stApp{background:#fff;color:var(--ink)}
        .block-container{max-width:1550px;padding-top:1.1rem;padding-bottom:4rem}
        div[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px}
        .shiv-hero{border:1px solid var(--line);border-radius:20px;padding:1.2rem 1.4rem;background:linear-gradient(112deg,#fff 55%,#edf7f1 100%);margin-bottom:1rem}
        .shiv-kicker{font-size:.72rem;letter-spacing:.14em;font-weight:800;color:var(--shiv);text-transform:uppercase}
        .shiv-title{font-size:2rem;font-weight:850;letter-spacing:-.04em;margin:.15rem 0}.shiv-copy{color:var(--muted)}
        .decision-box{border:1px solid var(--line);border-radius:18px;padding:1rem 1.15rem;background:#fff}
        .decision-stage{font-size:2rem;font-weight:850;letter-spacing:-.04em;color:var(--shiv)}
        .blocker{border-left:3px solid var(--bad);background:#fbf1f1;border-radius:0 8px 8px 0;padding:.35rem .6rem;margin:.3rem 0}
        .reason{border-left:3px solid var(--shiv);background:var(--soft);border-radius:0 8px 8px 0;padding:.35rem .6rem;margin:.3rem 0}
        .small-note{color:var(--muted);font-size:.78rem}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _decision_card(decision) -> None:
    blockers = "".join(f'<div class="blocker">{item}</div>' for item in decision.blockers)
    reasons = "".join(f'<div class="reason">{item}</div>' for item in decision.reasons)
    stats = decision.similarity
    history_line = (
        f"Similar setups: {stats.sample_size} · historical win rate {stats.win_rate:.1f}% · avg {stats.average_points:+.2f} points"
        if stats.win_rate is not None and stats.average_points is not None
        else f"Similar setups: {stats.status} — no probability is shown until real paper outcomes exist."
    )
    entry = decision.entry_plan
    entry_text = (
        f"{entry.status} · ATM {entry.strike:.0f} {decision.side} · entry ₹{entry.entry:.2f} · stop ₹{entry.stop:.2f} · targets ₹{entry.target_1:.2f}/₹{entry.target_2:.2f}"
        if entry.entry is not None and entry.strike is not None and decision.side in {"CE", "PE"}
        else entry.reason
    )
    st.markdown(
        f"""
        <div class="decision-box">
          <div class="shiv-kicker">Shiv Decision</div>
          <div class="decision-stage">{decision.stage.value}</div>
          <div class="small-note">Setup quality {decision.setup_quality:.1f}/100 · regime {decision.regime.value} · MTF {decision.mtf_direction.value} {decision.mtf_agreement:.0f}% · persistence {decision.persistence_count}</div>
          <div style="margin-top:.7rem"><b>{entry_text}</b></div>
          <div class="small-note" style="margin-top:.35rem">{history_line}</div>
          <div style="margin-top:.8rem">{reasons}{blockers}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_charts(
    primary_nifty: tuple[IntradayCandle, ...],
    atm: OptionStrikeCandles,
    interval: int,
) -> None:
    st.subheader("Read-only market charts")
    components.html(
        candlestick_chart_html(
            primary_nifty,
            interval_minutes=interval,
            title="NIFTY 50",
            subtitle="Shiv primary timeframe · read-only Upstox V3 OHLC",
            evidence_note="Completed candles feed the Shiv regime and setup engine; the forming candle is display-only.",
            chart_height=460,
        ),
        height=575,
        scrolling=False,
    )
    left, right = st.columns(2, gap="large")
    with left:
        components.html(
            candlestick_chart_html(
                atm.ce_candles,
                interval_minutes=interval,
                title=f"NIFTY {atm.strike:.0f} CE",
                subtitle="ATM call premium · read only",
                evidence_note="Used for premium confirmation and paper entry timing only.",
                chart_height=330,
            ),
            height=445,
            scrolling=False,
        )
    with right:
        components.html(
            candlestick_chart_html(
                atm.pe_candles,
                interval_minutes=interval,
                title=f"NIFTY {atm.strike:.0f} PE",
                subtitle="ATM put premium · read only",
                evidence_note="Used for premium confirmation and paper entry timing only.",
                chart_height=330,
            ),
            height=445,
            scrolling=False,
        )


def _render_paper(active: dict[str, object] | None) -> None:
    st.subheader("Automatic paper tracker")
    st.caption("Research simulation only. Shiv never sends broker orders.")
    if not active:
        st.info("No active paper trade. Shiv waits for an actionable setup plus an ENTRY READY premium trigger.")
        return
    metrics = st.columns(6)
    metrics[0].metric("Side", str(active["side"]))
    metrics[1].metric("Strike", f"{float(active['strike']):.0f}")
    metrics[2].metric("Entry", f"₹{float(active['entry_price']):.2f}")
    metrics[3].metric("Current", f"₹{float(active['current_price']):.2f}")
    metrics[4].metric("Paper points", f"{float(active['unrealized_points']):+.2f}")
    metrics[5].metric("Protected stop", f"₹{float(active['stop']):.2f}")
    st.write(f"**Management state:** {active['exit_status']}")


def _render_history() -> None:
    trades = research_store().recent_trades(100)
    if not trades:
        st.info("No completed Shiv paper trades yet. Similar-setup win rates remain unvalidated.")
        return
    frame = pd.DataFrame([asdict(item) for item in trades])
    wins = int((frame["points"] > 0).sum())
    metrics = st.columns(4)
    metrics[0].metric("Completed paper trades", len(frame))
    metrics[1].metric("Observed win rate", f"{wins / len(frame) * 100:.1f}%")
    metrics[2].metric("Net premium points", f"{frame['points'].sum():+.2f}")
    metrics[3].metric("Average points", f"{frame['points'].mean():+.2f}")
    st.dataframe(frame, use_container_width=True, hide_index=True, height=420)
    st.caption("These are recorded Shiv paper outcomes, not a guaranteed probability of future profit. Local Streamlit storage can reset on redeploy.")


def render_shiv_terminal(access_token: str) -> None:
    _style()
    st.markdown(
        """
        <div class="shiv-hero"><div class="shiv-kicker">Shiv · Experimental NIFTY Research</div><div class="shiv-title">Decision first. Noise second.</div><div class="shiv-copy">Market regime → multi-timeframe NIFTY structure → ATM → ATM ±2 → OI/execution → persistence → entry → paper management → measured similar-setup results.</div></div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown("## Shiv")
    st.sidebar.caption("Experimental research branch · Nandi remains separate")
    primary = st.sidebar.selectbox(
        "Primary strategy timeframe",
        PRIMARY_TIMEFRAMES,
        index=PRIMARY_TIMEFRAMES.index(5),
        format_func=lambda value: f"{value}m" if value < 60 else "1h",
        key="shiv_primary_timeframe",
    )
    st.sidebar.caption("1m/3m/5m/15m NIFTY structure is always checked in the background. ATM and ATM ±2 use the selected primary timeframe.")
    if st.sidebar.button("Refresh Shiv data", use_container_width=True):
        fetch_nse_snapshot.clear()
        fetch_nifty_candles.clear()
        fetch_option_window.clear()
        st.rerun()

    live_terminal_fragment(access_token, int(primary))


@st.fragment(run_every="30s")
def live_terminal_fragment(access_token: str, primary_interval: int) -> None:
    now = datetime.now(IST)
    try:
        snapshot = fetch_nse_snapshot()
        required_timeframes = tuple(sorted(set(CORE_TIMEFRAMES + (primary_interval,))))
        nifty_by_tf = {interval: fetch_nifty_candles(access_token, interval) for interval in required_timeframes}
        spot_anchor = round(float(snapshot.spot) / 25.0) * 25.0
        option_window = fetch_option_window(access_token, snapshot.expiry, spot_anchor, primary_interval)
    except (NSEDataError, UpstoxAPIError, ValueError) as exc:
        st.error(f"Shiv is waiting for valid read-only market data: {exc}")
        return

    completed_by_tf = {
        interval: completed_candles(candles, now, interval)
        for interval, candles in nifty_by_tf.items()
    }
    primary_completed = completed_by_tf[primary_interval]
    if len(primary_completed) < 6:
        st.warning("Shiv needs more completed primary-timeframe candles before producing a directional setup.")
        return

    mtf_rows = tuple(assess_timeframe(interval, completed_by_tf[interval]) for interval in CORE_TIMEFRAMES)
    mtf = combine_timeframes(mtf_rows)
    primary_regime = classify_market_regime(primary_completed)

    completed_window = _completed_window(option_window, now, primary_interval)
    atm_series = next((item for item in completed_window if item.offset == 0), None)
    raw_atm = next((item for item in option_window if item.offset == 0), None)
    if atm_series is None or raw_atm is None:
        st.warning("ATM option pair is unavailable inside the current ±2 window.")
        return
    atm_assessment = assess_atm_confirmation(primary_completed, atm_series.ce_candles, atm_series.pe_candles)
    strike_assessment = assess_strike_window_confirmation(primary_completed, completed_window)
    raw_oi, oi_side, oi_score = _oi_engine(snapshot, primary_completed, now)
    candidate_side = infer_candidate_side(mtf, atm_assessment, strike_assessment, oi_side)
    persistence = _persistence(candidate_side, snapshot.timestamp)
    spread = _spread_for_side(snapshot, candidate_side)
    option_candles = (
        atm_series.ce_candles if candidate_side == "CE" else atm_series.pe_candles if candidate_side == "PE" else tuple()
    )

    preliminary = build_shiv_decision(
        interval_minutes=primary_interval,
        primary_regime=primary_regime,
        mtf=mtf,
        atm=atm_assessment,
        strike=strike_assessment,
        oi_side=oi_side,
        oi_score=oi_score,
        candidate_side=candidate_side,
        persistence_count=persistence,
        option_spread_pct=spread,
        option_strike=atm_series.strike,
        option_candles=option_candles,
        similarity=SimilarityStats(),
    )
    stats = research_store().similarity_stats(preliminary.signature, primary_interval, preliminary.side)
    decision = build_shiv_decision(
        interval_minutes=primary_interval,
        primary_regime=primary_regime,
        mtf=mtf,
        atm=atm_assessment,
        strike=strike_assessment,
        oi_side=oi_side,
        oi_score=oi_score,
        candidate_side=candidate_side,
        persistence_count=persistence,
        option_spread_pct=spread,
        option_strike=atm_series.strike,
        option_candles=option_candles,
        similarity=stats,
    )

    top = st.columns(6)
    top[0].metric("NIFTY", f"{float(snapshot.spot):,.2f}")
    top[1].metric("Regime", primary_regime.regime.value)
    top[2].metric("MTF", mtf.direction.value, f"{mtf.agreement_score:.0f}% agreement")
    top[3].metric("ATM", atm_assessment.signal.value, f"{atm_assessment.agreement_score:.0f}/100")
    top[4].metric("ATM ±2", strike_assessment.status_label or strike_assessment.signal.value, f"{strike_assessment.agreement_score:.0f}/100")
    top[5].metric("Shiv", decision.stage.value, f"{decision.setup_quality:.0f}/100")

    _decision_card(decision)
    active = _update_paper_tracker(decision, option_window, now)

    decision_tab, chart_tab, evidence_tab, paper_tab, results_tab = st.tabs(
        ["Decision evidence", "Charts", "ATM ±2 detail", "Paper trade", "Measured results"]
    )
    with decision_tab:
        left, right = st.columns([1.15, 1], gap="large")
        with left:
            st.subheader("Setup-quality components")
            score_frame = pd.DataFrame(decision.component_scores, columns=["Evidence", "Points"])
            st.bar_chart(score_frame.set_index("Evidence"))
            st.dataframe(score_frame, use_container_width=True, hide_index=True)
        with right:
            st.subheader("Core NIFTY timeframe agreement")
            mtf_frame = pd.DataFrame(
                [
                    {
                        "Timeframe": f"{row.interval_minutes}m",
                        "Regime": row.regime.value,
                        "Direction": row.direction.value,
                        "Strength": row.strength,
                        "Efficiency": row.efficiency,
                        "Net move %": row.net_change_pct,
                    }
                    for row in mtf.rows
                ]
            )
            st.dataframe(mtf_frame, use_container_width=True, hide_index=True)
            st.caption("1m = timing, 3m/5m = main structure, 15m = directional context. Mixed timeframes produce WAIT/NO TRADE rather than forced flips.")
        if raw_oi is not None:
            st.caption(
                f"OI/execution engine: CE {raw_oi.ce_score:.1f} · PE {raw_oi.pe_score:.1f} · state {raw_oi.market_state}. "
                "This is setup evidence, not a win probability."
            )

    with chart_tab:
        _render_charts(nifty_by_tf[primary_interval], raw_atm, primary_interval)

    with evidence_tab:
        rows = []
        for item in option_window:
            ce = item.ce_candles[-1] if item.ce_candles else None
            pe = item.pe_candles[-1] if item.pe_candles else None
            rows.append(
                {
                    "Position": strike_offset_label(item.offset),
                    "Strike": item.strike,
                    "CE premium": ce.close if ce else None,
                    "CE volume": ce.volume if ce else None,
                    "CE OI": ce.open_interest if ce else None,
                    "PE premium": pe.close if pe else None,
                    "PE volume": pe.volume if pe else None,
                    "PE OI": pe.open_interest if pe else None,
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        detail = st.columns(4)
        detail[0].metric("Weighted dominance", f"{strike_assessment.weighted_dominance_pct:.0f}%")
        detail[1].metric("OI confirmation", strike_assessment.oi_confirmation)
        detail[2].metric("Volume", strike_assessment.volume_confirmation)
        detail[3].metric("VWAP", strike_assessment.vwap_confirmation)
        st.write(strike_assessment.reason)
        if strike_assessment.blockers:
            st.warning(" | ".join(strike_assessment.blockers))

    with paper_tab:
        _render_paper(active)
        st.markdown(
            "**Paper exit ladder:** initial stop −3.5 points → +4 breakeven → +6 protected trail → +8 book/protect runner → +10 exit target."
        )

    with results_tab:
        _render_history()

    st.caption(
        "Shiv V1 is an experimental, read-only/paper research system. Setup quality is not probability. "
        "A historical win rate appears only after Shiv records completed paper trades for comparable setup signatures."
    )
