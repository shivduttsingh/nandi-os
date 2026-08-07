from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

from nandi_v2.nse import NSEDataError, NSEPublicClient

IST = ZoneInfo("Asia/Kolkata")
TRADINGVIEW_NIFTY_URL = "https://www.tradingview.com/symbols/NSE-NIFTY/"

st.set_page_config(page_title="Nandi · TradingView Style NIFTY", page_icon="N", layout="wide")

if not st.session_state.get("logged_in", False):
    st.error("Please sign in from the Nandi home page first.")
    st.stop()

if "tv_nifty_points" not in st.session_state:
    st.session_state.tv_nifty_points = []
if "tv_nifty_error" not in st.session_state:
    st.session_state.tv_nifty_error = ""


@st.cache_resource
def nse_client() -> NSEPublicClient:
    return NSEPublicClient()


def append_point(value: float, stamp: datetime) -> None:
    aware = stamp.astimezone(IST) if stamp.tzinfo else stamp.replace(tzinfo=IST)
    epoch = int(aware.timestamp())
    points = list(st.session_state.tv_nifty_points)
    point = {"time": epoch, "value": round(float(value), 2)}
    if points and points[-1]["time"] == epoch:
        points[-1] = point
    else:
        points.append(point)
    st.session_state.tv_nifty_points = points[-1200:]


def chart_html(points: list[dict[str, float | int]]) -> str:
    data = json.dumps(points, separators=(",", ":"))
    return f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff;border:1px solid #dbe8e0;border-radius:14px;overflow:hidden;">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid #edf2ef;">
        <div><b>NIFTY 50 · NSE live spot</b><div style="font-size:12px;color:#65756d;margin-top:2px;">TradingView Lightweight Charts™ rendering · data supplied by Nandi's NSE feed</div></div>
        <div style="font-size:12px;color:#65756d;">NSE:NIFTY</div>
      </div>
      <div id="nandi-tv-chart" style="width:100%;height:560px;"></div>
      <div style="padding:7px 12px;font-size:11px;color:#65756d;border-top:1px solid #edf2ef;">Charts by TradingView Lightweight Charts™. This is not TradingView's hosted NSE widget; Nandi supplies the NSE spot data.</div>
    </div>
    <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
    <script>
      const points = {data};
      const container = document.getElementById('nandi-tv-chart');
      const chart = LightweightCharts.createChart(container, {{
        width: container.clientWidth,
        height: 560,
        layout: {{ background: {{ color: '#ffffff' }}, textColor: '#25352d', attributionLogo: true }},
        grid: {{ vertLines: {{ color: '#eef3f0' }}, horzLines: {{ color: '#eef3f0' }} }},
        rightPriceScale: {{ borderColor: '#dbe8e0' }},
        timeScale: {{ borderColor: '#dbe8e0', timeVisible: true, secondsVisible: true, rightOffset: 4, barSpacing: 8 }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        localization: {{ locale: 'en-IN' }}
      }});
      const series = chart.addLineSeries({{
        color: '#126b3a',
        lineWidth: 2,
        priceLineVisible: true,
        lastValueVisible: true,
        crosshairMarkerVisible: true
      }});
      if (points.length) {{
        series.setData(points);
        chart.timeScale().fitContent();
      }}
      const resize = () => chart.applyOptions({{ width: container.clientWidth }});
      window.addEventListener('resize', resize);
    </script>
    """


st.title("NIFTY TradingView-style live chart")
st.caption("Alternative method: TradingView's open-source Lightweight Charts™ library renders Nandi's own NSE spot feed inside the website. The official TradingView NSE:NIFTY hosted widget remains exchange-restricted.")

controls = st.columns([1, 1, 2])
if controls[0].button("Refresh now", type="primary", use_container_width=True):
    try:
        value, stamp = nse_client().fetch_nifty_spot()
        append_point(value, stamp)
        st.session_state.tv_nifty_error = ""
    except NSEDataError as exc:
        st.session_state.tv_nifty_error = str(exc)
controls[1].link_button("Open full TradingView", TRADINGVIEW_NIFTY_URL, use_container_width=True)
controls[2].caption("The page automatically requests a fresh NSE spot sample every 3 seconds and redraws the chart.")


@st.fragment(run_every="3s")
def live_chart() -> None:
    try:
        value, stamp = nse_client().fetch_nifty_spot()
        append_point(value, stamp)
        st.session_state.tv_nifty_error = ""
    except NSEDataError as exc:
        st.session_state.tv_nifty_error = str(exc)

    points = list(st.session_state.tv_nifty_points)
    if points:
        last = points[-1]
        first = points[0]
        change = float(last["value"]) - float(first["value"])
        c1, c2, c3 = st.columns(3)
        c1.metric("NIFTY", f"{float(last['value']):,.2f}", f"{change:+.2f} vs chart start")
        c2.metric("Live samples", len(points))
        c3.metric("Refresh", "3 sec")
        components.html(chart_html(points), height=650, scrolling=False)
    else:
        st.info("Waiting for the first NSE spot sample.")

    if st.session_state.tv_nifty_error:
        st.warning("Latest NSE refresh failed; the last valid chart remains visible. " + st.session_state.tv_nifty_error)


live_chart()
