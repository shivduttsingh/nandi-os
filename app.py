from __future__ import annotations

from datetime import datetime
from urllib.parse import quote
import os

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Nandi OS", page_icon="🐂", layout="wide", initial_sidebar_state="expanded")

DATA_DIR = "data"
NOTES_FILE = os.path.join(DATA_DIR, "nandi_notes.csv")
WATCHLIST_FILE = os.path.join(DATA_DIR, "nandi_watchlist.csv")
os.makedirs(DATA_DIR, exist_ok=True)

try:
    APP_USERNAME = st.secrets["auth"]["username"]
    APP_PASSWORD = st.secrets["auth"]["password"]
except Exception:
    APP_USERNAME = "admin"
    APP_PASSWORD = "admin"

RSI_LENGTH = 14
RSI_SOURCE = "Close"
RSI_METHOD = "TradingView-style Wilder/RMA RSI"
BUY_ZONE_RSI = 24
BUY_WATCH_RSI = 35
SELL_EXIT_RSI = 72

NSE_SYMBOLS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "BHARTIARTL",
    "ITC", "HINDUNILVR", "BAJFINANCE", "HCLTECH", "SUNPHARMA", "MARUTI", "TITAN", "ULTRACEMCO", "NTPC", "POWERGRID",
    "ONGC", "ADANIENT", "ADANIPORTS", "TATASTEEL", "JSWSTEEL", "COALINDIA", "WIPRO", "TECHM", "M&M", "BAJAJFINSV",
    "HDFCLIFE", "SBILIFE", "HEROMOTOCO", "EICHERMOT", "DRREDDY", "CIPLA", "DIVISLAB", "GRASIM", "BPCL", "IOC",
    "HINDALCO", "APOLLOHOSP", "BRITANNIA", "NESTLEIND", "ASIANPAINT", "TATAMOTORS", "INDUSINDBK", "BAJAJ-AUTO", "SHRIRAMFIN", "UPL",
    "DMART", "PIDILITIND", "DABUR", "GODREJCP", "BERGEPAINT", "AMBUJACEM", "ACC", "VEDL", "SAIL", "NMDC",
    "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "IDFCFIRSTB", "FEDERALBNK", "AUBANK", "BANDHANBNK", "YESBANK", "RBLBANK",
    "IRCTC", "INDIGO", "DLF", "GAIL", "PETRONET", "MOTHERSON", "BOSCHLTD", "TVSMOTOR", "ASHOKLEY", "ESCORTS",
    "TRENT", "VOLTAS", "CROMPTON", "POLYCAB", "HAVELLS", "SIEMENS", "ABB", "BEL", "HAL", "BHEL",
    "IRFC", "RVNL", "CONCOR", "LICI", "JIOFIN", "PAYTM", "ZOMATO", "NYKAA", "NAUKRI", "POLICYBZR",
]

st.markdown("""
<style>
.stApp {background: radial-gradient(circle at top left, rgba(19,181,90,.14), transparent 32%), linear-gradient(135deg,#ffffff 0%,#f8fff9 45%,#eefaf2 100%);}
header[data-testid="stHeader"] {background: rgba(255,255,255,0);}
.block-container {max-width: 1450px; padding-top: 1.2rem; padding-bottom: 2rem;}
section[data-testid="stSidebar"] {background: rgba(255,255,255,.94); border-right: 1px solid #dfece4;}
div[data-testid="stMetric"] {background: rgba(255,255,255,.86); border: 1px solid #dfece4; border-radius: 18px; padding: 16px; box-shadow: 0 8px 28px rgba(7,90,44,.06);}
div[data-testid="stVerticalBlockBorderWrapper"] {background: rgba(255,255,255,.88); border-radius: 22px; box-shadow: 0 10px 34px rgba(7,90,44,.06);}
div.stButton > button, div[data-testid="stFormSubmitButton"] > button {border-radius: 12px; font-weight: 800;}
div[data-testid="stFormSubmitButton"] > button {background: #07883f; color: white; border: 1px solid #07883f;}
</style>
""", unsafe_allow_html=True)

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "page" not in st.session_state:
    st.session_state.page = "Command Center"
