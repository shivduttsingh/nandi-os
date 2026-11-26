from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


STRATEGY_CATALOG = [
    ("A", "Opening-range breakout continuation", "Breakout", "Implemented", "Opening range break, retest, VWAP and volume confirmation."),
    ("B", "Failed-breakout reversal", "Reversal", "Implemented", "Liquidity sweep, close back inside range and opposing structure break."),
    ("C", "Adaptive conflict-aware selector", "Regime selector", "Implemented", "Compares A and B and abstains when evidence conflicts."),
    ("D", "VWAP reclaim / rejection", "Price action", "Research candidate", "VWAP reclaim with volume and structure confirmation."),
    ("E", "Previous-day high/low breakout", "Breakout", "Research candidate", "Prior-session level break with acceptance and retest."),
    ("F", "Opening gap continuation / fill", "Gap", "Research candidate", "Classifies gap regime and tests continuation versus gap-fill behaviour."),
    ("G", "Range expansion", "Volatility", "Research candidate", "Detects compression followed by statistically unusual range expansion."),
    ("H", "OI buildup + price confirmation", "Options flow", "Research candidate", "Pairs strike-wise OI change with underlying direction and premium response."),
    ("I", "OI unwinding reversal", "Options flow", "Research candidate", "Looks for concentrated unwinding with price confirmation."),
    ("J", "PCR regime shift", "Options flow", "Research candidate", "Tracks changes in put-call positioning across strikes and time."),
    ("K", "IV expansion momentum", "Volatility", "Research candidate", "Tests directional premium expansion against implied-volatility changes."),
    ("L", "IV crush / premium decay filter", "Volatility", "Research candidate", "Avoids buying when volatility contraction dominates directional edge."),
    ("M", "Multi-timeframe trend alignment", "Trend", "Research candidate", "Requires agreement across intraday and higher timeframes."),
    ("N", "Momentum pullback continuation", "Trend", "Research candidate", "Enters after a controlled pullback in a confirmed directional regime."),
    ("O", "Mean reversion to VWAP", "Mean reversion", "Research candidate", "Tests stretched moves reverting toward session VWAP."),
    ("P", "Support / resistance rejection", "Price action", "Research candidate", "Tests rejection at levels with follow-through confirmation."),
    ("Q", "Volume-price divergence", "Volume", "Research candidate", "Flags divergence between participation and price movement."),
    ("R", "Volatility squeeze breakout", "Volatility", "Research candidate", "Detects low-volatility compression before a directional expansion."),
    ("S", "Expiry-session regime filter", "Expiry", "Research candidate", "Separates expiry-day behaviour and premium-risk conditions."),
    ("T", "News / event risk filter", "Event", "Research candidate", "Classifies scheduled catalysts and suppresses unsuitable entries."),
    ("U", "Calendar / overnight gap risk", "Overnight", "Research candidate", "Uses overnight context to adjust next-session scenarios."),
    ("V", "Options liquidity and spread filter", "Execution", "Research candidate", "Rejects illiquid contracts and wide bid-ask spreads."),
]


def _safe_frame(value: Any) -> pd.DataFrame | None:
    if isinstance(value, pd.DataFrame) and not value.empty:
        return value.copy()
    return None


def _standardise_time(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in out.columns:
        if str(col).lower() in {"timestamp", "datetime", "date", "time", "trade_time"}:
            parsed = pd.to_datetime(out[col], errors="coerce")
            if parsed.notna().any():
                out[col] = parsed
                break
    return out


def _ohlc_chart(frame: pd.DataFrame, title: str, height: int = 620):
    time_col = next((c for c in frame.columns if str(c).lower() in {"timestamp", "datetime", "date", "time", "trade_time"}), None)
    lookup = {str(c).lower(): c for c in frame.columns}
    required = ["open", "high", "low", "close"]
    if time_col and all(k in lookup for k in required):
        fig = go.Figure(go.Candlestick(
            x=frame[time_col], open=frame[lookup["open"]], high=frame[lookup["high"]],
            low=frame[lookup["low"]], close=frame[lookup["close"]], name="OHLC"
        ))
        volume_key = lookup.get("volume")
        if volume_key:
            fig.add_trace(go.Bar(x=frame[time_col], y=frame[volume_key], name="Volume", yaxis="y2", opacity=0.22))
            fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, title="Volume"))
        fig.update_layout(title=title, height=height, xaxis_rangeslider_visible=True, hovermode="x unified",
                          margin=dict(l=12,r=12,t=50,b=12), template="plotly_white")
        return fig
    return None


