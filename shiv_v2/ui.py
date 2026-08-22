from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from nandi_oi import UpstoxAPIError
from nandi_oi.models import IntradayCandle, OptionStrikeCandles
from nandi_v2.atm_strategy import assess_atm_confirmation
from nandi_v2.charting import candlestick_chart_html, completed_candles
from nandi_v2.nse import NSEDataError
from nandi_v2.strike_window_strategy import assess_strike_window_confirmation
from shiv_v1.engine import (
    MarketRegime,
    SimilarityStats,
    assess_timeframe,
    build_shiv_decision,
    classify_market_regime,
    combine_timeframes,
    infer_candidate_side,
)
from shiv_v1.ui import (
    CORE_TIMEFRAMES,
    PRIMARY_TIMEFRAMES,
    _completed_window,
    _oi_engine,
    _persistence,
    _render_charts,
    _spread_for_side,
    fetch_nifty_candles,
    fetch_nse_snapshot,
    fetch_option_window,
)

from .history import ShivV2ResearchStore
from .replay import CalibrationSample, calibrate_thresholds, walk_forward_replay
from .strategy import (
    V2Decision,
    build_v2_decision,
    manage_adaptive_exit,
    select_option_strike,
)


IST = ZoneInfo("Asia/Kolkata")


@st.cache_resource
def v2_store() -> ShivV2ResearchStore:
    return ShivV2ResearchStore()


def _selected_option_candles(
    window: tuple[OptionStrikeCandles, ...], strike: float, side: str,
) -> tuple[IntradayCandle, ...]:
    selected = next((item for item in window if abs(item.strike - strike) < 0.01), None)
    if selected is None:
        return tuple()
    return selected.ce_candles if side == "CE" else selected.pe_candles


def _setup_tracker(candidate_side: str, snapshot_key: str, premium: float | None, strike: float | None, now: datetime):
    if candidate_side not in {"CE", "PE"} or premium is None or premium <= 0 or strike is None:
        st.session_state.shiv_v2_first_side = ""
        st.session_state.shiv_v2_first_seen_at = None
        st.session_state.shiv_v2_first_premium = None
        st.session_state.shiv_v2_first_strike = None
        return None, None

    prior_side = st.session_state.get("shiv_v2_first_side", "")
    prior_strike = st.session_state.get("shiv_v2_first_strike")
    first_seen = st.session_state.get("shiv_v2_first_seen_at")
    first_premium = st.session_state.get("shiv_v2_first_premium")
    if prior_side != candidate_side or prior_strike != strike or not isinstance(first_seen, datetime):
        first_seen = now
        first_premium = float(premium)
        st.session_state.shiv_v2_first_side = candidate_side
        st.session_state.shiv_v2_first_seen_at = first_seen
        st.session_state.shiv_v2_first_premium = first_premium
        st.session_state.shiv_v2_first_strike = float(strike)
        st.session_state.shiv_v2_first_snapshot = snapshot_key
    return first_seen, float(first_premium) if first_premium is not None else None


def _close_v2_trade(active: dict[str, object], current: float, reason: str, now: datetime) -> None:
    v2_store().record_trade(
        opened_at=active["opened_at"],
        closed_at=now,
        signature=str(active["signature"]),
        interval_minutes=int(active["interval_minutes"]),
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
        pattern=str(active["pattern"]),
    )
    st.session_state.shiv_v2_last_closed_at = now
    st.session_state.shiv_v2_paper_trade = None


