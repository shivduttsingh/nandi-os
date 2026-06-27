from engine.market_engine import market_bias_engine
from engine.strategy_engine import rsi_24_78_signal
from engine.risk_engine import risk_check
from engine.nandi_brain import nandi_decision
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime

st.set_page_config(
    page_title="Nandi OS",
    layout="wide",
    initial_sidebar_state="expanded"
)

APP_PASSWORD = "nandi123"

BULL_LOGO = """
<svg class="nandi-logo" viewBox="0 0 160 160" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bullGreen" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#10d86f"/>
      <stop offset="52%" stop-color="#07883f"/>
      <stop offset="100%" stop-color="#034f28"/>
    </linearGradient>
  </defs>
  <path d="M34 22 L62 58 L49 72 L12 34 Z" fill="url(#bullGreen)"/>
  <path d="M126 22 L98 58 L111 72 L148 34 Z" fill="url(#bullGreen)"/>
  <path d="M28 31 C10 27 7 13 7 5 C29 15 45 25 59 47 Z" fill="#07983f"/>
  <path d="M132 31 C150 27 153 13 153 5 C131 15 115 25 101 47 Z" fill="#07983f"/>
  <path d="M39 59 L80 30 L121 59 L109 113 L80 146 L51 113 Z" fill="url(#bullGreen)"/>
  <path d="M55 67 L80 47 L105 67 L95 101 L80 120 L65 101 Z" fill="#045d2d" opacity="0.58"/>
  <path d="M58 80 L72 86 L63 96 Z" fill="#eafff2"/>
  <path d="M102 80 L88 86 L97 96 Z" fill="#eafff2"/>
  <path d="M70 112 L80 129 L90 112 Z" fill="#02391d"/>
</svg>
"""