def _add_indicators(frame: pd.DataFrame, time_col: str, y_col: str, selected: list[str]):
    data = frame.copy()
    data[y_col] = pd.to_numeric(data[y_col], errors="coerce")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=data[time_col], y=data[y_col], mode="lines", name=y_col))
    values = data[y_col]
    if "SMA 20" in selected:
        fig.add_trace(go.Scatter(x=data[time_col], y=values.rolling(20, min_periods=5).mean(), name="SMA 20"))
    if "SMA 50" in selected:
        fig.add_trace(go.Scatter(x=data[time_col], y=values.rolling(50, min_periods=10).mean(), name="SMA 50"))
    if "Bollinger Bands" in selected:
        mid = values.rolling(20, min_periods=5).mean()
        sd = values.rolling(20, min_periods=5).std()
        fig.add_trace(go.Scatter(x=data[time_col], y=mid + 2 * sd, name="Upper band", line=dict(dash="dot")))
        fig.add_trace(go.Scatter(x=data[time_col], y=mid - 2 * sd, name="Lower band", line=dict(dash="dot"), fill="tonexty"))
    if "VWAP" in selected and {"high", "low", "close", "volume"}.issubset({str(c).lower() for c in data.columns}):
        cols = {str(c).lower(): c for c in data.columns}
        typical = (pd.to_numeric(data[cols["high"]], errors="coerce") +
                   pd.to_numeric(data[cols["low"]], errors="coerce") +
                   pd.to_numeric(data[cols["close"]], errors="coerce")) / 3
        vol = pd.to_numeric(data[cols["volume"]], errors="coerce").fillna(0)
        session_key = data[time_col].dt.date if pd.api.types.is_datetime64_any_dtype(data[time_col]) else pd.Series("all", index=data.index)
        vwap = (typical * vol).groupby(session_key).cumsum() / vol.groupby(session_key).cumsum().replace(0, np.nan)
        fig.add_trace(go.Scatter(x=data[time_col], y=vwap, name="VWAP"))
    fig.update_layout(title=f"{y_col} with selected overlays", height=560, hovermode="x unified",
                      template="plotly_white", margin=dict(l=12,r=12,t=50,b=12))
    return fig