def _update_v2_paper(decision: V2Decision, raw_window: tuple[OptionStrikeCandles, ...], now: datetime):
    active = st.session_state.get("shiv_v2_paper_trade")
    if active:
        candles = _selected_option_candles(raw_window, float(active["strike"]), str(active["side"]))
        if candles:
            current = float(candles[-1].close)
            high = max(float(active["high_since_entry"]), float(candles[-1].high))
            active["high_since_entry"] = high
            current_stop = float(active["stop"])
            if current <= current_stop:
                _close_v2_trade(active, current, "V2 PAPER STOP/TRAIL", now)
                return None
            held = max(0.0, (now - active["opened_at"]).total_seconds() / 60.0)
            try:
                regime = MarketRegime(str(active["regime"]))
            except ValueError:
                regime = decision.base.regime
            plan = manage_adaptive_exit(
                float(active["entry_price"]),
                current,
                high,
                regime=regime,
                volatility=decision.volatility,
                minutes_held=held,
            )
            active["current_price"] = current
            active["unrealized_points"] = round(current - float(active["entry_price"]), 2)
            active["exit_status"] = plan.status
            active["stop"] = max(current_stop, float(plan.stop))
            if plan.status in {"EXIT — TARGET 2", "EXIT — TIME DECAY"}:
                _close_v2_trade(active, current, plan.status, now)
                return None
            st.session_state.shiv_v2_paper_trade = active
        return active

    cooldown = st.session_state.get("shiv_v2_last_closed_at")
    in_cooldown = isinstance(cooldown, datetime) and now - cooldown < timedelta(minutes=5)
    selected = decision.strike_selection.selected
    entry = decision.entry_plan
    if not in_cooldown and decision.actionable and selected is not None and entry.entry is not None:
        active = {
            "opened_at": now,
            "signature": decision.signature,
            "interval_minutes": int(st.session_state.shiv_v2_primary_timeframe),
            "side": decision.side,
            "strike": float(selected.strike),
            "strike_offset": int(selected.offset),
            "entry_price": float(entry.entry),
            "current_price": float(entry.entry),
            "high_since_entry": float(entry.entry),
            "stop": float(entry.stop or entry.entry - decision.policy.stop_points),
            "unrealized_points": 0.0,
            "exit_status": "HOLD",
            "setup_quality": float(decision.setup_quality),
            "mtf_agreement": float(decision.base.mtf_agreement),
            "strike_score": float(selected.score),
            "persistence": int(decision.base.persistence_count),
            "regime": decision.base.regime.value,
            "session_bucket": decision.session.bucket.value,
            "volatility_band": decision.volatility.band.value,
            "pattern": decision.pattern.label,
        }
        st.session_state.shiv_v2_paper_trade = active
    return active