CSS = """
<style>
    :root {
        --green:#07883f;
        --green2:#10b95d;
        --mint:#eefaf2;
        --line:#dfece4;
        --text:#101820;
        --muted:#65746c;
    }

    .stApp {
        background:
            radial-gradient(circle at 7% 8%, rgba(16,216,111,.16), transparent 30%),
            radial-gradient(circle at 92% 80%, rgba(7,136,63,.14), transparent 32%),
            linear-gradient(135deg, #ffffff 0%, #f7fff9 48%, #eefaf2 100%);
        color: var(--text);
    }

    .block-container {
        padding-top: 1.25rem;
        padding-bottom: 2rem;
        max-width: 1480px;
    }

    header[data-testid="stHeader"] {
        background: rgba(255,255,255,0);
    }

    section[data-testid="stSidebar"] {
        background: rgba(255,255,255,.82);
        border-right: 1px solid var(--line);
    }

    div.stButton > button,
    div[data-testid="stFormSubmitButton"] > button {
        width: 100%;
        border-radius: 14px;
        border: 1px solid #0a9348;
        background: #07883f;
        color: #fff;
        font-weight: 850;
        padding: .72rem 1rem;
        transition: .18s ease;
    }

    div.stButton > button:hover,
    div[data-testid="stFormSubmitButton"] > button:hover {
        background: #056f34;
        color: #fff;
        border-color: #056f34;
        transform: translateY(-1px);
    }

    .nandi-logo {
        width: 82px;
        height: 82px;
        filter: drop-shadow(0 14px 24px rgba(0,120,55,.22));
    }

    .brand-box {
        display: flex;
        align-items: center;
        gap: 14px;
        margin-bottom: 20px;
    }

    .brand-title {
        font-size: 25px;
        font-weight: 950;
        margin: 0;
        letter-spacing: -.6px;
    }

    .brand-subtitle {
        color: var(--muted);
        font-size: 13px;
        line-height: 1.3;
    }

    .topline {
        border-bottom: 1px solid var(--line);
        padding-bottom: 22px;
        margin-bottom: 30px;
    }

    .main-title {
        font-size: 46px;
        font-weight: 950;
        letter-spacing: -1.6px;
        margin: 0 0 6px;
        color: var(--text);
        line-height: 1.05;
    }

    .hero-title {
        font-size: 42px;
        line-height: 1.08;
        font-weight: 950;
        letter-spacing: -1.3px;
        margin: 22px 0 0;
    }

    .hero-title span {
        color: var(--green);
    }

    .subtitle,
    .hero-copy {
        color: var(--muted);
        font-size: 16px;
        line-height: 1.6;
        margin: 12px 0 22px;
    }

    .hero-copy {
        font-size: 17px;
        max-width: 620px;
    }

    .pill {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 7px 13px;
        border-radius: 999px;
        background: #e7f8ee;
        border: 1px solid #cdebd8;
        color: #08783c;
        font-size: 13px;
        font-weight: 850;
    }

    .dot {
        height: 8px;
        width: 8px;
        border-radius: 999px;
        background: #0dae55;
        display: inline-block;
        box-shadow: 0 0 0 5px rgba(13,174,85,.12);
    }

    .card,
    .mini-card {
        background: rgba(255,255,255,.88);
        border: 1px solid var(--line);
        border-radius: 24px;
        padding: 22px;
        box-shadow: 0 12px 42px rgba(7,90,44,.08);
        min-height: 215px;
        overflow: hidden;
    }

    .mini-card {
        border-radius: 20px;
        padding: 16px;
        min-height: auto;
    }

    .card-title {
        font-size: 18px;
        font-weight: 900;
        color: #142017;
        margin-bottom: 12px;
    }

    .muted {
        color: var(--muted);
        font-size: 13px;
    }

    .big-green {
        color: var(--green);
        font-size: 30px;
        font-weight: 950;
        letter-spacing: -.7px;
    }

    .green {
        color: var(--green);
        font-weight: 850;
    }

    .red {
        color: #d93636;
        font-weight: 850;
    }

    .orange {
        color: #c68100;
        font-weight: 850;
    }

    .row {
        display: flex;
        justify-content: space-between;
        gap: 10px;
        font-size: 13px;
        margin-top: 9px;
        color: var(--muted);
    }

    .tag {
        display: inline-block;
        border-radius: 999px;
        padding: 4px 10px;
        font-size: 11px;
        font-weight: 850;
        background: #e6f8ed;
        color: #08783c;
    }

    .tag-red {
        background: #fff0f0;
        color: #cf2e2e;
    }

    .tag-orange {
        background: #fff7df;
        color: #ad7400;
    }

    .progress-track {
        height: 8px;
        border-radius: 999px;
        background: #e9eee9;
        overflow: hidden;
    }

    .progress-fill {
        height: 100%;
        border-radius: 999px;
        background: linear-gradient(90deg, #07983f, #10d86f);
    }

    .gauge {
        width: 126px;
        height: 126px;
        border-radius: 50%;
        background: conic-gradient(#07983f 0deg 266deg, #e8eee9 266deg 360deg);
        display: flex;
        align-items: center;
        justify-content: center;
        margin: auto;
        box-shadow: inset 0 0 0 12px #f5fff8;
    }

    .gauge-inner {
        width: 88px;
        height: 88px;
        border-radius: 50%;
        background: #fff;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        border: 1px solid #dcefe4;
    }

    .gauge-number {
        font-size: 31px;
        font-weight: 950;
        color: var(--green);
        line-height: 1;
    }

    div[data-testid="stForm"] {
        background: rgba(255,255,255,.92);
        border: 1px solid var(--line);
        border-radius: 30px;
        padding: 35px;
        box-shadow: 0 18px 60px rgba(7,90,44,.12);
    }

    .scenario {
        border: 1px solid var(--line);
        background: rgba(248,255,250,.92);
        border-radius: 16px;
        padding: 14px;
        margin-bottom: 10px;
    }

    .nav-note {
        background: rgba(255,255,255,.82);
        border: 1px solid var(--line);
        border-radius: 20px;
        padding: 15px;
        margin-top: 18px;
        font-size: 13px;
        color: #33443a;
    }

    .ticker-grid {
        display: grid;
        grid-template-columns: repeat(7, minmax(130px, 1fr));
        gap: 12px;
    }

    .ticker-card {
        background: rgba(255,255,255,.9);
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 14px;
    }

    .ticker-name {
        font-size: 12px;
        font-weight: 900;
    }

    .ticker-price {
        font-size: 18px;
        font-weight: 950;
        color: var(--green);
    }

    @media(max-width:1100px) {
        .ticker-grid {
            grid-template-columns: repeat(2, 1fr);
        }

        .main-title {
            font-size: 34px;
        }

        .hero-title {
            font-size: 34px;
        }
    }
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "page" not in st.session_state:
    st.session_state.page = "Command Center"

if "username" not in st.session_state:
    st.session_state.username = "Shivdutt"

if "watchlist" not in st.session_state:
    st.session_state.watchlist = [
        "NIFTY 50",
        "BANK NIFTY",
        "FINNIFTY",
        "INDIA VIX",
        "USD/INR",
        "GOLD",
        "CRUDE OIL",
    ]


def rerun():
    st.rerun()


@st.cache_data
def make_series(seed=10, points=80, start=24600):
    rng = np.random.default_rng(seed)
    trend = np.linspace(0, 260, points)
    noise = np.cumsum(rng.normal(0, 22, points))
    return start + trend + noise


def sparkline(values, color="#07983f", width=260, height=88):
    values = np.array(values, dtype=float)
    vmin, vmax = values.min(), values.max()
    span = max(vmax - vmin, 1e-9)

    xs = np.linspace(8, width - 8, len(values))
    ys = height - 10 - ((values - vmin) / span) * (height - 20)

    pts = " ".join([f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys)])

    return f"""
    <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" xmlns="http://www.w3.org/2000/svg">
        <polyline fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="{pts}"/>
    </svg>
    """


def candle_chart(values, width=620, height=255):
    values = np.array(values[-34:], dtype=float)

    rng = np.random.default_rng(7)
    opens = values + rng.normal(0, 22, len(values))
    highs = np.maximum(opens, values) + rng.uniform(12, 38, len(values))
    lows = np.minimum(opens, values) - rng.uniform(12, 38, len(values))

    pmin, pmax = lows.min(), highs.max()
    span = max(pmax - pmin, 1e-9)

    def y(price):
        return height - 25 - ((price - pmin) / span) * (height - 45)

    step = width / len(values)
    candles = ""

    for i in range(len(values)):
        x = i * step + step / 2
        yo = y(opens[i])
        yc = y(values[i])
        yh = y(highs[i])
        yl = y(lows[i])

        up = values[i] >= opens[i]
        color = "#07983f" if up else "#e14646"
        body_y = min(yo, yc)
        body_h = max(abs(yo - yc), 4)

        candles += f"""
        <line x1="{x:.1f}" y1="{yh:.1f}" x2="{x:.1f}" y2="{yl:.1f}" stroke="{color}" stroke-width="2"/>
        <rect x="{x-4:.1f}" y="{body_y:.1f}" width="8" height="{body_h:.1f}" rx="2" fill="{color}"/>
        """

    return f"""
    <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" xmlns="http://www.w3.org/2000/svg">
        <rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#fbfffc"/>
        <g opacity="0.65">
            <line x1="0" y1="55" x2="{width}" y2="55" stroke="#e6eee9"/>
            <line x1="0" y1="105" x2="{width}" y2="105" stroke="#e6eee9"/>
            <line x1="0" y1="155" x2="{width}" y2="155" stroke="#e6eee9"/>
            <line x1="0" y1="205" x2="{width}" y2="205" stroke="#e6eee9"/>
        </g>
        <rect x="270" y="132" width="250" height="52" rx="9" fill="#dbf5e5" opacity="0.85"/>
        {candles}
        <rect x="{width-98}" y="92" width="90" height="25" rx="6" fill="#07983f"/>
        <text x="{width-53}" y="109" text-anchor="middle" fill="white" font-size="13" font-weight="800">24,926.15</text>
        <text x="20" y="{height-10}" fill="#78867e" font-size="12">10:00</text>
        <text x="230" y="{height-10}" fill="#78867e" font-size="12">12:00</text>
        <text x="440" y="{height-10}" fill="#78867e" font-size="12">14:00</text>
    </svg>
    """


def login_page():
    prices = make_series()

    st.markdown(
        f"""
        <div class="topline">
            <div class="brand-box">
                {BULL_LOGO}
                <div>
                    <div class="brand-title">Nandi OS</div>
                    <div class="brand-subtitle">Private AI Finance Platform</div>
                </div>
                <div style="margin-left:auto;">
                    <span class="pill"><span class="dot"></span> All Systems Operational</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.08, 1], gap="large")

    with left:
        st.markdown(
            f"""
            <div class="hero-title">
                AI-Powered Research.<br>
                <span>Smarter Decisions.</span>
            </div>
            <div class="hero-copy">
                Market intelligence, research notes, strategy testing, watchlist,
                private memory, and daily planning inside one clean workspace.
            </div>

            <div class="card">
                <div class="card-title">
                    Market Pulse <span class="tag" style="float:right;">Live</span>
                </div>
                <div class="muted">NIFTY 50</div>
                <div class="big-green">24,926.15</div>
                <div class="green">+196.35 (+0.79%)</div>
                {sparkline(prices[-55:])}
                <div class="row">
                    <span>High 24,951.80</span>
                    <span>Low 24,684.10</span>
                    <span>Adv <b class="green">1,638</b></span>
                    <span>Dec <b class="red">742</b></span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.write("")

        st.markdown(
            f"""
            <div class="card">
                <div class="card-title">
                    Finance Research Preview <span class="tag" style="float:right;">NIFTY 15m</span>
                </div>
                {candle_chart(prices)}
                <div class="row">
                    <span>Trend: <b class="green">Bullish</b></span>
                    <span>Momentum: <b class="green">Strong</b></span>
                    <span>Risk: <b class="orange">Medium</b></span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        st.markdown(
            f"""
            <div style="text-align:center;margin-bottom:18px;">
                {BULL_LOGO}
                <h1 style="margin:6px 0 4px 0;font-size:34px;letter-spacing:-1px;">
                    Welcome back 👋
                </h1>
                <div class="muted">Sign in to access your private Nandi workspace</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.form("login"):
            username = st.text_input("Email or Username", placeholder="Enter email or username")
            password = st.text_input("Password", placeholder="Enter password", type="password")
            st.checkbox("Remember me")

            if st.form_submit_button("🔐 Sign In"):
                if username.strip() and password == APP_PASSWORD:
                    st.session_state.logged_in = True
                    st.session_state.username = username.split("@")[0].title()
                    rerun()
                else:
                    st.error("Use password: nandi123")

            st.markdown(
                """
                <div style="text-align:center;color:#65746c;font-size:13px;margin-top:18px;">
                    🛡 Private Workspace | AI Research Mode | Secure Access
                </div>
                """,
                unsafe_allow_html=True,
            )


def sidebar():
    with st.sidebar:
        st.markdown(
            f"""
            <div class="brand-box">
                {BULL_LOGO}
                <div>
                    <div class="brand-title">Nandi OS</div>
                    <div class="brand-subtitle">Private AI<br>Finance Platform</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        pages = [
            "Command Center",
            "Finance Research",
            "Nandi Chat",
            "Memory Core",
            "Goals",
            "Daily Updates",
            "Strategy Lab",
            "Watchlist",
            "Settings",
        ]

        for page in pages:
            if st.button(page):
                st.session_state.page = page
                rerun()

        st.markdown(
            """
            <div class="nav-note">
                <b>System Status</b><br><br>
                <div class="row"><span>Model Engine</span><b class="green">Online</b></div>
                <div class="row"><span>Data Feeds</span><b class="green">Live</b></div>
                <div class="row"><span>Memory</span><b class="green">Active</b></div>
                <div class="row"><span>Last sync</span><b>09:30 IST</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("Logout"):
            st.session_state.logged_in = False
            rerun()


def command_center():
    prices = make_series()

    top_left, top_right = st.columns([2.4, 1])

    with top_left:
        st.text_input(
            "Search",
            placeholder="Search markets, research, tickers, or ideas...",
            label_visibility="collapsed",
        )

    with top_right:
        st.markdown(
            f"""
            <div style="display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;">
                <span class="pill">☀ Theme</span>
                <span class="pill">🔔 3</span>
                <span class="pill">👤 {st.session_state.username}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        f"""
        <div style="margin-top:22px;margin-bottom:22px;">
            <div class="muted">Good morning, {st.session_state.username} 👋</div>
            <div class="main-title">Financial Research Command Center</div>
            <div class="subtitle">AI-powered research, strategy analysis, and market intelligence.</div>
            <span class="pill"><span class="dot"></span> All Systems Active</span>
            <span class="pill">📅 {datetime.now().strftime("%d %b %Y")} · {datetime.now().strftime("%I:%M %p")} IST</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4, gap="large")

    with c1:
        st.markdown(
            f"""
            <div class="card">
                <div class="card-title">
                    Market Pulse <span class="tag" style="float:right;">Live</span>
                </div>
                <div class="muted">NIFTY 50</div>
                <div class="big-green">24,926.15</div>
                <div class="green">+196.35 (+0.79%)</div>
                {sparkline(prices[-55:])}
                <div class="row"><span>High</span><b>24,951.80</b></div>
                <div class="row"><span>Low</span><b>24,684.10</b></div>
                <div class="row"><span>Adv</span><b class="green">1,638</b></div>
                <div class="row"><span>Dec</span><b class="red">742</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c2:
        st.markdown(
            """
            <div class="card">
                <div class="card-title">
                    Research Engine <span class="tag" style="float:right;">Active</span>
                </div>
                <div class="gauge">
                    <div class="gauge-inner">
                        <div class="gauge-number">74</div>
                        <div class="muted">/100</div>
                    </div>
                </div>
                <div class="row"><span>Bullish Setups</span><b class="green">18</b></div>
                <div class="row"><span>Bearish Setups</span><b class="red">6</b></div>
                <div class="row"><span>Neutral</span><b>4</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c3:
        st.markdown(
            """
            <div class="card">
                <div class="card-title">
                    Memory Core <span class="tag" style="float:right;">Learning</span>
                </div>
                <div class="row"><span>Patterns Tracked</span><b>18,642</b></div>
                <div class="row"><span>Scenarios Stored</span><b>1,248</b></div>
                <div class="row"><span>Models Evolved</span><b>23</b></div>
                <div class="row"><span>Active Memory</span><b class="green">98.7%</b></div>
                <br>
                <div class="progress-track">
                    <div class="progress-fill" style="width:98%;"></div>
                </div>
                <br>
                <div class="muted">Last update: 09:28 AM IST</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c4:
        st.markdown(
            """
            <div class="card">
                <div class="card-title">
                    Today's Focus <span class="tag" style="float:right;">60%</span>
                </div>
                <div class="row"><span>✅ NIFTY Structure Analysis</span></div>
                <div class="row"><span>✅ Bank Nifty Scenario Study</span></div>
                <div class="row"><span>✅ Global Market Correlation</span></div>
                <div class="row"><span>○ Sector Rotation Scan</span></div>
                <div class="row"><span>○ Research Watchlist</span></div>
                <br>
                <div class="progress-track">
                    <div class="progress-fill" style="width:60%;"></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")

    left, right = st.columns([1.25, 1], gap="large")

    with left:
        st.markdown(
            f"""
            <div class="card">
                <div class="card-title">
                    Finance Research Panel
                    <span class="tag" style="float:right;">NIFTY 50 · 15m</span>
                </div>

                <div style="display:grid;grid-template-columns:210px 1fr;gap:16px;">
                    <div>
                        <div class="scenario">
                            <b>Primary Scenario</b> <span class="tag">Bullish</span><br>
                            <span class="muted">Continuation above 24,700</span><br>
                            <b>Target 25,200 – 25,500</b>
                        </div>
                        <div class="scenario">
                            <b>Alternate Scenario</b> <span class="tag tag-orange">Neutral</span><br>
                            <span class="muted">Range bound 24,400 – 25,200</span>
                        </div>
                        <div class="scenario">
                            <b>Risk Scenario</b> <span class="tag tag-red">Bearish</span><br>
                            <span class="muted">Breakdown below 24,400</span><br>
                            <b>Target 24,000 – 23,800</b>
                        </div>
                    </div>

                    <div>
                        {candle_chart(prices)}
                    </div>
                </div>

                <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:14px;">
                    <span class="pill">Trend: Bullish</span>
                    <span class="pill">Momentum: Strong</span>
                    <span class="pill">Volume: Above Avg</span>
                    <span class="pill">Risk: Medium</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        st.markdown(
            """
            <div class="card">
                <div class="card-title">
                    Paper Journal / Backtests <span class="tag" style="float:right;">View All</span>
                </div>

                <table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <tr>
                        <th align="left">Title</th>
                        <th align="left">Type</th>
                        <th align="left">Result</th>
                        <th align="left">Score</th>
                    </tr>
                    <tr>
                        <td>NIFTY Breakout Study</td>
                        <td>Backtest</td>
                        <td class="green">+1.34%</td>
                        <td><span class="tag">78/100</span></td>
                    </tr>
                    <tr>
                        <td>Bank Nifty Call Study</td>
                        <td>Backtest</td>
                        <td class="green">+0.92%</td>
                        <td><span class="tag">72/100</span></td>
                    </tr>
                    <tr>
                        <td>Opening Range Momentum</td>
                        <td>Journal</td>
                        <td class="green">+0.68%</td>
                        <td><span class="tag">65/100</span></td>
                    </tr>
                    <tr>
                        <td>Midcap Rotation Scan</td>
                        <td>Backtest</td>
                        <td class="red">-0.21%</td>
                        <td><span class="tag tag-red">52/100</span></td>
                    </tr>
                    <tr>
                        <td>Global Cues Impact</td>
                        <td>Journal</td>
                        <td class="green">+0.44%</td>
                        <td><span class="tag">60/100</span></td>
                    </tr>
                </table>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")

    tickers = [
        ("NIFTY 50", "24,926.15", "+196.35 (+0.79%)", "#07983f", 1),
        ("BANK NIFTY", "55,215.80", "+412.10 (+0.75%)", "#07983f", 2),
        ("FINNIFTY", "26,102.45", "+218.95 (+0.85%)", "#07983f", 3),
        ("INDIA VIX", "13.24", "-0.28 (-2.07%)", "#e14646", 4),
        ("USD/INR", "83.46", "-0.08 (-0.10%)", "#e14646", 5),
        ("GOLD", "73,820", "+512 (+0.70%)", "#07983f", 6),
        ("CRUDE OIL", "64.15", "-0.18 (-0.28%)", "#e14646", 7),
    ]

    html = """
    <div class="mini-card">
        <div class="card-title">
            Market Watch <span class="tag" style="float:right;">Customize</span>
        </div>
        <div class="ticker-grid">
    """

    for name, price, move, color, seed in tickers:
        move_class = "green" if "+" in move else "red"
        html += f"""
        <div class="ticker-card">
            <div class="ticker-name">{name}</div>
            <div class="ticker-price" style="color:{color};">{price}</div>
            <div class="{move_class}" style="font-size:12px;">{move}</div>
            {sparkline(make_series(seed=seed, points=28, start=100), color=color, width=140, height=45)}
        </div>
        """

    html += """
        </div>
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


def simple_page(title, subtitle):
    st.markdown(
        f"""
        <div class="main-title">{title}</div>
        <div class="subtitle">{subtitle}</div>
        """,
        unsafe_allow_html=True,
    )


def finance_research():
    simple_page("Finance Research", "Scenario planning, market structure, and research notes.")

    c1, c2 = st.columns(2, gap="large")

    with c1:
        st.markdown(
            """
            <div class="card">
                <div class="card-title">Research Setup</div>
                <div class="scenario"><b>NIFTY:</b> Bullish above 24,700. Risk below 24,400.</div>
                <div class="scenario"><b>BANK NIFTY:</b> Watch call-side buildup and first 15-minute range.</div>
                <div class="scenario"><b>Global Cues:</b> Track US futures, DXY, crude, gold, and VIX.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c2:
        st.markdown(
            """
            <div class="card">
                <div class="card-title">Research Output</div>
                <span class="pill">Bullish Setup Detected</span><br><br>
                <div class="muted">Confidence Score</div>
                <div class="big-green">74 / 100</div>
                <div class="progress-track">
                    <div class="progress-fill" style="width:74%;"></div>
                </div>
                <br>
                <div class="muted">Research output only. Not financial advice.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def nandi_chat():
    simple_page("Nandi Chat", "Private AI chat area. Full AI API connection will be added after UI is stable.")

    message = st.chat_input("Ask Nandi...")

    if message:
        with st.chat_message("user"):
            st.write(message)

        with st.chat_message("assistant"):
            st.write("Nandi is ready. Next we will connect real AI model, memory, and market tools.")


def memory_core():
    simple_page("Memory Core", "Pattern memory, scenario storage, and learning status.")

    c1, c2, c3 = st.columns(3)

    c1.metric("Patterns Tracked", "18,642", "+214")
    c2.metric("Scenarios Stored", "1,248", "+31")
    c3.metric("Active Memory", "98.7%", "+0.4%")

    st.progress(0.987)


def goals():
    simple_page("Goals", "Track your capital growth and Nandi build roadmap.")

    st.checkbox("Build Nandi OS fresh UI")
    st.checkbox("Add login")
    st.checkbox("Add real market data")
    st.checkbox("Add option chain / OI logic")
    st.checkbox("Add AI chat and memory")


def daily_updates():
    simple_page("Daily Updates", "Morning checklist and market preparation.")

    items = [
        "Check global market sentiment",
        "Check GIFT NIFTY",
        "Mark previous day high and low",
        "Mark first 15-minute range",
        "Check option chain OI buildup",
        "Avoid forced trade",
        "Protect capital first",
    ]

    for item in items:
        st.checkbox(item)

    st.text_area("Today’s notes")


def strategy_lab():
    simple_page("Strategy Lab", "Upload CSV and test RSI 24/78 setup.")

    uploaded = st.file_uploader("Upload CSV with Close column", type=["csv"])

    if uploaded:
        df = pd.read_csv(uploaded)

        if "Close" not in df.columns:
            st.error("CSV must contain Close column.")
            return

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()

        rs = avg_gain / avg_loss
        df["RSI"] = 100 - (100 / (1 + rs))

        df["Signal"] = "Hold"
        df.loc[df["RSI"] <= 24, "Signal"] = "Buy Zone"
        df.loc[df["RSI"] >= 78, "Signal"] = "Exit Zone"

        st.dataframe(df.tail(80), use_container_width=True)
        st.line_chart(df[["Close", "RSI"]])
    else:
        st.info("Upload your CSV file to test the strategy.")


def watchlist():
    simple_page("Watchlist", "Manage your personal market watchlist.")

    new_symbol = st.text_input("Add symbol", placeholder="Example: TCS, SBIN, NIFTY")

    if st.button("Add to Watchlist") and new_symbol.strip():
        st.session_state.watchlist.append(new_symbol.strip().upper())
        rerun()

    for symbol in st.session_state.watchlist:
        st.markdown(
            f"""
            <div class="mini-card" style="margin-bottom:10px;">
                <b>☆ {symbol}</b>
                <span style="float:right;" class="green">Active</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

def nandi_decision_engine_page():
    simple_page("Nandi Decision Engine", "Market bias + strategy signal + risk check = final Nandi action.")

    col1, col2, col3 = st.columns(3)

    with col1:
        price = st.number_input("Current Price", value=24900.0)
        support = st.number_input("Support", value=24700.0)
        resistance = st.number_input("Resistance", value=25000.0)

    with col2:
        strategy_signal = st.selectbox("Strategy Signal", ["Buy", "Sell", "No Signal"])
        capital = st.number_input("Capital", value=25000.0)

    with col3:
        max_risk = st.number_input("Max Risk %", value=2.0)
        stop_loss_points = st.number_input("Stop Loss Points", value=40.0)
        lot_size = st.number_input("Lot Size", value=1)

    market = market_bias_engine(price, support, resistance)
    risk = risk_check(capital, max_risk, stop_loss_points, lot_size)

    final = nandi_decision(
        market["bias"],
        strategy_signal,
        risk["status"]
    )

    st.subheader("Final Nandi Decision")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Action", final["action"])
    c2.metric("Confidence", f'{final["confidence"]}%')
    c3.metric("Market Bias", market["bias"])
    c4.metric("Risk Status", risk["status"])

    st.write("### Reasons")
    for reason in final["reasons"]:
        st.write(f"✅ {reason}")

    st.info("Research support only. Not guaranteed profit or financial advice.")
def settings():
    simple_page("Settings", "Control Nandi OS preferences.")

    st.text_input("Display Name", value=st.session_state.username)
    st.selectbox("Theme", ["Premium Mint", "Clean White", "Deep Green"])
    st.selectbox("Mode", ["Research Mode", "Trading Mode", "Personal AI Mode"])
    st.warning("Current login is demo/local only. Later we will add secure password hashing.")


if not st.session_state.logged_in:
    login_page()
else:
    sidebar()

    pages = {
        "Command Center": command_center,
        "Finance Research": finance_research,
        "Nandi Chat": nandi_chat,
        "Memory Core": memory_core,
        "Goals": goals,
        "Daily Updates": daily_updates,
        "Strategy Lab": strategy_lab,
        "Watchlist": watchlist,
        "Settings": settings,
    }

    pages.get(st.session_state.page, command_center)()
