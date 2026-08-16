from __future__ import annotations

import json
from datetime import datetime, timedelta
from html import escape
from typing import Iterable
from zoneinfo import ZoneInfo

from nandi_oi.models import IntradayCandle


IST = ZoneInfo("Asia/Kolkata")


def tradingview_advanced_chart_html() -> str:
    """Embed TradingView's hosted Advanced Chart for the NSE NIFTY index."""
    config = {
        "autosize": True,
        "symbol": "NSE:NIFTY",
        "interval": "15",
        "timezone": "Asia/Kolkata",
        "theme": "light",
        "style": "1",
        "locale": "en",
        "backgroundColor": "#ffffff",
        "gridColor": "rgba(219, 232, 224, 0.45)",
        "allow_symbol_change": True,
        "calendar": False,
        "details": True,
        "hide_side_toolbar": False,
        "hide_top_toolbar": False,
        "hide_legend": False,
        "hide_volume": False,
        "hotlist": False,
        "save_image": True,
        "withdateranges": True,
        "support_host": "https://www.tradingview.com",
    }
    settings = json.dumps(config, separators=(",", ":"))
    return f"""
    <div class="tradingview-widget-container" style="height:640px;width:100%;">
      <div class="tradingview-widget-container__widget" style="height:calc(100% - 32px);width:100%;"></div>
      <div class="tradingview-widget-copyright" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:12px;padding-top:8px;">
        <a href="https://www.tradingview.com/symbols/NSE-NIFTY/" rel="noopener nofollow" target="_blank"><span style="color:#126b3a;">NIFTY 50 chart</span></a><span style="color:#65756d;"> by TradingView</span>
      </div>
      <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
      {settings}
      </script>
    </div>
    """


def merge_candles(*groups: Iterable[IntradayCandle]) -> tuple[IntradayCandle, ...]:
    """Merge candle groups by timestamp; later groups replace earlier duplicates."""
    merged: dict[datetime, IntradayCandle] = {}
    for group in groups:
        for candle in group:
            merged[candle.timestamp] = candle
    return tuple(merged[timestamp] for timestamp in sorted(merged))


def completed_candles(
    candles: Iterable[IntradayCandle], observed_at: datetime, interval_minutes: int,
) -> tuple[IntradayCandle, ...]:
    """Exclude the still-forming Upstox candle from decision evidence."""
    observed = observed_at.astimezone(IST).replace(tzinfo=None) if observed_at.tzinfo else observed_at
    return tuple(
        candle for candle in sorted(candles, key=lambda item: item.timestamp)
        if candle.timestamp + timedelta(minutes=interval_minutes) <= observed
    )


def candlestick_chart_html(
    candles: Iterable[IntradayCandle],
    *,
    interval_minutes: int = 15,
    title: str = "NIFTY 50",
    subtitle: str = "TradingView Lightweight Charts™ rendering · read-only Upstox V3 OHLC data",
    evidence_note: str = "The chart is visual evidence. Nandi's market-structure context uses completed candles; the forming candle remains display-only.",
    chart_height: int = 540,
) -> str:
    """Render Upstox OHLC data with TradingView Lightweight Charts."""
    if chart_height < 240:
        raise ValueError("Chart height must be at least 240 pixels")
    points = []
    for candle in sorted(candles, key=lambda item: item.timestamp):
        aware = candle.timestamp.replace(tzinfo=IST) if candle.timestamp.tzinfo is None else candle.timestamp.astimezone(IST)
        points.append({
            "time": int(aware.timestamp()),
            "open": round(candle.open, 2),
            "high": round(candle.high, 2),
            "low": round(candle.low, 2),
            "close": round(candle.close, 2),
        })
    data = json.dumps(points, separators=(",", ":"))
    safe_title = escape(title)
    safe_subtitle = escape(subtitle)
    safe_note = escape(evidence_note)
    return f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff;border:1px solid #dbe8e0;border-radius:14px;overflow:hidden;">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid #edf2ef;">
        <div><b>{safe_title} · {interval_minutes}-minute candles</b><div style="font-size:12px;color:#65756d;margin-top:2px;">{safe_subtitle}</div></div>
        <div style="font-size:12px;color:#65756d;">UPSTOX · READ ONLY</div>
      </div>
      <div id="nandi-upstox-candles" style="width:100%;height:{chart_height}px;"></div>
      <div style="padding:7px 12px;font-size:11px;color:#65756d;border-top:1px solid #edf2ef;">{safe_note}</div>
    </div>
    <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
    <script>
      const candles = {data};
      const container = document.getElementById('nandi-upstox-candles');
      const chart = LightweightCharts.createChart(container, {{
        width: container.clientWidth,
        height: {chart_height},
        layout: {{ background: {{ color: '#ffffff' }}, textColor: '#25352d', attributionLogo: true }},
        grid: {{ vertLines: {{ color: '#eef3f0' }}, horzLines: {{ color: '#eef3f0' }} }},
        rightPriceScale: {{ borderColor: '#dbe8e0' }},
        timeScale: {{ borderColor: '#dbe8e0', timeVisible: true, secondsVisible: false, rightOffset: 3, barSpacing: 16 }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        localization: {{ locale: 'en-IN' }}
      }});
      const series = chart.addCandlestickSeries({{
        upColor: '#126b3a', downColor: '#c13f32', borderVisible: false,
        wickUpColor: '#126b3a', wickDownColor: '#c13f32',
        priceLineVisible: true, lastValueVisible: true
      }});
      if (candles.length) {{ series.setData(candles); chart.timeScale().fitContent(); }}
      const resize = () => chart.applyOptions({{ width: container.clientWidth }});
      window.addEventListener('resize', resize);
    </script>
    """