if "username" not in st.session_state:
    st.session_state.username = "Shiv"
if "latest_market_scan" not in st.session_state:
    st.session_state.latest_market_scan = None
if "latest_scan_meta" not in st.session_state:
    st.session_state.latest_scan_meta = {}
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "watchlist" not in st.session_state:
    st.session_state.watchlist = ["NIFTY 50", "BANK NIFTY", "RELIANCE", "HDFCBANK", "TATASTEEL", "GOLD", "CRUDE OIL"]


def clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ["nan", "none", "nat"] else text


def page_title(title: str, subtitle: str = "") -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def tv_symbol(symbol: str) -> str:
    symbol = clean_text(symbol).upper()
    mapping = {
        "NIFTY 50": "NSE:NIFTY",
        "NIFTY": "NSE:NIFTY",
        "BANK NIFTY": "NSE:BANKNIFTY",
        "BANKNIFTY": "NSE:BANKNIFTY",
        "FINNIFTY": "NSE:CNXFINANCE",
        "SENSEX": "BSE:SENSEX",
        "INDIA VIX": "NSE:INDIAVIX",
        "GOLD": "MCX:GOLD1!",
        "CRUDE OIL": "MCX:CRUDEOIL1!",
        "CRUDEOIL": "MCX:CRUDEOIL1!",
        "NATURAL GAS": "MCX:NATURALGAS1!",
        "USD/INR": "FX_IDC:USDINR",
    }
    if symbol in mapping:
        return mapping[symbol]
    if ":" in symbol:
        return symbol
    return f"NSE:{symbol}"


def tv_url(symbol: str) -> str:
    return f"https://in.tradingview.com/chart/?symbol={quote(symbol, safe='')}"


def save_watchlist() -> None:
    pd.DataFrame({"symbol": st.session_state.watchlist}).to_csv(WATCHLIST_FILE, index=False)


def load_watchlist() -> None:
    if os.path.exists(WATCHLIST_FILE):
        try:
            df = pd.read_csv(WATCHLIST_FILE)
            if "symbol" in df.columns and not df.empty:
                st.session_state.watchlist = list(dict.fromkeys([clean_text(x).upper() for x in df["symbol"] if clean_text(x)]))
        except Exception:
            pass


