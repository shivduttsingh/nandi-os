from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError
from nandi_oi.market_schedule import MarketSchedule
from nandi_oi.models import IntradayCandle, OptionStrikeCandles
from nandi_v2.atm_strategy import assess_atm_confirmation
from nandi_v2.charting import candlestick_chart_html, completed_candles
from nandi_v2.nse import NSEDataError
from nandi_v2.strike_window_strategy import assess_strike_window_confirmation
from shiv_v1.engine import (
    SimilarityStats,
    assess_timeframe,
    build_shiv_decision,
    classify_market_regime,
    combine_timeframes,
    infer_candidate_side,
)
from shiv_v1.ui import (
    CORE_TIMEFRAMES,
    _completed_window,
    _oi_engine,
    _spread_for_side,
    fetch_nifty_candles,
    fetch_nse_snapshot,
    fetch_option_window,
)

from .a4_history import A4ResearchStore
from .elite import EliteAssessment, assess_a_plus_plus_plus_plus
from .replay import walk_forward_replay
from .safety import build_safe_v2_decision
from .strategy import manage_adaptive_exit, select_option_strike


IST = ZoneInfo("Asia/Kolkata")
PRIMARY_INTERVAL = 5
PAPER_QTY = 130


@st.cache_resource
def a4_store() -> A4ResearchStore:
    return A4ResearchStore()


def _option_candles(
    window: tuple[OptionStrikeCandles, ...], strike: float, side: str,
) -> tuple[IntradayCandle, ...]:
    item = next((row for row in window if abs(row.strike - strike) < 0.01), None)
    if item is None:
        return tuple()
    return item.ce_candles if side == "CE" else item.pe_candles


def _a4_persistence(side: str, snapshot_key: str) -> int:
    prior_key = st.session_state.get("a4_last_snapshot_key")
    prior_side = st.session_state.get("a4_last_side", "")
    prior_count = int(st.session_state.get("a4_persistence", 0))
    if snapshot_key == prior_key:
        return prior_count
    if side not in {"CE", "PE"}:
        count = 0
    elif side == prior_side:
        count = prior_count + 1
    else:
        count = 1
    st.session_state.a4_last_snapshot_key = snapshot_key
    st.session_state.a4_last_side = side
    st.session_state.a4_persistence = count
    return count


def _a4_setup_tracker(
    side: str,
    strike: float | None,
    premium: float | None,
    now: datetime,
) -> tuple[datetime | None, float | None]:
    if side not in {"CE", "PE"} or strike is None or premium is None or premium <= 0:
        st.session_state.a4_first_side = ""
        st.session_state.a4_first_strike = None
        st.session_state.a4_first_seen_at = None
        st.session_state.a4_first_premium = None
        return None, None
    prior_side = st.session_state.get("a4_first_side", "")
    prior_strike = st.session_state.get("a4_first_strike")
    first_seen = st.session_state.get("a4_first_seen_at")
    first_premium = st.session_state.get("a4_first_premium")
    if prior_side != side or prior_strike != strike or not isinstance(first_seen, datetime):
        first_seen = now
        first_premium = float(premium)
        st.session_state.a4_first_side = side
        st.session_state.a4_first_strike = float(strike)
        st.session_state.a4_first_seen_at = first_seen
        st.session_state.a4_first_premium = first_premium
    return first_seen, float(first_premium) if first_premium is not None else None


def _elite_signature(decision) -> str:
    selected = decision.strike_selection.selected
    offset = selected.offset if selected is not None else 99
    # Deliberately excludes absolute strike and all M/W fields so structurally
    # equivalent setups can be compared across different NIFTY price levels.
    return "|".join(
        (
            decision.base.signature,
            f"session={decision.session.bucket.value}",
            f"vol={decision.volatility.band.value}",
            f"offset={offset}",
        )
    )