def _fetch_upstox_history(token: str, instrument_key: str, unit: str, interval: int, start: date, end: date) -> pd.DataFrame:
    import requests
    from urllib.parse import quote
    if not token:
        raise ValueError("Add your private Upstox read-only access token in Streamlit secrets or the server environment.")
    if start > end:
        raise ValueError("Start date must be on or before end date.")
    if unit == "minutes":
        max_days = 30 if interval <= 15 else 89
    elif unit == "hours":
        max_days = 89
    elif unit == "days":
        max_days = 3650
    else:
        max_days = (end - start).days + 1
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + pd.Timedelta(days=max_days - 1).to_pytimedelta())
        path = (
            f"https://api.upstox.com/v3/historical-candle/{quote(instrument_key, safe='')}/"
            f"{unit}/{interval}/{chunk_end.isoformat()}/{cursor.isoformat()}"
        )
        response = requests.get(path, headers={"Accept":"application/json","Authorization":f"Bearer {token}"}, timeout=25)
        if response.status_code != 200:
            raise RuntimeError(f"Upstox history request failed ({response.status_code}): {response.text[:400]}")
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"Upstox history returned an unsuccessful response: {payload}")
        for row in (payload.get("data") or {}).get("candles") or []:
            if len(row) >= 5:
                chunks.append({
                    "timestamp": row[0], "open": row[1], "high": row[2], "low": row[3],
                    "close": row[4], "volume": row[5] if len(row) > 5 else 0,
                    "open_interest": row[6] if len(row) > 6 else 0,
                    "instrument_key": instrument_key, "unit": unit, "interval": interval,
                })
        cursor = chunk_end + pd.Timedelta(days=1).to_pytimedelta()
    frame = pd.DataFrame(chunks)
    if frame.empty:
        raise RuntimeError("Upstox returned no historical candles for this instrument and date range.")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    return frame.dropna(subset=["timestamp"]).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def render_chart_explorer(st_module, pd_module, live, bars, data_label: str, upstox_token: str = ""):
    st_module.subheader("Universal Chart Explorer")
    st_module.caption("Chart any available or uploaded dataset. Interactive zoom, pan, range selection, hover values and chart export are available through Plotly.")
    datasets: dict[str, pd.DataFrame] = {}
    loaded = _safe_frame(bars)
    if loaded is not None:
        datasets["Loaded NIFTY candles"] = loaded
    if isinstance(live, dict):
        for key, label in [("upstox_chain", "Upstox option chain"), ("nse_chain", "NSE option chain")]:
            frame = _safe_frame(live.get(key))
            if frame is not None:
                datasets[label] = frame
        if live.get("candles"):
            datasets["Live NIFTY candles"] = pd.DataFrame([{
                "timestamp": c.timestamp, "open": c.open, "high": c.high, "low": c.low,
                "close": c.close, "volume": c.volume, "open_interest": getattr(c, "open_interest", 0)
            } for c in live["candles"]])
        if live.get("strike_window"):
            rows = []
            for item in live["strike_window"]:
                for side, candles in [("CE", item.ce_candles), ("PE", item.pe_candles)]:
                    for candle in candles:
                        rows.append({"timestamp": candle.timestamp, "strike": item.strike, "expiry": item.expiry,
                                     "offset": item.offset, "side": side, "open": candle.open, "high": candle.high,
                                     "low": candle.low, "close": candle.close, "volume": candle.volume,
                                     "open_interest": getattr(candle, "open_interest", 0)})
            if rows:
                datasets["ATM window option candles"] = pd.DataFrame(rows)
    custom = st_module.session_state.get("atlas_uploaded_datasets", {})
    for name, frame in custom.items():
        valid = _safe_frame(frame)
        if valid is not None:
            datasets[name] = valid
    with st_module.expander("Fetch historical candles from Upstox for any instrument", expanded=False):
        st_module.caption("Use an exact Upstox instrument key. Minute/hour availability and maximum request windows depend on the selected interval; ATLAS chunks requests to stay within documented limits.")
        hx1, hx2 = st_module.columns([2,1])
        instrument_key = hx1.text_input("Upstox instrument key", value="NSE_INDEX|Nifty 50", key="atlas_history_instrument")
        unit = hx2.selectbox("Time unit", ["minutes","hours","days","weeks","months"], key="atlas_history_unit")
        if unit == "minutes":
            interval = st_module.selectbox("Interval", list(range(1, 16)) + [20,30,45,60,75,90,120,150,180,240,300], index=4, key="atlas_history_interval")
        elif unit == "hours":
            interval = st_module.selectbox("Interval", [1,2,3,4,5], key="atlas_history_interval")
        else:
            interval = 1
            st_module.caption("Interval: 1")
        hx3, hx4 = st_module.columns(2)
        start_date = hx3.date_input("From date", value=date(2022,1,1), key="atlas_history_from")
        end_date = hx4.date_input("To date", value=date.today(), key="atlas_history_to")
        if st_module.button("Fetch historical candles", type="primary", key="atlas_fetch_history"):
            try:
                hist = _fetch_upstox_history(upstox_token, instrument_key.strip(), unit, int(interval), start_date, end_date)
                name = f"Upstox · {instrument_key.strip()} · {unit} {interval}"
                st_module.session_state.setdefault("atlas_uploaded_datasets", {})[name] = hist
                datasets[name] = hist
                st_module.success(f"Loaded {len(hist):,} historical candles. Select this dataset below to chart any available field.")
            except Exception as exc:
                st_module.error(str(exc))
    with st_module.expander("Add any dataset to the chart workspace", expanded=not bool(datasets)):
        uploads = st_module.file_uploader("Upload CSV or Excel files", type=["csv", "xlsx", "xls"], accept_multiple_files=True, key="atlas_chart_uploads")
        for upload in uploads or []:
            try:
                if upload.name.lower().endswith(".csv"):
                    frame = pd_module.read_csv(upload)
                else:
                    frame = pd_module.read_excel(upload)
                if frame.empty:
                    st_module.warning(f"{upload.name}: file contains no rows.")
                    continue
                frame = _standardise_time(frame)
                st_module.session_state.setdefault("atlas_uploaded_datasets", {})[upload.name] = frame
                datasets[upload.name] = frame
            except Exception as exc:
                st_module.error(f"Could not load {upload.name}: {exc}")
    if not datasets:
        st_module.info("Load a live snapshot or upload a CSV/Excel file in Data & Controls or above. The chart builder will expose every numeric column in the selected dataset.")
        return
    left, right = st_module.columns([1, 2])
    selected_name = left.selectbox("Dataset", list(datasets), key="atlas_chart_dataset")
    frame = _standardise_time(datasets[selected_name])
    right.caption(f"{selected_name} · {len(frame):,} rows · {len(frame.columns)} columns")
    if frame.empty:
        st_module.warning("Selected dataset is empty.")
        return
    time_cols = [c for c in frame.columns if pd_module.api.types.is_datetime64_any_dtype(frame[c]) or str(c).lower() in {"timestamp","datetime","date","time","trade_time"}]
    numeric_cols = [c for c in frame.columns if pd_module.api.types.is_numeric_dtype(frame[c])]
    if time_cols:
        date_col = st_module.selectbox("Date / time column", time_cols, key="atlas_chart_time")
        parsed = pd_module.to_datetime(frame[date_col], errors="coerce")
        if parsed.notna().any():
            frame = frame.loc[parsed.notna()].copy()
            frame[date_col] = parsed.loc[parsed.notna()]
            min_date, max_date = frame[date_col].min().date(), frame[date_col].max().date()
            date_range = st_module.date_input("Visible date range", value=(min_date, max_date), min_value=min_date, max_value=max_date, key="atlas_chart_dates")
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start, end = date_range
            else:
                start = end = date_range
            frame = frame[(frame[date_col].dt.date >= start) & (frame[date_col].dt.date <= end)]
    else:
        date_col = st_module.selectbox("X-axis column", list(frame.columns), key="atlas_chart_x")
    if frame.empty:
        st_module.warning("No rows in the selected date range.")
        return
    type_options = ["Auto OHLC candlestick", "Line", "Area", "Bar", "Scatter", "Histogram", "Box", "Heatmap"]
    chart_type = st_module.selectbox("Chart type", type_options, key="atlas_chart_type")
    title = st_module.text_input("Chart title", value=f"{selected_name} — ATLAS", key="atlas_chart_title")
    if chart_type == "Auto OHLC candlestick":
        fig = _ohlc_chart(frame, title)
        if fig is None:
            st_module.warning("This dataset does not contain a date/time column and Open, High, Low, Close columns. Choose another chart type.")
            return
        st_module.plotly_chart(fig, use_container_width=True, theme="streamlit", key="atlas_ohlc_chart")
    elif chart_type == "Heatmap":
        if len(numeric_cols) < 2:
            st_module.warning("A heatmap needs at least two numeric columns.")
            return
        xcol = st_module.selectbox("Heatmap X", [date_col] + numeric_cols, key="atlas_heat_x")
        ycol = st_module.selectbox("Heatmap Y", numeric_cols, key="atlas_heat_y")
        zcol = st_module.selectbox("Heatmap intensity", numeric_cols, key="atlas_heat_z")
        pivot = frame.pivot_table(index=ycol, columns=xcol, values=zcol, aggfunc="mean")
        fig = px.imshow(pivot, aspect="auto", title=title, labels=dict(color=zcol), color_continuous_scale="RdYlGn")
        fig.update_layout(height=650, template="plotly_white")
        st_module.plotly_chart(fig, use_container_width=True, key="atlas_heatmap")
    elif chart_type == "Histogram":
        ycol = st_module.selectbox("Value", numeric_cols, key="atlas_hist_y")
        fig = px.histogram(frame, x=ycol, nbins=60, title=title, marginal="box")
        st_module.plotly_chart(fig, use_container_width=True, key="atlas_hist")
    elif chart_type == "Box":
        ycol = st_module.selectbox("Value", numeric_cols, key="atlas_box_y")
        group = st_module.selectbox("Group (optional)", ["None"] + [c for c in frame.columns if c != ycol], key="atlas_box_group")
        fig = px.box(frame, x=None if group == "None" else group, y=ycol, title=title, points="outliers")
        st_module.plotly_chart(fig, use_container_width=True, key="atlas_box")
    else:
        if not numeric_cols:
            st_module.warning("No numeric columns are available in this dataset.")
            return
        xcol = st_module.selectbox("X-axis", list(frame.columns), index=list(frame.columns).index(date_col) if date_col in frame.columns else 0, key="atlas_xy_x")
        ycols = st_module.multiselect("Y-axis series (select as many as you want)", numeric_cols, default=numeric_cols[:min(3,len(numeric_cols))], key="atlas_xy_y")
        color_col = st_module.selectbox("Colour / grouping (optional)", ["None"] + [c for c in frame.columns if c not in ycols], key="atlas_xy_color")
        if not ycols:
            st_module.warning("Select at least one numeric Y-axis series.")
            return
        if len(ycols) == 1 and chart_type in {"Line", "Area"}:
            overlays = st_module.multiselect("Technical overlays", ["SMA 20", "SMA 50", "Bollinger Bands", "VWAP"], key="atlas_overlays")
            fig = _add_indicators(frame, xcol, ycols[0], overlays) if overlays else None
        else:
            fig = None
        if fig is None:
            long = frame[[xcol] + ycols + ([] if color_col == "None" else [color_col])].melt(
                id_vars=[xcol] + ([] if color_col == "None" else [color_col]), value_vars=ycols,
                var_name="Series", value_name="Value")
            if chart_type == "Line":
                fig = px.line(long, x=xcol, y="Value", color="Series" if color_col == "None" else color_col,
                              line_dash="Series" if color_col != "None" else None, title=title)
            elif chart_type == "Area":
                fig = px.area(long, x=xcol, y="Value", color="Series", title=title)
            elif chart_type == "Bar":
                fig = px.bar(long, x=xcol, y="Value", color="Series", barmode="group", title=title)
            else:
                fig = px.scatter(long, x=xcol, y="Value", color="Series", title=title)
        fig.update_layout(height=620, hovermode="x unified", template="plotly_white", margin=dict(l=12,r=12,t=50,b=12))
        st_module.plotly_chart(fig, use_container_width=True, key="atlas_custom_chart")
    st_module.download_button("Download selected dataset as CSV", frame.to_csv(index=False), file_name="atlas_chart_dataset.csv", mime="text/csv")
    with st_module.expander("Inspect underlying rows"):
        st_module.dataframe(frame, use_container_width=True, hide_index=True, height=360)


