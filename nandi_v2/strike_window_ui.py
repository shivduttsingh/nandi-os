from __future__ import annotations

from datetime import datetime
from typing import Iterable

import streamlit as st
import streamlit.components.v1 as components

from nandi_oi.models import IntradayCandle, OptionStrikeCandles
from nandi_v2.charting import candlestick_chart_html, completed_candles
from nandi_v2.strike_window_strategy import (
    StrikeWindowSignal,
    assess_strike_window_confirmation,
    strike_offset_label,
)


def render_strike_window_charts(
    nifty_candles: Iterable[IntradayCandle],
    strike_series: Iterable[OptionStrikeCandles],
    *,
    observed_at: datetime,
    interval_minutes: int,
) -> None:
    """Render the separate read-only ATM ±2 strategy and its ten option charts."""
    series = tuple(sorted(strike_series, key=lambda item: item.offset))
    if len(series) != 5:
        st.info("Waiting for the complete ATM ±2 CE/PE chart window from Upstox.")
        return

    completed_series = tuple(
        OptionStrikeCandles(
            strike=item.strike,
            expiry=item.expiry,
            offset=item.offset,
            ce_candles=completed_candles(
                item.ce_candles,
                observed_at,
                interval_minutes,
            ),
            pe_candles=completed_candles(
                item.pe_candles,
                observed_at,
                interval_minutes,
            ),
        )
        for item in series
    )
    assessment = assess_strike_window_confirmation(
        completed_candles(nifty_candles, observed_at, interval_minutes),
        completed_series,
    )

    expiry = series[0].expiry
    metrics = st.columns(5)
    metrics[0].metric("ATM ±2 strategy", assessment.signal.value)
    metrics[1].metric("Agreement", f"{assessment.agreement_score:.1f}/100")
    metrics[2].metric(
        "NIFTY move",
        "—"
        if assessment.nifty_change_pct is None
        else f"{assessment.nifty_change_pct:+.2f}%",
    )
    metrics[3].metric(
        "CE median move",
        "—"
        if assessment.ce_median_change_pct is None
        else f"{assessment.ce_median_change_pct:+.2f}%",
    )
    metrics[4].metric(
        "PE median move",
        "—"
        if assessment.pe_median_change_pct is None
        else f"{assessment.pe_median_change_pct:+.2f}%",
    )
    message = (
        f"{assessment.reason} Agreement strength: {assessment.agreement_score:.1f}/100. "
        "This is a paper-validation reading, not a win probability or an order instruction."
    )
    if assessment.signal in {
        StrikeWindowSignal.CONFIRM_CE,
        StrikeWindowSignal.CONFIRM_PE,
    }:
        st.success(message)
    elif assessment.signal == StrikeWindowSignal.WAIT:
        st.warning(message)
    else:
        st.info(message)
    st.caption(
        f"Positive premium breadth: CE {assessment.ce_positive_strikes}/5 · "
        f"PE {assessment.pe_positive_strikes}/5 · strongest side dominates "
        f"{assessment.dominant_strikes}/5 matching strikes. A confirmation requires at least "
        "four of five strikes plus the matching NIFTY direction."
    )

    ce_column, pe_column = st.columns(2, gap="large")
    with ce_column:
        st.markdown("#### CE side · five read-only charts")
    with pe_column:
        st.markdown("#### PE side · five read-only charts")

    for item in series:
        label = strike_offset_label(item.offset)
        for column, side, candles in (
            (ce_column, "CE", item.ce_candles),
            (pe_column, "PE", item.pe_candles),
        ):
            with column:
                st.metric(
                    f"{label} · {item.strike:.0f} {side}",
                    f"₹{candles[-1].close:,.2f}",
                    f"{candles[-1].close - candles[0].open:+.2f} today",
                )
                components.html(
                    candlestick_chart_html(
                        candles,
                        interval_minutes=interval_minutes,
                        title=f"NIFTY {item.strike:.0f} {side}",
                        subtitle=f"{label} · nearest expiry {expiry}",
                        evidence_note=(
                            f"Exact Upstox {side} premium chart for paper analysis only; "
                            "read only and no broker-order action."
                        ),
                        chart_height=260,
                    ),
                    height=375,
                    scrolling=False,
                )