def _close_a4_trade(active: dict[str, object], current: float, reason: str, now: datetime) -> None:
    a4_store().record_trade(
        opened_at=active["opened_at"],
        closed_at=now,
        signature=str(active["signature"]),
        interval_minutes=PRIMARY_INTERVAL,
        side=str(active["side"]),
        strike=float(active["strike"]),
        strike_offset=int(active["strike_offset"]),
        entry_price=float(active["entry_price"]),
        exit_price=float(current),
        exit_reason=reason,
        setup_quality=float(active["setup_quality"]),
        mtf_agreement=float(active["mtf_agreement"]),
        strike_score=float(active["strike_score"]),
        persistence=int(active["persistence"]),
        regime=str(active["regime"]),
        session_bucket=str(active["session_bucket"]),
        volatility_band=str(active["volatility_band"]),
    )
    st.session_state.a4_last_closed_at = now
    st.session_state.a4_paper_trade = None


def _update_a4_paper(
    elite: EliteAssessment,
    decision,
    raw_window: tuple[OptionStrikeCandles, ...],
    signature: str,
    now: datetime,
):
    active = st.session_state.get("a4_paper_trade")
    if active:
        candles = _option_candles(raw_window, float(active["strike"]), str(active["side"]))
        if candles:
            current = float(candles[-1].close)
            high = max(float(active["high_since_entry"]), float(candles[-1].high))
            active["high_since_entry"] = high
            current_stop = float(active["stop"])
            if current <= current_stop:
                _close_a4_trade(active, current, "A++++ PAPER STOP/TRAIL", now)
                return None
            held = max(0.0, (now - active["opened_at"]).total_seconds() / 60.0)
            plan = manage_adaptive_exit(
                float(active["entry_price"]),
                current,
                high,
                regime=decision.base.regime,
                volatility=decision.volatility,
                minutes_held=held,
            )
            active["current_price"] = current
            active["points"] = round(current - float(active["entry_price"]), 2)
            active["paper_pnl"] = round(active["points"] * PAPER_QTY, 2)
            active["stop"] = max(current_stop, float(plan.stop))
            active["exit_status"] = plan.status
            if plan.status in {"EXIT — TARGET 2", "EXIT — TIME DECAY"}:
                _close_a4_trade(active, current, plan.status, now)
                return None
            st.session_state.a4_paper_trade = active
        return active

    cooldown = st.session_state.get("a4_last_closed_at")
    in_cooldown = isinstance(cooldown, datetime) and now - cooldown < timedelta(minutes=10)
    selected = decision.strike_selection.selected
    entry = decision.entry_plan
    # Every elite live candidate is paper-tracked so A++++ can earn or lose its
    # historical validation. No broker order is ever sent.
    if (
        not in_cooldown
        and elite.live_candidate
        and selected is not None
        and entry.entry is not None
        and entry.stop is not None
    ):
        active = {
            "opened_at": now,
            "signature": signature,
            "side": decision.side,
            "strike": float(selected.strike),
            "strike_offset": int(selected.offset),
            "entry_price": float(entry.entry),
            "current_price": float(entry.entry),
            "high_since_entry": float(entry.entry),
            "stop": float(entry.stop),
            "points": 0.0,
            "paper_pnl": 0.0,
            "exit_status": "HOLD",
            "setup_quality": float(decision.setup_quality),
            "mtf_agreement": float(decision.base.mtf_agreement),
            "strike_score": float(selected.score),
            "persistence": int(decision.base.persistence_count),
            "regime": decision.base.regime.value,
            "session_bucket": decision.session.bucket.value,
            "volatility_band": decision.volatility.band.value,
        }
        st.session_state.a4_paper_trade = active
    return active