def render_strategy_library(st_module, pd_module):
    st_module.subheader("Open Strategy Library")
    st_module.caption("The strategy catalogue is expandable. A, B and C are the initial implemented models; the remaining entries are research candidates until independently tested and approved.")
    frame = pd_module.DataFrame(STRATEGY_CATALOG, columns=["ID","Strategy","Family","Status","Research hypothesis"])
    families = ["All"] + sorted(frame["Family"].unique().tolist())
    selected_family = st_module.selectbox("Strategy family", families)
    if selected_family != "All":
        frame = frame[frame["Family"] == selected_family]
    statuses = st_module.multiselect("Show status", sorted(frame["Status"].unique()), default=sorted(frame["Status"].unique()))
    frame = frame[frame["Status"].isin(statuses)]
    st_module.metric("Strategies in catalogue", len(frame))
    st_module.dataframe(frame, use_container_width=True, hide_index=True, height=520)
    st_module.info("New strategy hypotheses can be added as independent modules. They do not become active trading strategies until data requirements, transaction-cost assumptions, out-of-sample results and risk gates are documented.")
    with st_module.expander("Strategy promotion gates"):
        st_module.markdown("""
        1. Define an unambiguous entry, exit, stop and holding horizon.
        2. Identify required source fields and reject stale or missing data.
        3. Test chronologically with no look-ahead or survivorship leakage.
        4. Include option bid/ask, slippage, brokerage, exchange charges, STT and taxes where applicable.
        5. Run walk-forward and regime-specific validation.
        6. Compare against a simple baseline and test parameter sensitivity.
        7. Paper trade prospectively before promotion.
        8. Monitor drift and automatically suspend a strategy when guardrails fail.
        """)