def _style() -> None:
    st.markdown(
        """
        <style>
        :root{--shiv:#126b3a;--ink:#14251c;--muted:#66756d;--line:#dce8e1;--soft:#f6faf7;--bad:#9f2d2d;--warn:#8a5a05}
        .stApp{background:#fff;color:var(--ink)}
        .block-container{max-width:1580px;padding-top:1.05rem;padding-bottom:4rem}
        div[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px}
        .v2hero{border:1px solid var(--line);border-radius:20px;padding:1.2rem 1.4rem;background:linear-gradient(112deg,#fff 50%,#eaf6ef 100%);margin-bottom:1rem}
        .kicker{font-size:.72rem;letter-spacing:.14em;font-weight:800;color:var(--shiv);text-transform:uppercase}
        .title{font-size:2rem;font-weight:850;letter-spacing:-.04em;margin:.15rem 0}.copy{color:var(--muted)}
        .v2card{border:1px solid var(--line);border-radius:18px;padding:1rem 1.15rem;background:#fff}
        .v2stage{font-size:2rem;font-weight:850;letter-spacing:-.04em;color:var(--shiv)}
        .blocker{border-left:3px solid var(--bad);background:#fbf1f1;border-radius:0 8px 8px 0;padding:.35rem .6rem;margin:.3rem 0}
        .reason{border-left:3px solid var(--shiv);background:var(--soft);border-radius:0 8px 8px 0;padding:.35rem .6rem;margin:.3rem 0}
        .small{font-size:.78rem;color:var(--muted)}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _decision_card(decision: V2Decision, similarity: SimilarityStats) -> None:
    selected = decision.strike_selection.selected
    entry = decision.entry_plan
    blockers = "".join(f'<div class="blocker">{item}</div>' for item in decision.blockers)
    reasons = "".join(f'<div class="reason">{item}</div>' for item in decision.reasons)
    selected_text = (
        f"{selected.strike:.0f} {decision.side} · ATM {selected.offset:+d} · contract score {selected.score:.0f}/100"
        if selected is not None else "No contract selected"
    )
    entry_text = (
        f"{entry.status} · entry ₹{entry.entry:.2f} · stop ₹{entry.stop:.2f} · targets ₹{entry.target_1:.2f}/₹{entry.target_2:.2f}"
        if entry.entry is not None and entry.stop is not None and entry.target_1 is not None and entry.target_2 is not None
        else entry.reason
    )
    history = (
        f"Similar V2 setup sample {similarity.sample_size} · observed win rate {similarity.win_rate:.1f}% · avg {similarity.average_points:+.2f} points"
        if similarity.win_rate is not None and similarity.average_points is not None
        else f"Similar V2 setups: {similarity.status}. No historical probability is shown yet."
    )
    st.markdown(
        f"""
        <div class="v2card">
          <div class="kicker">Shiv Advanced V2</div>
          <div class="v2stage">{decision.status}</div>
          <div class="small">Quality {decision.setup_quality:.1f}/{decision.required_quality:.1f} required · MTF {decision.base.mtf_agreement:.0f}%/{decision.required_mtf:.0f}% required · persistence {decision.base.persistence_count}/{decision.policy.minimum_persistence}</div>
          <div style="margin-top:.55rem"><b>{selected_text}</b></div>
          <div style="margin-top:.35rem"><b>{entry_text}</b></div>
          <div class="small" style="margin-top:.35rem">{history}</div>
          <div style="margin-top:.8rem">{reasons}{blockers}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_selected_chart(decision: V2Decision, raw_window: tuple[OptionStrikeCandles, ...], interval: int) -> None:
    selected = decision.strike_selection.selected
    if selected is None or decision.side not in {"CE", "PE"}:
        return
    candles = _selected_option_candles(raw_window, selected.strike, decision.side)
    if not candles:
        return
    components.html(
        candlestick_chart_html(
            candles,
            interval_minutes=interval,
            title=f"Selected contract · {selected.strike:.0f} {decision.side}",
            subtitle=f"Shiv V2 contract selector · ATM {selected.offset:+d} · score {selected.score:.0f}/100",
            evidence_note="Read-only Upstox premium chart. Selection score combines liquidity, volume, premium response, OI availability and ATM proximity.",
            chart_height=360,
        ),
        height=475,
        scrolling=False,
    )


def _render_paper(active) -> None:
    st.subheader("V2 automatic paper tracker")
    st.caption("Research simulation only. No broker order is sent.")
    if not active:
        st.info("No V2 paper trade is active. Direction confirmation and ENTRY READY must both be present.")
        return
    metrics = st.columns(7)
    metrics[0].metric("Side", str(active["side"]))
    metrics[1].metric("Strike", f"{float(active['strike']):.0f}")
    metrics[2].metric("Offset", f"ATM {int(active['strike_offset']):+d}")
    metrics[3].metric("Entry", f"₹{float(active['entry_price']):.2f}")
    metrics[4].metric("Current", f"₹{float(active['current_price']):.2f}")
    metrics[5].metric("Paper points", f"{float(active['unrealized_points']):+.2f}")
    metrics[6].metric("Stop", f"₹{float(active['stop']):.2f}")
    st.write(f"**Adaptive management:** {active['exit_status']}")


def _samples_from_upload(frame: pd.DataFrame) -> tuple[CalibrationSample, ...]:
    required = {
        "timestamp", "regime", "interval_minutes", "side", "setup_quality",
        "mtf_agreement", "strike_score", "persistence", "points",
    }
    if not required.issubset(frame.columns):
        return tuple()
    output: list[CalibrationSample] = []
    for _, row in frame.iterrows():
        try:
            output.append(CalibrationSample(
                timestamp=pd.to_datetime(row["timestamp"]).to_pydatetime(),
                regime=str(row["regime"]),
                interval_minutes=int(row["interval_minutes"]),
                side=str(row["side"]),
                setup_quality=float(row["setup_quality"]),
                mtf_agreement=float(row["mtf_agreement"]),
                strike_score=float(row["strike_score"]),
                persistence=int(row["persistence"]),
                points=float(row["points"]),
                session_bucket=str(row.get("session_bucket", "UNKNOWN")),
                volatility_band=str(row.get("volatility_band", "UNKNOWN")),
                pattern=str(row.get("pattern", "NONE")),
            ))
        except (TypeError, ValueError):
            continue
    return tuple(output)


def _render_calibration() -> None:
    records = v2_store().recent_trades(500)
    samples = v2_store().calibration_samples(1000)
    st.subheader("Walk-forward validation & threshold calibration")
    st.caption("Only completed V2 outcomes are used. Training thresholds never look at their future test fold.")
    if records:
        frame = pd.DataFrame([asdict(item) for item in records])
        metrics = st.columns(5)
        metrics[0].metric("Completed V2 trades", len(frame))
        metrics[1].metric("Observed wins", int((frame["points"] > 0).sum()))
        metrics[2].metric("Net points", f"{frame['points'].sum():+.2f}")
        metrics[3].metric("Avg points", f"{frame['points'].mean():+.2f}")
        metrics[4].metric("Avg hold", f"{frame['hold_minutes'].mean():.1f}m")
        st.dataframe(frame, use_container_width=True, hide_index=True, height=310)
        by_regime = frame.groupby("regime")["points"].agg(["count", "mean", "sum"]).reset_index()
        st.write("**Observed outcome by regime**")
        st.dataframe(by_regime, use_container_width=True, hide_index=True)
    else:
        st.info("No completed V2 trades yet. Calibration is intentionally unavailable until Shiv records real paper outcomes.")

    calibration = calibrate_thresholds(samples)
    if calibration.thresholds is None:
        st.warning(calibration.reason)
    else:
        c = calibration.thresholds
        cols = st.columns(5)
        cols[0].metric("Calibration", calibration.status)
        cols[1].metric("Min quality", f"{c.minimum_quality:.0f}")
        cols[2].metric("Min MTF", f"{c.minimum_mtf:.0f}%")
        cols[3].metric("Min strike score", f"{c.minimum_strike_score:.0f}")
        cols[4].metric("Persistence", c.minimum_persistence)
        st.caption(f"In-sample observed win rate {calibration.observed_win_rate:.1f}% · avg {calibration.average_points:+.2f} · {calibration.reason}")

    walk = walk_forward_replay(samples)
    if not walk.folds:
        st.info(walk.reason)
    else:
        cols = st.columns(5)
        cols[0].metric("Walk-forward status", walk.status)
        cols[1].metric("Test trades", walk.selected_trades)
        cols[2].metric("Observed test win rate", f"{walk.observed_win_rate:.1f}%" if walk.observed_win_rate is not None else "—")
        cols[3].metric("Avg test points", f"{walk.average_points:+.2f}" if walk.average_points is not None else "—")
        cols[4].metric("Max losing streak", walk.maximum_losing_streak)
        fold_frame = pd.DataFrame([
            {
                "Fold": fold.fold,
                "Train": f"{fold.train_start:%d %b} → {fold.train_end:%d %b}",
                "Test": f"{fold.test_start:%d %b} → {fold.test_end:%d %b}",
                "Quality": fold.thresholds.minimum_quality,
                "MTF": fold.thresholds.minimum_mtf,
                "Strike": fold.thresholds.minimum_strike_score,
                "Persistence": fold.thresholds.minimum_persistence,
                "Test trades": fold.selected_test_trades,
                "Win %": fold.observed_win_rate,
                "Avg points": fold.average_points,
                "Net points": fold.net_points,
            }
            for fold in walk.folds
        ])
        st.dataframe(fold_frame, use_container_width=True, hide_index=True)
        st.caption(walk.reason)

    with st.expander("Optional historical outcome CSV replay"):
        st.caption("Use real completed outcomes only. Required columns: timestamp, regime, interval_minutes, side, setup_quality, mtf_agreement, strike_score, persistence, points.")
        uploaded = st.file_uploader("Load completed historical outcomes", type=["csv"], key="shiv_v2_replay_csv")
        if uploaded is not None:
            try:
                uploaded_frame = pd.read_csv(uploaded)
            except Exception as exc:
                st.error(f"Could not read CSV: {exc}")
            else:
                uploaded_samples = _samples_from_upload(uploaded_frame)
                if not uploaded_samples:
                    st.error("CSV did not contain usable rows with the required columns.")
                else:
                    result = walk_forward_replay(uploaded_samples)
                    st.write(f"**Replay status:** {result.status} · samples {result.sample_size} · selected test trades {result.selected_trades}")
                    st.write(result.reason)


def render_shiv_terminal(access_token: str) -> None:
    _style()
    st.markdown(
        """
        <div class="v2hero"><div class="kicker">Shiv · Advanced V2 R&D</div><div class="title">Adaptive setup quality, not more indicator noise.</div><div class="copy">Regime-specific policy → time-of-day filter → volatility/expiry context → M/W structure → multi-timeframe confirmation → ATM/ATM±2/OI → intelligent contract selection → false-breakout filter → setup decay → adaptive paper management → walk-forward validation.</div></div>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("## Shiv Advanced V2")
    st.sidebar.caption("R&D branch · Nandi stable remains separate")
    primary = st.sidebar.selectbox(
        "Primary strategy timeframe",
        PRIMARY_TIMEFRAMES,
        index=PRIMARY_TIMEFRAMES.index(5),
        format_func=lambda value: f"{value}m" if value < 60 else "1h",
        key="shiv_v2_primary_timeframe",
    )
    st.sidebar.caption("1m/3m/5m/15m NIFTY context is always checked. The selected timeframe drives primary regime, option confirmation and setup ageing.")
    if st.sidebar.button("Refresh V2 data", use_container_width=True):
        fetch_nse_snapshot.clear()
        fetch_nifty_candles.clear()
        fetch_option_window.clear()
        st.rerun()
    live_v2_fragment(access_token, int(primary))


@st.fragment(run_every="30s")
def live_v2_fragment(access_token: str, primary_interval: int) -> None:
    now = datetime.now(IST)
    try:
        snapshot = fetch_nse_snapshot()
        required_timeframes = tuple(sorted(set(CORE_TIMEFRAMES + (primary_interval,))))
        nifty_by_tf = {interval: fetch_nifty_candles(access_token, interval) for interval in required_timeframes}
        spot_anchor = round(float(snapshot.spot) / 25.0) * 25.0
        option_window = fetch_option_window(access_token, snapshot.expiry, spot_anchor, primary_interval)
    except (NSEDataError, UpstoxAPIError, ValueError) as exc:
        st.error(f"Shiv V2 is waiting for valid read-only market data: {exc}")
        return

    completed_by_tf = {interval: completed_candles(candles, now, interval) for interval, candles in nifty_by_tf.items()}
    primary_completed = completed_by_tf[primary_interval]
    if len(primary_completed) < 6:
        st.warning("V2 needs more completed primary-timeframe candles before evaluating a setup.")
        return

    mtf_rows = tuple(assess_timeframe(interval, completed_by_tf[interval]) for interval in CORE_TIMEFRAMES)
    mtf = combine_timeframes(mtf_rows)
    primary_regime = classify_market_regime(primary_completed)
    completed_window = _completed_window(option_window, now, primary_interval)
    atm = next((item for item in completed_window if item.offset == 0), None)
    raw_atm = next((item for item in option_window if item.offset == 0), None)
    if atm is None or raw_atm is None:
        st.warning("ATM option pair is unavailable inside the current ±2 window.")
        return

    atm_assessment = assess_atm_confirmation(primary_completed, atm.ce_candles, atm.pe_candles)
    strike_assessment = assess_strike_window_confirmation(primary_completed, completed_window)
    raw_oi, oi_side, oi_score = _oi_engine(snapshot, primary_completed, now)
    candidate_side = infer_candidate_side(mtf, atm_assessment, strike_assessment, oi_side)
    persistence = _persistence(candidate_side, snapshot.timestamp)
    atm_spread = _spread_for_side(snapshot, candidate_side)
    base_option_candles = atm.ce_candles if candidate_side == "CE" else atm.pe_candles if candidate_side == "PE" else tuple()
    base = build_shiv_decision(
        interval_minutes=primary_interval,
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
    premium = preview.selected.premium if preview.selected else None
    strike = preview.selected.strike if preview.selected else None
    first_seen, first_premium = _setup_tracker(candidate_side, snapshot.timestamp.isoformat(), premium, strike, now)
    decision = build_v2_decision(
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
    selected = decision.strike_selection.selected
    similarity = v2_store().similarity_stats(decision.signature, primary_interval, decision.side)
    v2_store().record_observation(
        observed_at=now,
        snapshot_key=f"{snapshot.timestamp.isoformat()}|tf={primary_interval}",
        signature=decision.signature,
        interval_minutes=primary_interval,
        side=decision.side,
        regime=decision.base.regime.value,
        status=decision.status,
        setup_quality=decision.setup_quality,
        required_quality=decision.required_quality,
        mtf_agreement=decision.base.mtf_agreement,
        required_mtf=decision.required_mtf,
        strike=selected.strike if selected else None,
        strike_offset=selected.offset if selected else None,
        strike_score=selected.score if selected else 0.0,
        persistence=decision.base.persistence_count,
        session_bucket=decision.session.bucket.value,
        volatility_band=decision.volatility.band.value,
        pattern=decision.pattern.label,
        entry_status=decision.entry_plan.status,
    )

    first_row = st.columns(5)
    first_row[0].metric("NIFTY", f"{float(snapshot.spot):,.2f}")
    first_row[1].metric("Regime", decision.base.regime.value)
    first_row[2].metric("V2", decision.status, f"{decision.setup_quality:.0f}/{decision.required_quality:.0f} quality")
    first_row[3].metric("Session", decision.session.bucket.value)
    first_row[4].metric("Volatility", decision.volatility.band.value, decision.volatility.expiry_label)
    second_row = st.columns(5)
    second_row[0].metric("MTF", decision.base.mtf_direction.value, f"{decision.base.mtf_agreement:.0f}/{decision.required_mtf:.0f}%")
    second_row[1].metric("M/W", decision.pattern.label, f"{decision.pattern.confidence:.0f}/100")
    second_row[2].metric("False breakout", decision.breakout.status)
    second_row[3].metric("Setup age", decision.decay.status, f"{decision.decay.age_bars:.1f} bars")
    second_row[4].metric("Selected option", f"{selected.strike:.0f} {decision.side}" if selected else "NONE", f"score {selected.score:.0f}/100" if selected else "")

    _decision_card(decision, similarity)
    active = _update_v2_paper(decision, option_window, now)

    evidence_tab, charts_tab, strike_tab, paper_tab, calibration_tab, compare_tab = st.tabs(
        ["V2 evidence", "Charts", "Contract selector", "Paper trade", "Calibration & replay", "V1 vs V2"]
    )
    with evidence_tab:
        left, right = st.columns([1.1, 1], gap="large")
        with left:
            st.subheader("Adaptive requirements")
            policy_frame = pd.DataFrame([
                {"Gate": "Setup quality", "Observed": decision.setup_quality, "Required": decision.required_quality},
                {"Gate": "MTF agreement", "Observed": decision.base.mtf_agreement, "Required": decision.required_mtf},
                {"Gate": "Persistence", "Observed": decision.base.persistence_count, "Required": decision.policy.minimum_persistence},
                {"Gate": "Max option spread %", "Observed": selected.spread_pct if selected else None, "Required": decision.policy.maximum_spread_pct},
                {"Gate": "Max chase %", "Observed": decision.decay.premium_move_pct, "Required": decision.policy.maximum_chase_pct},
            ])
            st.dataframe(policy_frame, use_container_width=True, hide_index=True)
            st.write(f"**Time-of-day:** {decision.session.reason}")
            st.write(f"**Volatility/expiry:** {decision.volatility.reason}")
            st.write(f"**M/W:** {decision.pattern.reason}")
            st.write(f"**Breakout check:** {decision.breakout.reason}")
        with right:
            st.subheader("Core timeframe structure")
            mtf_frame = pd.DataFrame([
                {
                    "TF": f"{row.interval_minutes}m",
                    "Regime": row.regime.value,
                    "Direction": row.direction.value,
                    "Strength": row.strength,
                    "Efficiency": row.efficiency,
                    "Move %": row.net_change_pct,
                }
                for row in mtf.rows
            ])
            st.dataframe(mtf_frame, use_container_width=True, hide_index=True)
            if raw_oi is not None:
                st.caption(f"OI/execution evidence: CE {raw_oi.ce_score:.1f} · PE {raw_oi.pe_score:.1f} · {raw_oi.market_state}.")
            st.caption(f"ATM: {atm_assessment.signal.value} {atm_assessment.agreement_score:.0f}/100 · ATM±2: {strike_assessment.status_label or strike_assessment.signal.value} {strike_assessment.agreement_score:.0f}/100")

    with charts_tab:
        _render_charts(nifty_by_tf[primary_interval], raw_atm, primary_interval)
        _render_selected_chart(decision, option_window, primary_interval)

    with strike_tab:
        st.subheader("Intelligent option selection")
        if decision.strike_selection.candidates:
            candidate_frame = pd.DataFrame([asdict(item) for item in decision.strike_selection.candidates])
            candidate_frame["Selected"] = candidate_frame["strike"].apply(lambda value: "YES" if selected and abs(value - selected.strike) < 0.01 else "")
            st.dataframe(candidate_frame, use_container_width=True, hide_index=True)
        else:
            st.info(decision.strike_selection.reason)
        st.caption("Contract score is a liquidity/response selection score, not a win probability.")

    with paper_tab:
        _render_paper(active)
        st.write("Adaptive exits change by regime and volatility: trends can keep a wider runner, reversals book earlier, and low-volatility targets contract. Initial risk remains bounded.")

    with calibration_tab:
        _render_calibration()

    with compare_tab:
        cols = st.columns(2)
        cols[0].subheader("V1 evidence engine")
        cols[0].metric("V1 stage", base.stage.value)
        cols[0].metric("V1 setup quality", f"{base.setup_quality:.1f}/100")
        cols[0].write("V1 remains the transparent underlying evidence engine.")
        cols[1].subheader("V2 adaptive gate")
        cols[1].metric("V2 state", decision.status)
        cols[1].metric("Adaptive quality requirement", f"{decision.required_quality:.1f}")
        cols[1].write("V2 adds regime/time/volatility/M-W/strike/false-breakout/decay filters before paper execution.")
        if decision.blockers:
            st.warning("V2 rejected or delayed this setup because: " + " | ".join(decision.blockers))
        else:
            st.success("V2 adaptive gates currently agree with the underlying directional evidence.")

    st.caption(
        "Shiv Advanced V2 is read-only/paper research. Setup quality and contract score are not probabilities. "
        "Observed win rates appear only from completed outcomes; walk-forward test folds never select thresholds from their own future results."
    )