def _style() -> None:
    st.markdown(
        """
        <style>
        :root{--shiv:#126b3a;--ink:#14251c;--muted:#66756d;--line:#dce8e1;--soft:#f6faf7;--warn:#8a5a05;--bad:#9f2d2d}
        .block-container{max-width:1500px;padding-top:1rem;padding-bottom:4rem}
        .a4hero{border:1px solid var(--line);border-radius:22px;padding:1.35rem 1.5rem;background:linear-gradient(112deg,#fff 45%,#eaf6ef 100%);margin-bottom:1rem}
        .kicker{font-size:.72rem;letter-spacing:.14em;font-weight:800;color:var(--shiv);text-transform:uppercase}
        .title{font-size:2.2rem;font-weight:850;letter-spacing:-.045em;margin:.12rem 0}.copy{color:var(--muted);max-width:1000px}
        .a4card{border:1px solid var(--line);border-radius:18px;padding:1.05rem 1.2rem;background:#fff}
        .a4status{font-size:2rem;font-weight:850;color:var(--shiv);letter-spacing:-.04em}
        .gate{border-left:3px solid var(--bad);background:#fbf1f1;border-radius:0 8px 8px 0;padding:.4rem .65rem;margin:.3rem 0}
        div[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_history_summary() -> None:
    records = a4_store().recent_trades(300)
    st.subheader("A++++ paper evidence")
    if not records:
        st.info("No completed A++++ candidate paper trades yet. Validation starts only after real candidate outcomes accumulate.")
        return
    frame = pd.DataFrame([asdict(item) for item in records])
    cols = st.columns(5)
    cols[0].metric("Completed", len(frame))
    cols[1].metric("Wins", int((frame["points"] > 0).sum()))
    cols[2].metric("Observed win rate", f"{(frame['points'] > 0).mean() * 100:.1f}%")
    cols[3].metric("Avg points", f"{frame['points'].mean():+.2f}")
    cols[4].metric("Net points", f"{frame['points'].sum():+.2f}")
    st.dataframe(frame.head(40), use_container_width=True, hide_index=True, height=300)
    st.caption("These are completed A++++ candidate paper outcomes only. They are observations, not a guarantee of the next trade.")


def render_a4_terminal(access_token: str) -> None:
    _style()
    now = datetime.now(IST)
    session = MarketSchedule().status(now)

    st.markdown(
        """
        <div class="a4hero">
          <div class="kicker">Shiv · A++++</div>
          <div class="title">Ultra-selective trade gate</div>
          <div class="copy">Fixed 5-minute primary setup. It requires strong regime + 1m/3m/5m/15m agreement + ATM/ATM±2/OI alignment + premium follow-through + clean contract quality + persistence + no-chase entry timing. M/W is completely excluded.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown("## A++++")
    st.sidebar.caption("5m primary · 1m/3m/5m/15m confirmation")
    if session.is_open:
        st.sidebar.success("AUTO-REFRESH ON · every 30 seconds")
    else:
        st.sidebar.info("RESEARCH MODE · A++++ live gate locked")
    if st.sidebar.button("Refresh A++++ now", use_container_width=True):
        fetch_nse_snapshot.clear()
        fetch_nifty_candles.clear()
        fetch_option_window.clear()
        st.rerun()

    if not session.is_open:
        metrics = st.columns(4)
        metrics[0].metric("Session", session.label)
        metrics[1].metric("A++++", "LOCKED")
        metrics[2].metric("Current IST", now.strftime("%H:%M:%S"))
        metrics[3].metric("Next NSE open", session.next_open.strftime("%d %b · %H:%M"))
        st.info("A++++ never evaluates stale data as a live trade. The dedicated paper evidence remains available below.")
        _render_history_summary()
        return

    live_a4_fragment(access_token)


@st.fragment(run_every="30s")
def live_a4_fragment(access_token: str) -> None:
    now = datetime.now(IST)
    try:
        snapshot = fetch_nse_snapshot()
        required_timeframes = tuple(sorted(set(CORE_TIMEFRAMES + (PRIMARY_INTERVAL,))))
        nifty_by_tf = {interval: fetch_nifty_candles(access_token, interval) for interval in required_timeframes}
        spot_anchor = round(float(snapshot.spot) / 25.0) * 25.0
        raw_window = fetch_option_window(access_token, snapshot.expiry, spot_anchor, PRIMARY_INTERVAL)
    except (NSEDataError, UpstoxAPIError, ValueError) as exc:
        st.error(f"A++++ is waiting for valid read-only market data: {exc}")
        return

    completed_by_tf = {
        interval: completed_candles(candles, now, interval)
        for interval, candles in nifty_by_tf.items()
    }
    primary_completed = completed_by_tf[PRIMARY_INTERVAL]
    if len(primary_completed) < 6:
        st.warning("A++++ needs more completed 5-minute candles before evaluating a setup.")
        return

    mtf_rows = tuple(assess_timeframe(interval, completed_by_tf[interval]) for interval in CORE_TIMEFRAMES)
    mtf = combine_timeframes(mtf_rows)
    primary_regime = classify_market_regime(primary_completed)
    completed_window = _completed_window(raw_window, now, PRIMARY_INTERVAL)
    atm = next((item for item in completed_window if item.offset == 0), None)
    if atm is None:
        st.warning("ATM option pair is unavailable inside the current ±2 window.")
        return

    atm_assessment = assess_atm_confirmation(primary_completed, atm.ce_candles, atm.pe_candles)
    strike_assessment = assess_strike_window_confirmation(primary_completed, completed_window)
    raw_oi, oi_side, oi_score = _oi_engine(snapshot, primary_completed, now)
    candidate_side = infer_candidate_side(mtf, atm_assessment, strike_assessment, oi_side)
    snapshot_key = snapshot.timestamp.isoformat()
    persistence = _a4_persistence(candidate_side, snapshot_key)
    atm_spread = _spread_for_side(snapshot, candidate_side)
    base_option_candles = (
        atm.ce_candles if candidate_side == "CE"
        else atm.pe_candles if candidate_side == "PE"
        else tuple()
    )
    base = build_shiv_decision(
        interval_minutes=PRIMARY_INTERVAL,
        primary_regime=primary_regime,
        mtf=mtf,
        atm=atm_assessment,
        strike=strike_assessment,
        oi_side=oi_side,
        oi_score=oi_score,
        candidate_side=candidate_side,
        persistence_count=persistence,
        option_spread_pct=atm_spread,
        option_strike=atm.strike,
        option_candles=base_option_candles,
        similarity=SimilarityStats(),
    )

    preview = select_option_strike(snapshot, completed_window, candidate_side)
    premium = preview.selected.premium if preview.selected is not None else None
    strike = preview.selected.strike if preview.selected is not None else None
    first_seen, first_premium = _a4_setup_tracker(candidate_side, strike, premium, now)
    decision = build_safe_v2_decision(
        base=base,
        primary_regime=primary_regime,
        mtf=mtf,
        snapshot=snapshot,
        option_window=completed_window,
        primary_nifty=primary_completed,
        now=now,
        first_seen_at=first_seen,
        first_premium=first_premium,
        expiry=snapshot.expiry,
    )

    signature = _elite_signature(decision)
    similarity = a4_store().similarity_stats(signature, PRIMARY_INTERVAL, decision.side)
    comparable_walk_samples = tuple(
        sample for sample in a4_store().calibration_samples(1000)
        if sample.interval_minutes == PRIMARY_INTERVAL
        and sample.side == decision.side
        and sample.regime == decision.base.regime.value
    )
    walk = walk_forward_replay(comparable_walk_samples)
    elite = assess_a_plus_plus_plus_plus(decision, similarity, walk)
    selected = decision.strike_selection.selected
    entry = decision.entry_plan

    top = st.columns(6)
    top[0].metric("NIFTY", f"{float(snapshot.spot):,.2f}")
    top[1].metric("A++++", elite.status.replace("A++++ ", "", 1))
    top[2].metric("Side", decision.side)
    top[3].metric("Regime", decision.base.regime.value)
    top[4].metric("MTF", f"{decision.base.mtf_agreement:.0f}%")
    top[5].metric("Updated", now.strftime("%H:%M:%S"))

    observed = f"{elite.observed_win_rate:.1f}%" if elite.observed_win_rate is not None else "UNVALIDATED"
    wf_rate = f"{elite.walk_forward_win_rate:.1f}%" if elite.walk_forward_win_rate is not None else "UNVALIDATED"
    lower = f"{elite.confidence_lower_bound:.1f}%" if elite.confidence_lower_bound is not None else "UNVALIDATED"
    history_cols = st.columns(4)
    history_cols[0].metric("Comparable A++++ sample", elite.sample_size)
    history_cols[1].metric("Observed win rate", observed)
    history_cols[2].metric("Walk-forward observed", wf_rate)
    history_cols[3].metric("95% lower bound", lower)

    selected_text = (
        f"{selected.strike:.0f} {decision.side} · ATM {selected.offset:+d} · contract {selected.score:.0f}/100"
        if selected is not None else "No option selected"
    )
    entry_text = (
        f"{entry.status} · Entry ₹{entry.entry:.2f} · Stop ₹{entry.stop:.2f} · T1 ₹{entry.target_1:.2f} · T2 ₹{entry.target_2:.2f}"
        if entry.entry is not None and entry.stop is not None and entry.target_1 is not None and entry.target_2 is not None
        else entry.reason
    )
    blockers = "".join(f'<div class="gate">{item}</div>' for item in elite.blockers)
    st.markdown(
        f"""
        <div class="a4card">
          <div class="kicker">A++++ decision</div>
          <div class="a4status">{elite.status}</div>
          <div style="margin-top:.45rem"><b>{selected_text}</b></div>
          <div style="margin-top:.35rem"><b>{entry_text}</b></div>
          <div style="margin-top:.45rem;color:#66756d">{elite.reason}</div>
          <div style="margin-top:.75rem">{blockers}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    active = _update_a4_paper(elite, decision, raw_window, signature, now)
    live_tab, evidence_tab, chart_tab, paper_tab, history_tab = st.tabs(
        ["A++++ gates", "Underlying evidence", "Selected chart", "Paper tracker", "A++++ history"]
    )

    with live_tab:
        gate_rows = [
            {"Gate": "V2 + entry", "Observed": decision.entry_plan.status, "Requirement": "Directional confirm + ENTRY READY"},
            {"Gate": "Setup quality", "Observed": f"{decision.setup_quality:.1f}", "Requirement": f">= {max(90.0, decision.required_quality + 8.0):.1f}"},
            {"Gate": "MTF", "Observed": f"{decision.base.mtf_agreement:.1f}%", "Requirement": f">= {max(90.0, decision.required_mtf + 5.0):.1f}%"},
            {"Gate": "Persistence", "Observed": decision.base.persistence_count, "Requirement": f">= {max(3, decision.policy.minimum_persistence + 1)}"},
            {"Gate": "Contract score", "Observed": f"{selected.score:.1f}" if selected else "NONE", "Requirement": ">= 90"},
            {"Gate": "Spread", "Observed": f"{selected.spread_pct:.2f}%" if selected and selected.spread_pct is not None else "UNAVAILABLE", "Requirement": "<= 1.50%"},
            {"Gate": "Volume expansion", "Observed": f"{selected.volume_ratio:.2f}x" if selected and selected.volume_ratio is not None else "UNAVAILABLE", "Requirement": ">= 1.10x when available"},
            {"Gate": "Premium response", "Observed": f"{selected.responsiveness_pct:.2f}%" if selected else "UNAVAILABLE", "Requirement": ">= 1.00%"},
            {"Gate": "False breakout", "Observed": decision.breakout.status, "Requirement": "PASSED"},
            {"Gate": "Setup decay", "Observed": decision.decay.status, "Requirement": "ACTIVE / fresh"},
            {"Gate": "Session", "Observed": decision.session.bucket.value, "Requirement": "MORNING or AFTERNOON"},
            {"Gate": "Volatility", "Observed": decision.volatility.band.value, "Requirement": "Not EXTREME / UNAVAILABLE"},
        ]
        st.dataframe(pd.DataFrame(gate_rows), use_container_width=True, hide_index=True)
        st.caption("M/W is not a gate, score, blocker or confirmation anywhere in A++++.")

    with evidence_tab:
        left, right = st.columns(2, gap="large")
        with left:
            st.write(f"**ATM premium:** {atm_assessment.signal.value} · {atm_assessment.agreement_score:.0f}/100")
            st.write(f"**ATM ±2:** {strike_assessment.status_label or strike_assessment.signal.value} · {strike_assessment.agreement_score:.0f}/100")
            if raw_oi is not None:
                st.write(f"**OI/execution:** {oi_side} · CE {raw_oi.ce_score:.1f} / PE {raw_oi.pe_score:.1f} · {raw_oi.market_state}")
            st.write(f"**Volatility/expiry:** {decision.volatility.reason}")
            st.write(f"**Session:** {decision.session.reason}")
        with right:
            mtf_frame = pd.DataFrame(
                [
                    {
                        "TF": f"{row.interval_minutes}m",
                        "Regime": row.regime.value,
                        "Direction": row.direction.value,
                        "Strength": row.strength,
                        "Efficiency": row.efficiency,
                        "Move %": row.net_change_pct,
                    }
                    for row in mtf.rows
                ]
            )
            st.dataframe(mtf_frame, use_container_width=True, hide_index=True)

    with chart_tab:
        components.html(
            candlestick_chart_html(
                nifty_by_tf[PRIMARY_INTERVAL],
                interval_minutes=PRIMARY_INTERVAL,
                title="NIFTY 50 · A++++ primary",
                subtitle="5-minute read-only NIFTY structure",
                evidence_note="A++++ uses completed candles for decisions. The forming candle is display context only.",
                chart_height=380,
            ),
            height=500,
            scrolling=False,
        )
        if selected is not None:
            candles = _option_candles(raw_window, selected.strike, decision.side)
            if candles:
                components.html(
                    candlestick_chart_html(
                        candles,
                        interval_minutes=PRIMARY_INTERVAL,
                        title=f"{selected.strike:.0f} {decision.side}",
                        subtitle="A++++ selected option · read-only",
                        evidence_note="Selection is based on liquidity, spread, volume, premium response, OI availability and ATM proximity.",
                        chart_height=360,
                    ),
                    height=480,
                    scrolling=False,
                )

    with paper_tab:
        st.caption("A++++ candidates are automatically paper-tracked to build a dedicated validation sample. No broker order is sent.")
        if active is None:
            st.info("No A++++ candidate paper trade is active.")
        else:
            cols = st.columns(7)
            cols[0].metric("Side", str(active["side"]))
            cols[1].metric("Strike", f"{float(active['strike']):.0f}")
            cols[2].metric("Entry", f"₹{float(active['entry_price']):.2f}")
            cols[3].metric("Current", f"₹{float(active['current_price']):.2f}")
            cols[4].metric("Points", f"{float(active['points']):+.2f}")
            cols[5].metric("Qty", PAPER_QTY)
            cols[6].metric("Paper P&L", f"₹{float(active['paper_pnl']):+,.0f}")
            st.write(f"**Adaptive management:** {active['exit_status']} · stop ₹{float(active['stop']):.2f}")

    with history_tab:
        _render_history_summary()

    st.caption(
        "A++++ is an ultra-selective paper/research gate. An 85%+ historical rate is displayed only after sufficient completed A++++ outcomes; "
        "even then it is an observed historical rate, not an 85% guarantee for the next trade."
    )
