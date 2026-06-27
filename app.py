from datetime import datetime
from typing import Dict, Callable
from urllib.parse import quote

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from engine.market_engine import market_bias_engine
from engine.strategy_engine import rsi_24_78_signal
from engine.risk_engine import risk_check
from engine.nandi_brain import nandi_decision
from engine.universe_engine import load_master_universe, search_universe, get_universe_stats


st.set_page_config(
    page_title="Nandi OS",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_USERNAME = st.secrets["auth"]["username"]
APP_PASSWORD = st.secrets["auth"]["password"]


st.markdown(
    """
    <style>
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(19, 181, 90, 0.13), transparent 32%),
                radial-gradient(circle at bottom right, rgba(4, 132, 65, 0.12), transparent 30%),
                linear-gradient(135deg, #ffffff 0%, #f8fff9 45%, #eefaf2 100%);
        }

        header[data-testid="stHeader"] {
            background: rgba(255,255,255,0);
        }

        .block-container {
            max-width: 1450px;
            padding-top: 1.3rem;
            padding-bottom: 2rem;
        }

        section[data-testid="stSidebar"] {
            background: rgba(255, 255, 255, 0.94);
            border-right: 1px solid #dfece4;
        }

        div[data-testid="stMetric"] {
            background: rgba(255,255,255,0.84);
            border: 1px solid #dfece4;
            border-radius: 18px;
            padding: 16px;
            box-shadow: 0 8px 28px rgba(7, 90, 44, 0.06);
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: rgba(255,255,255,0.88);
            border-radius: 22px;
            box-shadow: 0 10px 34px rgba(7, 90, 44, 0.06);
        }

        div.stButton > button {
            border-radius: 12px;
            font-weight: 700;
        }

        div[data-testid="stFormSubmitButton"] > button {
            border-radius: 12px;
            background: #07883f;
            color: white;
            font-weight: 800;
            border: 1px solid #07883f;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "page" not in st.session_state:
    st.session_state.page = "Command Center"

if "username" not in st.session_state:
    st.session_state.username = "Shiv"

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

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


def clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in ["nan", "none", "nat"]:
        return ""
    return text


@st.cache_data
def make_series(seed: int = 10, points: int = 80, start: float = 24600) -> pd.Series:
    rng = np.random.default_rng(seed)
    trend = np.linspace(0, 260, points)
    noise = np.cumsum(rng.normal(0, 22, points))
    return pd.Series(start + trend + noise)


def demo_market_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["NIFTY 50", "24,926.15", "+196.35", "+0.79%", "Bullish"],
            ["BANK NIFTY", "55,215.80", "+412.10", "+0.75%", "Bullish"],
            ["FINNIFTY", "26,102.45", "+218.95", "+0.85%", "Bullish"],
            ["INDIA VIX", "13.24", "-0.28", "-2.07%", "Cooling"],
            ["USD/INR", "83.46", "-0.08", "-0.10%", "Neutral"],
            ["GOLD", "73,820", "+512", "+0.70%", "Bullish"],
            ["CRUDE OIL", "64.15", "-0.18", "-0.28%", "Neutral"],
        ],
        columns=["Instrument", "Price", "Change", "% Change", "Status"],
    )


def research_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["Primary", "Bullish", "Above 24,700", "25,200 - 25,500", "Valid above breakout zone"],
            ["Alternate", "Neutral", "24,400 - 25,200", "Range bound", "Wait for range expansion"],
            ["Risk", "Bearish", "Below 24,400", "24,000 - 23,800", "Avoid longs below support"],
        ],
        columns=["Scenario", "Bias", "Level", "Target", "Nandi Note"],
    )


def backtest_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["NIFTY Breakout Study", "Backtest", "+1.34%", "78/100"],
            ["Bank Nifty Call Study", "Backtest", "+0.92%", "72/100"],
            ["Opening Range Momentum", "Journal", "+0.68%", "65/100"],
            ["Midcap Rotation Scan", "Backtest", "-0.21%", "52/100"],
            ["Global Cues Impact", "Journal", "+0.44%", "60/100"],
        ],
        columns=["Title", "Type", "Result", "Score"],
    )


def page_title(title: str, subtitle: str = "") -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def login_page() -> None:
    st.title("Nandi OS")
    st.caption("Private AI Finance Platform")

    left, right = st.columns([1.18, 1], gap="large")

    with left:
        st.header("AI-Powered Research.")
        st.subheader("Smarter Decisions.")
        st.write(
            "A clean private workspace for market intelligence, strategy testing, "
            "watchlist tracking, risk control, and Nandi decision support."
        )

        c1, c2 = st.columns(2)

        with c1:
            with st.container(border=True):
                st.subheader("Market Pulse")
                st.metric("NIFTY 50", "24,926.15", "+196.35 (+0.79%)")
                st.line_chart(make_series(seed=1, points=45), height=190)

        with c2:
            with st.container(border=True):
                st.subheader("Research Engine")
                st.metric("Confidence", "74/100", "Active")
                st.progress(0.74)
                st.write("Bullish setups: 18")
                st.write("Bearish setups: 6")
                st.write("Neutral setups: 4")

        with st.container(border=True):
            st.subheader("Finance Research Preview")
            st.dataframe(research_table(), use_container_width=True, hide_index=True)

    with right:
        with st.container(border=True):
            st.header("Welcome back 👋")
            st.write("Sign in to access your private Nandi workspace.")

            with st.form("login_form"):
                username = st.text_input("Email or Username")
                password = st.text_input("Password", type="password")
                st.checkbox("Remember me")
                submitted = st.form_submit_button("Sign In", use_container_width=True)

            if submitted:
                if username.strip() == APP_USERNAME and password == APP_PASSWORD:
                    st.session_state.logged_in = True
                    st.session_state.username = username.split("@")[0].title()
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

            st.info("Private Workspace | AI Research Mode | Secure Access")


def sidebar() -> None:
    with st.sidebar:
        st.title("Nandi OS")
        st.caption("Private AI Finance Platform")
        st.success("All Systems Active")

        pages = [
            "Command Center",
            "Universe Engine",
            "TradingView Live Chart",
            "Finance Research",
            "Nandi CEO",
            "Nandi Chat",
            "Memory Core",
            "Goals",
            "Daily Updates",
            "Strategy Lab",
            "Watchlist",
            "Nandi Decision Engine",
            "Settings",
        ]

        for page in pages:
            if st.button(page, use_container_width=True):
                st.session_state.page = page
                st.rerun()

        st.divider()
        st.write("System Status")
        st.write("Model Engine: Online")
        st.write("Data Feeds: Foundation Mode")
        st.write("Universe: Auto-Refresh")
        st.write("Memory: Active")
        st.write("Last Sync: 09:30 IST")

        st.divider()
        if st.button("Logout", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()


def command_center() -> None:
    st.text_input(
        "Search",
        placeholder="Search markets, research, tickers, strategies, or notes...",
        label_visibility="collapsed",
    )

    st.caption(f"Good morning, {st.session_state.username} 👋")
    page_title(
        "Financial Research Command Center",
        "AI-powered research, strategy analysis, and market intelligence.",
    )

    s1, s2, s3 = st.columns(3)
    s1.success("All Systems Active")
    s2.info(datetime.now().strftime("%d %b %Y · %I:%M %p IST"))
    s3.warning("Data Mode: Foundation Build")

    st.divider()

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        with st.container(border=True):
            st.subheader("Market Pulse")
            st.metric("NIFTY 50", "24,926.15", "+196.35 (+0.79%)")
            st.line_chart(make_series(seed=2, points=45), height=190)
            st.write("High: 24,951.80")
            st.write("Low: 24,684.10")

    with c2:
        with st.container(border=True):
            st.subheader("Research Engine")
            st.metric("Confidence", "74/100", "Active")
            st.progress(0.74)
            st.write("Bullish setups: 18")
            st.write("Bearish setups: 6")
            st.write("Neutral: 4")

    with c3:
        with st.container(border=True):
            st.subheader("Memory Core")
            st.metric("Active Memory", "98.7%", "+0.4%")
            st.write("Patterns tracked: 18,642")
            st.write("Scenarios stored: 1,248")
            st.write("Models evolved: 23")
            st.progress(0.987)

    with c4:
        with st.container(border=True):
            st.subheader("Today's Focus")
            st.checkbox("NIFTY Structure Analysis", value=True)
            st.checkbox("Bank Nifty Scenario Study", value=True)
            st.checkbox("Global Market Correlation", value=True)
            st.checkbox("Sector Rotation Scan")
            st.checkbox("Research Watchlist")
            st.progress(0.60)

    st.divider()

    left, right = st.columns([1.25, 1], gap="large")

    with left:
        with st.container(border=True):
            st.subheader("Finance Research Panel")
            st.dataframe(research_table(), use_container_width=True, hide_index=True)
            st.line_chart(make_series(seed=7, points=75), height=260)
            m1, m2, m3, m4 = st.columns(4)
            m1.success("Trend: Bullish")
            m2.success("Momentum: Strong")
            m3.warning("Risk: Medium")
            m4.info("Volume: Above Avg")

    with right:
        with st.container(border=True):
            st.subheader("Paper Journal / Backtests")
            st.dataframe(backtest_table(), use_container_width=True, hide_index=True)

    st.divider()

    with st.container(border=True):
        st.subheader("Market Watch")
        st.dataframe(demo_market_table(), use_container_width=True, hide_index=True)


def universe_engine_page() -> None:
    page_title(
        "Universe Engine",
        "Automatic Indian market universe with free TradingView chart access.",
    )

    stats = get_universe_stats()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Instruments", stats.get("total_instruments", 0))
    c2.metric("NSE", stats.get("nse_count", 0))
    c3.metric("BSE", stats.get("bse_count", 0))
    c4.metric("MCX / Core", stats.get("mcx_count", 0))

    st.caption(f"Last updated: {stats.get('last_updated', 'Not available')}")

    if st.button("Refresh Indian Market Universe", use_container_width=True):
        with st.spinner("Updating Indian stock universe..."):
            load_master_universe(force_refresh=True)
        st.success("Universe updated successfully.")
        st.rerun()

    st.divider()

    search_text = st.text_input(
        "Search Indian stock / index / commodity",
        placeholder="Example: TATA, RELIANCE, SBIN, BANKNIFTY, CRUDEOIL",
    )

    results = search_universe(search_text, limit=200)

    if results.empty:
        st.warning("No result found. Try another symbol or company name.")
        return

    results = results.copy()

    for col in ["exchange", "symbol", "name", "instrument_type", "segment", "tradingview_symbol"]:
        if col not in results.columns:
            results[col] = ""

    results["exchange"] = results["exchange"].apply(clean_text)
    results["symbol"] = results["symbol"].apply(clean_text)
    results["name"] = results["name"].apply(clean_text)
    results["instrument_type"] = results["instrument_type"].apply(clean_text)
    results["segment"] = results["segment"].apply(clean_text)
    results["tradingview_symbol"] = results["tradingview_symbol"].apply(clean_text)

    missing_exchange = results["exchange"].eq("") & results["tradingview_symbol"].str.contains(":", na=False)
    results.loc[missing_exchange, "exchange"] = results.loc[missing_exchange, "tradingview_symbol"].str.split(":").str[0]

    st.subheader("Search Results")

    show_cols = [
        "exchange",
        "symbol",
        "name",
        "instrument_type",
        "segment",
        "tradingview_symbol",
    ]

    st.dataframe(results[show_cols], use_container_width=True, hide_index=True)

    st.divider()

    options = []

    for idx, row in results.iterrows():
        exchange = clean_text(row.get("exchange", ""))
        symbol = clean_text(row.get("symbol", ""))
        name = clean_text(row.get("name", ""))
        tv_symbol = clean_text(row.get("tradingview_symbol", ""))

        label = f"{exchange} | {symbol} | {name} | {tv_symbol}"
        options.append((label, idx))

    if not options:
        st.warning("No selectable instrument found.")
        return

    selected_label = st.selectbox(
        "Select instrument",
        [item[0] for item in options],
    )

    selected_index = dict(options)[selected_label]
    selected_row = results.loc[selected_index]

    tv_symbol = clean_text(selected_row.get("tradingview_symbol", ""))

    if not tv_symbol:
        st.error("TradingView symbol not available for this instrument.")
        return

    encoded_symbol = quote(tv_symbol, safe="")

    st.success(f"Selected: {tv_symbol}")

    st.link_button(
        f"Open {tv_symbol} in free TradingView chart",
        f"https://in.tradingview.com/chart/?symbol={encoded_symbol}",
        use_container_width=True,
    )

    st.info(
        "Main chart opens in TradingView free website. "
        "This is more reliable because some Indian symbols are blocked inside embedded widgets."
    )

    st.divider()

    show_embedded = st.checkbox(
        "Try embedded chart inside Nandi OS",
        value=False,
        help="Some symbols may not load here because of TradingView widget restrictions.",
    )

    if show_embedded:
        interval = st.selectbox(
            "Timeframe",
            ["1", "3", "5", "15", "30", "60", "D", "W", "M"],
            index=3,
        )

        chart_url = (
            f"https://s.tradingview.com/widgetembed/"
            f"?symbol={encoded_symbol}"
            f"&interval={interval}"
            f"&hidesidetoolbar=0"
            f"&symboledit=1"
            f"&saveimage=1"
            f"&toolbarbg=F1F3F6"
            f"&studies=[]"
            f"&theme=light"
            f"&style=1"
            f"&timezone=Asia%2FKolkata"
            f"&withdateranges=1"
            f"&hideideas=1"
            f"&locale=in"
        )

        components.iframe(chart_url, height=760, scrolling=False)


def tradingview_page() -> None:
    page_title(
        "TradingView Live Chart",
        "Free TradingView chart access for Indian markets.",
    )

    symbol_map = {
        "NIFTY 50": "NSE:NIFTY",
        "BANK NIFTY": "NSE:BANKNIFTY",
        "FINNIFTY": "NSE:CNXFINANCE",
        "SENSEX": "BSE:SENSEX",
        "RELIANCE": "NSE:RELIANCE",
        "HDFC BANK": "NSE:HDFCBANK",
        "INFOSYS": "NSE:INFY",
        "INDIA VIX": "NSE:INDIAVIX",
        "CRUDE OIL": "MCX:CRUDEOIL1!",
        "NATURAL GAS": "MCX:NATURALGAS1!",
        "GOLD": "MCX:GOLD1!",
    }

    col1, col2 = st.columns(2)

    with col1:
        selected = st.selectbox("Quick Market", list(symbol_map.keys()), index=1)

    with col2:
        manual_symbol = st.text_input(
            "Or type TradingView symbol",
            placeholder="Example: NSE:TCS, NSE:SBIN, NSE:TATASTEEL",
        )

    if manual_symbol.strip():
        symbol = manual_symbol.strip().upper()
        if ":" not in symbol:
            symbol = f"NSE:{symbol}"
    else:
        symbol = symbol_map[selected]

    encoded_symbol = quote(symbol, safe="")

    st.success(f"Selected: {symbol}")

    st.link_button(
        f"Open {symbol} in free TradingView chart",
        f"https://in.tradingview.com/chart/?symbol={encoded_symbol}",
        use_container_width=True,
    )

    st.info(
        "Use the free TradingView button as the main chart option. "
        "Embedded chart is optional because some Indian symbols are blocked inside widgets."
    )

    show_embedded = st.checkbox("Try embedded chart inside Nandi OS", value=False)

    if show_embedded:
        interval = st.selectbox(
            "Timeframe",
            ["1", "3", "5", "15", "30", "60", "D", "W", "M"],
            index=3,
        )

        chart_url = (
            f"https://s.tradingview.com/widgetembed/"
            f"?symbol={encoded_symbol}"
            f"&interval={interval}"
            f"&hidesidetoolbar=0"
            f"&symboledit=1"
            f"&saveimage=1"
            f"&toolbarbg=F1F3F6"
            f"&studies=[]"
            f"&theme=light"
            f"&style=1"
            f"&timezone=Asia%2FKolkata"
            f"&withdateranges=1"
            f"&hideideas=1"
            f"&locale=in"
        )

        components.iframe(chart_url, height=760, scrolling=False)


def finance_research() -> None:
    page_title("Finance Research", "Scenario planning, market structure, and research notes.")

    left, right = st.columns([1, 1], gap="large")

    with left:
        with st.container(border=True):
            st.subheader("Research Setup")
            st.dataframe(research_table(), use_container_width=True, hide_index=True)

    with right:
        with st.container(border=True):
            st.subheader("Research Output")
            st.metric("Nandi Setup", "Bullish", "74/100 confidence")
            st.progress(0.74)
            st.info("Research output only. Not guaranteed profit or financial advice.")


def nandi_ceo() -> None:
    page_title(
        "Nandi CEO",
        "Nandi coordinates specialist engines and gives final decision support.",
    )

    c1, c2, c3 = st.columns(3)

    with c1:
        with st.container(border=True):
            st.subheader("Market AI")
            st.metric("Bias", "Bullish", "87%")
            st.write("Trend, levels, volume, and momentum are supportive.")

    with c2:
        with st.container(border=True):
            st.subheader("Strategy AI")
            st.metric("Status", "Confirmed", "RSI/Breakout aligned")
            st.write("Current setup is passing base strategy checks.")

    with c3:
        with st.container(border=True):
            st.subheader("Risk AI")
            st.metric("Risk", "Medium", "Controlled")
            st.write("Trade allowed only with fixed stop loss.")

    st.divider()

    with st.container(border=True):
        st.subheader("CEO Summary")
        st.write("Nandi has received inputs from Market AI, Strategy AI, and Risk AI.")
        st.success("Final stance: WAIT FOR CONFIRMATION")
        st.info("Nandi will not approve forced trades when risk and confirmation are not aligned.")


def nandi_chat() -> None:
    page_title("Nandi Chat", "Private AI chat area. Full AI API connection will be added later.")

    for role, message in st.session_state.chat_history:
        with st.chat_message(role):
            st.write(message)

    prompt = st.chat_input("Ask Nandi...")

    if prompt:
        st.session_state.chat_history.append(("user", prompt))
        st.session_state.chat_history.append(
            (
                "assistant",
                "Nandi is ready. Next phase will connect real AI model, memory, market data, and tools.",
            )
        )
        st.rerun()


def memory_core() -> None:
    page_title("Memory Core", "Pattern memory, scenario storage, and learning status.")

    c1, c2, c3 = st.columns(3)
    c1.metric("Patterns Tracked", "18,642", "+214")
    c2.metric("Scenarios Stored", "1,248", "+31")
    c3.metric("Active Memory", "98.7%", "+0.4%")

    st.progress(0.987)

    with st.container(border=True):
        st.subheader("Memory Notes")
        st.write("This page will later connect to local memory / database for Nandi.")


def goals() -> None:
    page_title("Goals", "Track your capital growth and Nandi build roadmap.")

    st.checkbox("Build Nandi OS clean UI", value=True)
    st.checkbox("Add login", value=True)
    st.checkbox("Add Nandi Decision Engine", value=True)
    st.checkbox("Add Universe Engine", value=True)
    st.checkbox("Add real market data")
    st.checkbox("Add option chain / OI logic")
    st.checkbox("Add AI chat and memory")


def daily_updates() -> None:
    page_title("Daily Updates", "Morning checklist and market preparation.")

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


def strategy_lab() -> None:
    page_title("Strategy Lab", "Upload CSV and test RSI 24/78 setup.")

    uploaded = st.file_uploader("Upload CSV with Close column", type=["csv"])

    if not uploaded:
        st.info("Upload your CSV file to test the strategy.")
        return

    df = pd.read_csv(uploaded)

    if "Close" not in df.columns:
        st.error("CSV must contain a Close column.")
        return

    result = rsi_24_78_signal(df)

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

    st.metric("Latest Strategy Signal", result.get("signal", "No Signal"))
    st.write(result.get("reason", ""))

    st.dataframe(df.tail(80), use_container_width=True)
    st.line_chart(df[["Close", "RSI"]])


def watchlist() -> None:
    page_title("Watchlist", "Manage your personal market watchlist.")

    new_symbol = st.text_input("Add symbol", placeholder="Example: TCS, SBIN, NIFTY")

    if st.button("Add to Watchlist") and new_symbol.strip():
        st.session_state.watchlist.append(new_symbol.strip().upper())
        st.rerun()

    watch_df = pd.DataFrame({"Symbol": st.session_state.watchlist})
    st.dataframe(watch_df, use_container_width=True, hide_index=True)


def nandi_decision_engine_page() -> None:
    page_title(
        "Nandi Decision Engine",
        "Market bias + strategy signal + risk check = final Nandi action.",
    )

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
        risk["status"],
    )

    st.divider()
    st.subheader("Final Nandi Decision")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Action", final["action"])
    c2.metric("Confidence", f'{final["confidence"]}%')
    c3.metric("Market Bias", market["bias"])
    c4.metric("Risk Status", risk["status"])

    with st.container(border=True):
        st.subheader("Reasoning")
        for reason in final["reasons"]:
            st.write(f"✅ {reason}")

    st.info("Research support only. Not guaranteed profit or financial advice.")


def settings() -> None:
    page_title("Settings", "Control Nandi OS preferences.")

    st.text_input("Display Name", value=st.session_state.username)
    st.selectbox("Theme", ["Premium Mint", "Clean White", "Deep Green"])
    st.selectbox("Mode", ["Research Mode", "Trading Mode", "Personal AI Mode"])
    st.warning("Current login uses Streamlit Secrets. Later we can add stronger user management.")


if not st.session_state.logged_in:
    login_page()
else:
    sidebar()

    pages: Dict[str, Callable[[], None]] = {
        "Command Center": command_center,
        "Universe Engine": universe_engine_page,
        "TradingView Live Chart": tradingview_page,
        "Finance Research": finance_research,
        "Nandi CEO": nandi_ceo,
        "Nandi Chat": nandi_chat,
        "Memory Core": memory_core,
        "Goals": goals,
        "Daily Updates": daily_updates,
        "Strategy Lab": strategy_lab,
        "Watchlist": watchlist,
        "Nandi Decision Engine": nandi_decision_engine_page,
        "Settings": settings,
    }

    pages.get(st.session_state.page, command_center)()