def render_research_reports(st_module, pd_module, bars, signals, data_label):
    st_module.subheader("Research & Model Governance")
    st_module.caption("This page separates measured evidence from hypotheses. It does not manufacture live forecasts or performance claims.")
    a,b,c = st_module.columns(3)
    a.metric("Loaded market bars", f"{len(bars):,}" if isinstance(bars, pd_module.DataFrame) else "0")
    c.metric("Signals in loaded sample", len(signals))
    b.metric("Data source", "Uploaded / Upstox" if isinstance(bars, pd_module.DataFrame) else "Not loaded")
    st_module.write("**Dataset:**", data_label)
    if isinstance(bars, pd_module.DataFrame) and not bars.empty:
        st_module.markdown("### Market data quality")
        checks = {
            "Rows": len(bars),
            "First timestamp": str(bars["timestamp"].min()) if "timestamp" in bars else "Unavailable",
            "Last timestamp": str(bars["timestamp"].max()) if "timestamp" in bars else "Unavailable",
            "Duplicate timestamps": int(bars["timestamp"].duplicated().sum()) if "timestamp" in bars else "Unavailable",
            "Missing values": int(bars.isna().sum().sum()),
            "Columns": ", ".join(map(str, bars.columns)),
        }
        st_module.dataframe(pd_module.DataFrame([{"Check":k,"Result":v} for k,v in checks.items()]), use_container_width=True, hide_index=True)
        if "timestamp" in bars and {"open","high","low","close"}.issubset(bars.columns):
            st_module.markdown("### Price and participation overview")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=bars["timestamp"], y=bars["close"], name="Close"))
            if "volume" in bars:
                fig.add_trace(go.Bar(x=bars["timestamp"], y=bars["volume"], name="Volume", yaxis="y2", opacity=0.2))
            fig.update_layout(height=430, title="Loaded market series", template="plotly_white",
                              yaxis2=dict(overlaying="y", side="right", showgrid=False))
            st_module.plotly_chart(fig, use_container_width=True)
    st_module.markdown("### Validation status")
    st_module.warning("The current underlying-candle backtest is not an option-premium backtest. No live accuracy, win-rate or profitability claim is made. Strategy promotion requires realistic option-level fills and unseen-data validation.")
    st_module.markdown("### Model governance")
    st_module.write("- Keep training, validation and test periods chronologically separate.")
    st_module.write("- Calibrate probability estimates only on data not used to fit the model.")
    st_module.write("- Track performance by strategy, expiry, volatility regime and time of day.")
    st_module.write("- Record every signal, including rejected signals, with data timestamps and reasons.")
    st_module.write("- Preserve a complete audit trail for model versions, parameters and paper fills.")