def save_note(title: str, note: str) -> None:
    if not note.strip():
        return
    row = pd.DataFrame([{"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "title": title.strip() or "Nandi Note", "note": note.strip()}])
    if os.path.exists(NOTES_FILE):
        row = pd.concat([pd.read_csv(NOTES_FILE), row], ignore_index=True)
    row.to_csv(NOTES_FILE, index=False)


def read_notes() -> pd.DataFrame:
    if os.path.exists(NOTES_FILE):
        try:
            return pd.read_csv(NOTES_FILE)
        except Exception:
            pass
    return pd.DataFrame(columns=["time", "title", "note"])


@st.cache_data(ttl=900)
def make_series(seed: int = 10, points: int = 80, start: float = 24600) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(start + np.linspace(0, 260, points) + np.cumsum(rng.normal(0, 22, points)))


def calculate_rma(series: pd.Series, period: int = 14) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    rma = pd.Series(index=values.index, dtype="float64")
    seed = values.rolling(window=period, min_periods=period).mean()
    first_valid_index = seed.first_valid_index()
    if first_valid_index is None:
        return rma
    first_position = values.index.get_loc(first_valid_index)
    rma.iloc[first_position] = seed.iloc[first_position]
    for i in range(first_position + 1, len(values)):
        current_value = values.iloc[i]
        previous_rma = rma.iloc[i - 1]
        rma.iloc[i] = previous_rma if pd.isna(current_value) else ((previous_rma * (period - 1)) + current_value) / period
    return rma


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    close = pd.to_numeric(close, errors="coerce")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = calculate_rma(gain, period)
    avg_loss = calculate_rma(loss, period)
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50)
    return rsi


def clean_price_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    data = df.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [str(col[-1]) for col in data.columns]
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in data.columns:
            data[col] = pd.NA
        data[col] = pd.to_numeric(data[col], errors="coerce")
    return data[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


@st.cache_data(ttl=900)
def download_data(symbols: tuple[str, ...], period: str = "6mo") -> dict[str, pd.DataFrame]:
    output: dict[str, pd.DataFrame] = {}
    yahoo_symbols = [f"{s}.NS" for s in symbols]
    try:
        raw = yf.download(yahoo_symbols, period=period, interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    except Exception:
        return output
    if raw is None or raw.empty:
        return output
    for original, yahoo_symbol in zip(symbols, yahoo_symbols):
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if yahoo_symbol not in raw.columns.get_level_values(0):
                    continue
                df = raw[yahoo_symbol].copy()
            else:
                df = raw.copy()
            df = clean_price_df(df)
            if len(df) >= 55:
                output[original] = df
        except Exception:
            continue
    return output


def analyze_stock(symbol: str, df: pd.DataFrame) -> dict:
    data = clean_price_df(df)
    if len(data) < 55:
        return {}
    data["RSI"] = calculate_rsi(data["Close"], RSI_LENGTH)
    data["SMA20"] = data["Close"].rolling(20).mean()
    data["SMA50"] = data["Close"].rolling(50).mean()
    data["AVG_VOLUME20"] = data["Volume"].rolling(20).mean()
    data["MOMENTUM20"] = data["Close"].pct_change(20) * 100
    latest = data.iloc[-1]
    close = float(latest["Close"]) if pd.notna(latest["Close"]) else 0
    rsi = float(latest["RSI"]) if pd.notna(latest["RSI"]) else 0
    sma20 = float(latest["SMA20"]) if pd.notna(latest["SMA20"]) else 0
    sma50 = float(latest["SMA50"]) if pd.notna(latest["SMA50"]) else 0
    volume = float(latest["Volume"]) if pd.notna(latest["Volume"]) else 0
    avg_volume = float(latest["AVG_VOLUME20"]) if pd.notna(latest["AVG_VOLUME20"]) else 0
    momentum20 = float(latest["MOMENTUM20"]) if pd.notna(latest["MOMENTUM20"]) else 0
    volume_ratio = volume / avg_volume if avg_volume else 0

    if rsi <= BUY_ZONE_RSI:
        action, score, reason = "BUY ZONE", 90, "RSI is at or below 24. Main buy zone."
    elif rsi <= BUY_WATCH_RSI:
        action, score, reason = "BUY WATCH", 75, "RSI is between 24 and 35. Buy-watch recovery zone."
    elif rsi < SELL_EXIT_RSI:
        action, score, reason = "WAIT", 40, "RSI is between 35 and 72. Not a fresh buy zone."
    else:
        action, score, reason = "SELL / EXIT", 5, "RSI is at or above 72. Exit zone, not buy zone."

    reasons = [reason]
    if action in ["BUY ZONE", "BUY WATCH"]:
        if close > sma20:
            score += 5
            reasons.append("Price is above 20-day average.")
        else:
            reasons.append("Price is below 20-day average.")

        if close > sma50:
            score += 5
            reasons.append("Price is above 50-day average.")
        else:
            reasons.append("Price is below 50-day average.")

        if volume_ratio >= 1.5:
            score += 5
            reasons.append("Volume is strongly above 20-day average.")
        elif volume_ratio >= 1.1:
            score += 3
            reasons.append("Volume is above average.")
        else:
            reasons.append("Volume confirmation is weak.")

        if momentum20 > 0:
            score += 5
            reasons.append("20-day momentum is positive.")
        else:
            reasons.append("20-day momentum is not positive yet.")
    elif action == "WAIT":
        reasons.append("Nandi will wait until RSI comes closer to buy zone.")
    else:
        reasons.append("Nandi will avoid buying because RSI is already high.")

    return {
        "symbol": symbol,
        "name": symbol,
        "close": round(close, 2),
        "rsi": round(rsi, 2),
        "rsi_length": RSI_LENGTH,
        "rsi_source": RSI_SOURCE,
        "rsi_method": RSI_METHOD,
        "buy_zone": f"RSI <= {BUY_ZONE_RSI}",
        "buy_watch_zone": f"RSI {BUY_ZONE_RSI} to {BUY_WATCH_RSI}",
        "sell_exit_zone": f"RSI >= {SELL_EXIT_RSI}",
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2),
        "volume_ratio": round(volume_ratio, 2),
        "momentum20_pct": round(momentum20, 2),
        "score": min(100, int(score)),
        "action": action,
        "reason": " | ".join(reasons),
        "tradingview_symbol": f"NSE:{symbol}",
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def run_nandi_scan(max_symbols: int, top_n: int, period: str) -> pd.DataFrame:
    symbols = tuple(NSE_SYMBOLS[:max_symbols])
    data_map = download_data(symbols, period)
    results = []
    for symbol in symbols:
        if symbol in data_map:
            result = analyze_stock(symbol, data_map[symbol])
            if result:
                results.append(result)

    if not results:
        return pd.DataFrame()

    report = pd.DataFrame(results)
    action_rank = {"BUY ZONE": 1, "BUY WATCH": 2, "WAIT": 3, "SELL / EXIT": 4}
    report["action_rank"] = report["action"].map(action_rank).fillna(9)
    report = report.sort_values(
        ["action_rank", "score", "volume_ratio", "momentum20_pct"],
        ascending=[True, False, False, False],
    )
    return report.drop(columns=["action_rank"]).head(top_n).reset_index(drop=True)


def login_page() -> None:
    st.title("Nandi OS")
    st.caption("Private AI Finance Platform")
    left, right = st.columns([1.2, 1], gap="large")

    with left:
        st.header("AI-Powered Research. Smarter Decisions.")
        st.write("Private workspace for RSI scanning, TradingView confirmation, watchlist, risk, notes, and daily planning.")
        c1, c2 = st.columns(2)

        with c1:
            with st.container(border=True):
                st.subheader("Market Pulse")
                st.metric("NIFTY 50", "Research Mode", "Chart confirm")
                st.line_chart(make_series(seed=1, points=45), height=190)

        with c2:
            with st.container(border=True):
                st.subheader("Locked RSI Rule")
                st.success("BUY ZONE: RSI <= 24")
                st.info("BUY WATCH: RSI 24 to 35")
                st.error("SELL / EXIT: RSI >= 72")

    with right:
        with st.container(border=True):
            st.header("Welcome back 👋")
            with st.form("login_form"):
                username = st.text_input("Email or Username")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign In", use_container_width=True)

            if submitted:
                if username.strip() == APP_USERNAME and password == APP_PASSWORD:
                    st.session_state.logged_in = True
                    st.session_state.username = username.split("@")[0].title()
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

            st.info("Private Workspace | Secure Access")


def sidebar() -> None:
    with st.sidebar:
        st.title("🐂 Nandi OS")
        st.success("All Systems Active")
        pages = [
            "Command Center",
            "Nandi Market Scanner",
            "TradingView Live Chart",
            "Watchlist",
            "Daily Updates",
            "Memory Core",
            "Goals",
            "Strategy Lab",
            "Nandi Chat",
            "Nandi Decision Engine",
            "Settings",
        ]

        for page in pages:
            if st.button(page, use_container_width=True):
                st.session_state.page = page
                st.rerun()

        st.divider()
        st.write("RSI: TradingView-style")
        st.write("Buy: <= 24")
        st.write("Exit: >= 72")

        if st.button("Logout", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()


def command_center() -> None:
    page_title("Nandi Command Center", "Daily market cockpit for scanner, watchlist, risk, and notes.")
    scan = st.session_state.latest_market_scan
    best_stock = "Run scanner" if scan is None or scan.empty else str(scan.iloc[0].get("symbol", ""))
    best_action = "No scan" if scan is None or scan.empty else str(scan.iloc[0].get("action", ""))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scanner Rows", 0 if scan is None or scan.empty else len(scan))
    c2.metric("Best Stock", best_stock)
    c3.metric("Best Action", best_action)
    c4.metric("Watchlist", len(st.session_state.watchlist))

    st.divider()
    left, mid, right = st.columns([1.1, 1, 1], gap="large")

    with left:
        with st.container(border=True):
            st.subheader("Nandi RSI Rule")
            st.success("BUY ZONE: RSI <= 24")
            st.info("BUY WATCH: RSI 24 to 35")
            st.warning("WAIT: RSI 35 to 72")
            st.error("SELL / EXIT: RSI >= 72")

    with mid:
        with st.container(border=True):
            st.subheader("Latest Scanner Snapshot")
            if scan is None or scan.empty:
                st.write("No scan yet.")
            else:
                cols = [c for c in ["symbol", "rsi", "score", "action", "scan_time"] if c in scan.columns]
                st.dataframe(scan[cols].head(5), use_container_width=True, hide_index=True)

    with right:
        with st.container(border=True):
            st.subheader("Risk Reminder")
            st.write("Do not buy RSI 72+ stocks.")
            st.write("Focus only BUY ZONE / BUY WATCH.")
            st.write("Confirm final chart in TradingView.")


def market_scanner_page() -> None:
    page_title("Nandi Market Scanner", "Find buy opportunities using RSI 24/72 logic.")
    st.info("TradingView-style RSI 14 Close. BUY ZONE <=24 | BUY WATCH 24-35 | SELL/EXIT >=72.")

    c1, c2, c3 = st.columns(3)
    with c1:
        max_symbols = st.selectbox("How many NSE stocks to scan?", [50, 100], index=1)
    with c2:
        top_n = st.selectbox("Show top results", [5, 10, 15, 20], index=1)
    with c3:
        period = st.selectbox("Data period", ["6mo", "1y"], index=0)

    if st.button("Run Nandi Market Scan", use_container_width=True):
        with st.spinner("Nandi is scanning. Please wait..."):
            report = run_nandi_scan(int(max_symbols), int(top_n), period)

        st.session_state.latest_market_scan = report
        st.session_state.latest_scan_meta = {
            "scan_size": max_symbols,
            "top_n": top_n,
            "period": period,
            "completed_at": datetime.now().strftime("%d %b %Y · %I:%M %p"),
        }

        st.success("Market scan completed." if not report.empty else "Scan completed but no rows returned. Try again.")

    report = st.session_state.latest_market_scan
    if report is None or report.empty:
        st.write("Run scanner to see results.")
        return

    top = report.iloc[0]
    st.divider()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Best Stock", top.get("symbol", ""))
    m2.metric("Best RSI", top.get("rsi", ""))
    m3.metric("Best Action", top.get("action", ""))
    m4.metric("Rows", len(report))

    st.success(f"RSI method locked: {RSI_METHOD}")

    action_filter = st.selectbox("Filter Action", ["All", "BUY ZONE", "BUY WATCH", "WAIT", "SELL / EXIT"], index=0)
    display = report.copy()

    if action_filter != "All":
        display = display[display["action"] == action_filter]

    cols = [
        "symbol", "close", "rsi", "score", "action", "buy_zone", "buy_watch_zone",
        "sell_exit_zone", "volume_ratio", "momentum20_pct", "tradingview_symbol", "scan_time",
    ]

    st.dataframe(display[[c for c in cols if c in display.columns]], use_container_width=True, hide_index=True)

    st.download_button(
        "Download Latest Scan CSV",
        data=display.to_csv(index=False).encode("utf-8"),
        file_name="nandi_scan.csv",
        mime="text/csv",
        use_container_width=True,
    )

    if display.empty:
        return

    st.divider()
    labels = [f"{r.symbol} | RSI {r.rsi} | {r.action}" for r in display.itertuples()]
    selected = st.selectbox("Select one result", labels)
    selected_row = display.iloc[labels.index(selected)]

    st.subheader("Nandi Reasoning")
    for part in str(selected_row.get("reason", "")).split(" | "):
        st.write(f"✅ {part}")

    tv = selected_row.get("tradingview_symbol", tv_symbol(selected_row.get("symbol", "")))
    c1, c2 = st.columns(2)
    c1.link_button(f"Open {tv} in TradingView", tv_url(tv), use_container_width=True)

    if c2.button("Add to Watchlist", use_container_width=True):
        sym = str(selected_row.get("symbol", "")).upper()
        if sym and sym not in st.session_state.watchlist:
            st.session_state.watchlist.append(sym)
            save_watchlist()
            st.success(f"{sym} added.")


def tradingview_page() -> None:
    page_title("TradingView Live Chart", "Open free TradingView charts.")
    quick = ["NIFTY 50", "BANK NIFTY", "RELIANCE", "HDFCBANK", "INFY", "TATASTEEL", "GOLD", "CRUDE OIL"]

    c1, c2 = st.columns(2)
    selected = c1.selectbox("Quick Market", quick)
    manual = c2.text_input("Or type symbol", placeholder="Example: TCS, NSE:SBIN")

    tv = tv_symbol(manual if manual.strip() else selected)
    st.success(f"Selected: {tv}")
    st.link_button(f"Open {tv} in TradingView", tv_url(tv), use_container_width=True)


def watchlist_page() -> None:
    page_title("Watchlist", "Manage symbols and open charts.")
    new_symbol = st.text_input("Add symbol", placeholder="Example: TCS, SBIN, NIFTY")

    if st.button("Add to Watchlist", use_container_width=True) and new_symbol.strip():
        sym = new_symbol.strip().upper()
        if sym not in st.session_state.watchlist:
            st.session_state.watchlist.append(sym)
            save_watchlist()
        st.rerun()

    rows = [{"Symbol": s, "TradingView": tv_symbol(s), "URL": tv_url(tv_symbol(s))} for s in st.session_state.watchlist]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    for s in st.session_state.watchlist:
        tv = tv_symbol(s)
        st.link_button(f"Open {tv}", tv_url(tv), use_container_width=True)


def daily_updates_page() -> None:
    page_title("Daily Updates", "Morning checklist and market preparation.")
    tasks = [
        "Check global sentiment",
        "Check GIFT NIFTY",
        "Mark PDH/PDL",
        "Run Nandi scanner",
        "Check BUY ZONE / BUY WATCH only",
        "Avoid RSI 72+ buys",
        "Confirm on TradingView",
        "Protect capital",
    ]

    for task in tasks:
        st.checkbox(task)

    st.text_area("Today’s notes")


def memory_page() -> None:
    page_title("Memory Core", "Save notes and observations.")
    title = st.text_input("Note title", value="Market Note")
    note = st.text_area("Note", height=180)

    if st.button("Save Note", use_container_width=True):
        save_note(title, note)
        st.success("Saved.")

    notes = read_notes()
    if not notes.empty:
        st.dataframe(notes.tail(20).sort_index(ascending=False), use_container_width=True, hide_index=True)


def goals_page() -> None:
    page_title("Goals", "Capital tracker and roadmap.")
    capital = st.number_input("Current Capital", value=25000.0, step=1000.0)
    target = st.number_input("Target Capital", value=100000.0, step=5000.0)
    progress = 0 if target <= 0 else min(1.0, capital / target)

    st.progress(progress)
    st.metric("Progress", f"{progress*100:.1f}%")

    for item in ["Login", "Scanner", "TradingView", "RSI 24/72", "Watchlist", "Memory", "AI Chat later", "OI engine later"]:
        st.checkbox(item, value=item in ["Login", "Scanner", "TradingView", "RSI 24/72"])


def strategy_lab_page() -> None:
    page_title("Strategy Lab", "Upload CSV and test RSI 24/72.")
    uploaded = st.file_uploader("Upload CSV with Close column", type=["csv"])

    if not uploaded:
        return

    df = pd.read_csv(uploaded)

    if "Close" not in df.columns:
        st.error("CSV must contain Close column.")
        return

    df["RSI"] = calculate_rsi(df["Close"], RSI_LENGTH)
    df["Signal"] = "WAIT"
    df.loc[df["RSI"] <= 24, "Signal"] = "BUY ZONE"
    df.loc[(df["RSI"] > 24) & (df["RSI"] <= 35), "Signal"] = "BUY WATCH"
    df.loc[df["RSI"] >= 72, "Signal"] = "SELL / EXIT"

    st.metric("Latest Signal", df["Signal"].iloc[-1])
    st.dataframe(df.tail(80), use_container_width=True)
    st.line_chart(df[["Close", "RSI"]])


def nandi_chat_page() -> None:
    page_title("Nandi Chat", "Foundation chat mode.")

    for role, msg in st.session_state.chat_history:
        with st.chat_message(role):
            st.write(msg)

    prompt = st.chat_input("Ask Nandi...")

    if prompt:
        st.session_state.chat_history.append(("user", prompt))
        st.session_state.chat_history.append((
            "assistant",
            "Nandi foundation mode is active. Scanner uses TradingView-style RSI: buy <=24, watch 24-35, exit >=72.",
        ))
        st.rerun()


def decision_page() -> None:
    page_title("Nandi Decision Engine", "Manual decision support.")

    c1, c2, c3 = st.columns(3)
    price = c1.number_input("Current Price", value=24900.0)
    support = c1.number_input("Support", value=24700.0)
    resistance = c1.number_input("Resistance", value=25000.0)

    signal = c2.selectbox("Strategy Signal", ["Buy", "Sell", "No Signal"])
    capital = c2.number_input("Capital", value=25000.0)

    max_risk = c3.number_input("Max Risk %", value=2.0)
    stop = c3.number_input("Stop Loss Points", value=40.0)
    lot = c3.number_input("Lot Size", value=1)

    market_bias = "Bullish" if price > resistance else "Bearish" if price < support else "Neutral"
    max_loss = capital * max_risk / 100
    trade_risk = stop * lot
    risk_status = "Safe" if trade_risk <= max_loss else "Unsafe"

    score = (
        (30 if market_bias == "Bullish" else -30 if market_bias == "Bearish" else 0)
        + (40 if signal == "Buy" else -40 if signal == "Sell" else 0)
        + (30 if risk_status == "Safe" else -30)
    )

    action = "BUY" if score >= 70 else "AVOID / BEARISH" if score <= -40 else "WAIT"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Action", action)
    m2.metric("Confidence", f"{max(0, min(100, score))}%")
    m3.metric("Market Bias", market_bias)
    m4.metric("Risk Status", risk_status)

    st.info("Research support only. Not guaranteed profit or financial advice.")


def settings_page() -> None:
    page_title("Settings", "Nandi OS preferences.")
    st.text_input("Display Name", value=st.session_state.username)
    st.selectbox("Theme", ["Premium Mint", "Clean White", "Deep Green"])

    st.subheader("Locked Trading Rule")
    st.write(f"RSI method: {RSI_METHOD}")
    st.write("BUY ZONE: RSI <= 24")
    st.write("BUY WATCH: RSI 24 to 35")
    st.write("SELL / EXIT: RSI >= 72")


if not st.session_state.logged_in:
    login_page()
else:
    load_watchlist()
    sidebar()

    routes = {
        "Command Center": command_center,
        "Nandi Market Scanner": market_scanner_page,
        "TradingView Live Chart": tradingview_page,
        "Watchlist": watchlist_page,
        "Daily Updates": daily_updates_page,
        "Memory Core": memory_page,
        "Goals": goals_page,
        "Strategy Lab": strategy_lab_page,
        "Nandi Chat": nandi_chat_page,
        "Nandi Decision Engine": decision_page,
        "Settings": settings_page,
    }

    routes.get(st.session_state.page, command_center)()
