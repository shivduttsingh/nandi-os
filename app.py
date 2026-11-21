from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from atlas_engine import REQUIRED, backtest, generate_signals, metrics, normalise_bars
from nandi_oi import UpstoxAPIError, UpstoxOptionChainClient
from nandi_v2.charting import candlestick_chart_html
from nandi_v2.nse import NSEDataError, NSEPublicClient

def read_upstox_token():
    try:
        value = st.secrets["upstox"]["access_token"]
        if value and str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    return os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()

def candles_to_frame(candles):
    return pd.DataFrame([{
        "timestamp": c.timestamp, "open": c.open, "high": c.high,
        "low": c.low, "close": c.close, "volume": c.volume
    } for c in candles])

def upstox_chain_frame(snapshot):
    return pd.DataFrame([{
        "strike": leg.strike, "side": leg.side, "LTP": leg.ltp,
        "change": leg.change_ltp, "OI": leg.oi, "change OI": leg.change_oi,
        "volume": leg.volume, "bid": leg.bid, "ask": leg.ask
    } for leg in snapshot.legs])

def window_chain_frame(frame, spot, wings=8):
    if frame.empty or "strike" not in frame.columns or not spot:
        return frame
    strikes = sorted(frame["strike"].dropna().unique())
    if not strikes:
        return frame
    atm = min(range(len(strikes)), key=lambda i: abs(float(strikes[i]) - float(spot)))
    selected = set(strikes[max(0, atm-wings):min(len(strikes), atm+wings+1)])
    return frame[frame["strike"].isin(selected)].sort_values("strike").reset_index(drop=True)

def nse_chain_frame(snapshot):
    return pd.DataFrame([{
        "strike": row.strike, "CE LTP": row.ce.ltp, "CE OI": row.ce.oi,
        "CE ΔOI": row.ce.change_oi, "CE volume": row.ce.volume,
        "PE LTP": row.pe.ltp, "PE OI": row.pe.oi,
        "PE ΔOI": row.pe.change_oi, "PE volume": row.pe.volume
    } for row in snapshot.rows])

DB_PATH = Path("atlas_paper_ledger.sqlite3")
st.set_page_config(page_title="ATLAS | NIFTY Options Research", page_icon="A", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
:root{--green:#176b43;--green2:#eaf5ee;--ink:#17251e;--muted:#64746b;--line:#dce7df}
.stApp{background:#f7faf8;color:var(--ink)}
.block-container{max-width:1600px;padding-top:1.35rem;padding-bottom:3rem}
section[data-testid="stSidebar"]{background:#fff;border-right:1px solid var(--line)}
h1,h2,h3{letter-spacing:-.03em;color:var(--ink)}
div[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:13px;padding:12px}
.atlas-hero{background:linear-gradient(115deg,#fff 55%,#eaf5ee);border:1px solid var(--line);border-radius:18px;padding:22px 26px;margin-bottom:16px;position:relative}
.atlas-hero:before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--green);border-radius:18px 0 0 18px}
.atlas-kicker{font-size:11px;letter-spacing:.16em;font-weight:800;color:var(--green)}
.atlas-title{font-size:34px;font-weight:850;letter-spacing:-.045em;margin:3px 0}
.atlas-sub{color:var(--muted);font-size:14px;max-width:950px}
.pill{display:inline-block;padding:5px 10px;border:1px solid #c6dfce;background:#f7fcf8;color:#176b43;border-radius:999px;font-size:11px;font-weight:700;margin-right:5px}
.small-note{color:var(--muted);font-size:12px}
section[data-testid="stSidebar"] .stRadio label{font-weight:600}
.stButton>button[kind="primary"]{background:#176b43;border-color:#176b43}
</style>
""", unsafe_allow_html=True)

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, strategy TEXT NOT NULL,
        option_symbol TEXT NOT NULL, side TEXT NOT NULL, expiry TEXT, quantity INTEGER NOT NULL,
        entry_premium REAL NOT NULL, stop_premium REAL NOT NULL, target_premium REAL NOT NULL,
        exit_premium REAL, status TEXT NOT NULL DEFAULT 'OPEN', notes TEXT)""")
    conn.commit()
    return conn

def load_trades():
    return pd.read_sql_query("SELECT * FROM paper_trades ORDER BY id DESC", db())

def money(v):
    return f"₹{v:,.2f}"

st.markdown("""
<div class="atlas-hero">
 <div class="atlas-kicker">INDEPENDENT OPTIONS RESEARCH TERMINAL</div>
 <div class="atlas-title">ATLAS</div>
 <div class="atlas-sub">NIFTY options • Intraday research • Strategy A / B / C • Paper execution only</div>
 <div style="margin-top:12px"><span class="pill">PAPER ONLY</span><span class="pill">NO BROKER ORDERS</span><span class="pill">RESEARCH BUILD 0.1</span></div>
</div>
""", unsafe_allow_html=True)

if "live_market" not in st.session_state:
    st.session_state.live_market = None

with st.sidebar:
    st.markdown("## ATLAS")
    page = st.radio("Workspace", ["Command Centre","Strategy Lab","Backtest","Paper Ledger","Data & Controls"], label_visibility="collapsed")
    st.divider()
    st.caption("MARKET DATA")
    if live:
        st.success("Market snapshot loaded")
        st.caption(f"Last manual refresh: {live.get('captured_at','—')} IST")
    else:
        st.error("Live feed not connected")
        st.caption("Connect a read-only Upstox token and refresh data in Data & Controls.")
    st.divider()
    st.caption("RESEARCH CAPITAL")
    st.metric("Paper capital", "₹20,000")
    st.caption("Risk limits are research defaults, not a return promise.")

if "bars" not in st.session_state:
    st.session_state.bars = None
if "data_label" not in st.session_state:
    st.session_state.data_label = "No dataset loaded"
if "live_market" not in st.session_state:
    st.session_state.live_market = None
if "nse_client" not in st.session_state:
    st.session_state.nse_client = NSEPublicClient()

bars = st.session_state.bars
live = st.session_state.live_market
signals = []
if bars is not None:
    try:
        signals = generate_signals(bars)
    except Exception as exc:
        st.error(f"Strategy engine could not evaluate the loaded dataset: {exc}")

if page == "Command Centre":
    st.subheader("Command Centre")
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("NIFTY spot", f"{live['upstox_spot']:,.2f}" if live and live.get("upstox_spot") else "—", help="Upstox option-chain underlying spot; last manually fetched snapshot")
    c2.metric("A / ORB signals", sum(s.strategy=="A" for s in signals))
    c3.metric("B / Failed breakout", sum(s.strategy=="B" for s in signals))
    c4.metric("Loaded bars", f"{len(bars):,}" if bars is not None else "0")
    if live:
        nse_spot = live.get("nse_spot")
        st.caption(f"Upstox snapshot: {live.get('upstox_time','—')} · NSE option-chain timestamp: {live.get('nse_time','—')} · NSE spot: {nse_spot:,.2f}" if nse_spot else f"Upstox snapshot: {live.get('upstox_time','—')} · NSE option-chain timestamp: {live.get('nse_time','—')}")
        if live.get("candles"):
            st.markdown("### NIFTY 50 · Upstox 5-minute candles")
            components.html(candlestick_chart_html(live["candles"], interval_minutes=5, title="NIFTY 50", chart_height=420), height=490, scrolling=False)
        chain_col1, chain_col2 = st.columns(2)
        with chain_col1:
            st.markdown("### Upstox option chain")
            if live.get("upstox_chain") is not None:
                st.dataframe(live["upstox_chain"], use_container_width=True, hide_index=True, height=360)
        with chain_col2:
            st.markdown("### NSE option chain")
            if live.get("nse_chain") is not None:
                st.dataframe(live["nse_chain"], use_container_width=True, hide_index=True, height=360)
    else:
        st.info("ATLAS is in research mode. It will not invent a price, option premium, OI reading, win rate or trade recommendation when the relevant feed is unavailable.")
    left,right = st.columns([1.3,1])
    with left:
        st.markdown("### Decision engine")
        st.markdown("""
        **A — Opening-range continuation**  
        Looks for a 9:15–9:30 range break, retest, volume participation and VWAP alignment.

        **B — Failed-breakout reversal**  
        Looks for price to trade beyond the opening range, close back inside, then break the opposing short-term structure.

        **C — Adaptive selector**  
        Evaluates A and B independently. Conflicting or incomplete evidence means WAIT; it does not force a trade.
        """)
    with right:
        st.markdown("### Current evidence")
        st.write("**Dataset:**", st.session_state.data_label)
        st.write("**Upstox:**", "Connected snapshot" if live else "Not connected")
        st.write("**NSE:**", "Snapshot loaded" if live and live.get("nse_chain") is not None else "Not loaded")
        st.write("**Signals found:**", len(signals))
        st.write("**Execution:** Paper only")
        st.write("**Broker routing:** Disabled")
    if signals:
        st.markdown("### Latest research signals")
        st.dataframe(pd.DataFrame([s.__dict__ for s in signals]).tail(10), use_container_width=True, hide_index=True)
    else:
        st.warning("No qualifying signal is available. Load verified 5-minute NIFTY OHLCV data to run the research engines.")

elif page == "Strategy Lab":
    st.subheader("Strategy Lab")
    st.caption("A, B and C are independent research models. Signal scores are setup filters, not calibrated probabilities.")
    tabs = st.tabs(["A · Opening-range continuation","B · Failed-breakout reversal","C · Adaptive selector"])
    descriptions = [
        ("A", "Continuation model", "9:15–9:30 opening range → confirmed close outside → retest holds → volume and VWAP agree.", "Invalidation: retest failure / stop beyond the retest structure."),
        ("B", "Reversal model", "Price sweeps the opening range → closes back inside → opposite short-term structure breaks.", "Invalidation: beyond the failed-breakout extreme."),
        ("C", "Regime-aware selector", "Runs A and B separately; accepts only a valid, non-conflicting setup. If evidence conflicts, output is WAIT.", "One active paper position at a time; no simultaneous opposing trades.")
    ]
    for tab,(code,name,logic,risk) in zip(tabs,descriptions):
        with tab:
            st.markdown(f"### Strategy {code}: {name}")
            st.write(logic)
            st.write(risk)
            if bars is None:
                st.info("Load data to see actual signals.")
            else:
                selected = [s for s in signals if (s.strategy==code if code!="C" else True)]
                if code=="C":
                    by_time={}
                    for sig in signals: by_time.setdefault(sig.timestamp,[]).append(sig)
                    selected=[v[0] for v in by_time.values() if len({x.side for x in v})==1]
                if selected:
                    st.dataframe(pd.DataFrame([s.__dict__ for s in selected]), use_container_width=True, hide_index=True)
                else:
                    st.warning("No qualifying signal in the loaded sample.")

elif page == "Backtest":
    st.subheader("Backtest & Validation")
    st.warning("This first research build tests signals on the NIFTY underlying OHLCV series. It does not simulate option premiums, IV, bid/ask spreads, expiry decay, lot sizing or broker charges. Do not interpret underlying points as options P&L.")
    if bars is None:
        st.info("Load a timestamped 5-minute OHLCV CSV in Data & Controls.")
    else:
        c1,c2,c3 = st.columns(3)
        strategy = c1.selectbox("Model", ["A","B","C"], index=2)
        rr = c2.slider("Reward / risk", 1.0, 4.0, 2.0, 0.25)
        costs = c3.number_input("Estimated round-trip cost (underlying points)", min_value=0.0, value=1.0, step=0.25)
        trades = backtest(bars, strategy=strategy, risk_reward=rr, cost_points=costs)
        m = metrics(trades)
        a,b,c,d,e = st.columns(5)
        a.metric("Trades",m["trades"])
        b.metric("Win rate",f'{m["win_rate"]:.1f}%')
        c.metric("Net points",m["net_points"])
        d.metric("Profit factor", "∞" if m["profit_factor"]==float("inf") else m["profit_factor"])
        e.metric("Max drawdown",m["max_drawdown"])
        if not trades.empty:
            st.markdown("### Trade-by-trade results")
            st.dataframe(trades, use_container_width=True, hide_index=True)
            st.download_button("Download backtest CSV", trades.to_csv(index=False), "atlas_backtest.csv", "text/csv")
            st.caption("These are in-sample results on the uploaded file. Promotion requires chronological out-of-sample and walk-forward testing with option-level fills and realistic costs.")
        else:
            st.warning("No trades met the current rules in this dataset.")

elif page == "Paper Ledger":
    st.subheader("Paper Portfolio & Trade Ledger")
    st.caption("Manual paper entries only. Record actual observed option premiums; ATLAS does not infer or fabricate premiums.")
    with st.form("new_trade"):
        x1,x2,x3 = st.columns(3)
        strategy = x1.selectbox("Strategy",["A","B","C"])
        side = x2.selectbox("Option side",["CE","PE"])
        expiry = x3.text_input("Expiry (YYYY-MM-DD)",placeholder="YYYY-MM-DD")
        x4,x5,x6 = st.columns(3)
        symbol = x4.text_input("Option symbol",placeholder="NIFTY ... CE / PE")
        qty = x5.number_input("Quantity",min_value=1,value=65,step=1)
        entry = x6.number_input("Observed entry premium (₹)",min_value=0.05,value=100.0,step=0.05)
        x7,x8 = st.columns(2)
        stop = x7.number_input("Premium stop (₹)",min_value=0.01,value=90.0,step=0.05)
        target = x8.number_input("Premium target (₹)",min_value=0.01,value=120.0,step=0.05)
        notes = st.text_input("Evidence / notes")
        submitted = st.form_submit_button("Record paper trade", type="primary")
        if submitted:
            if not symbol.strip() or entry<=0 or stop>=entry or target<=entry:
                st.error("Enter an option symbol and valid long-option premium levels (stop below entry, target above entry).")
            else:
                conn=db()
                conn.execute("INSERT INTO paper_trades(created_at,strategy,option_symbol,side,expiry,quantity,entry_premium,stop_premium,target_premium,status,notes) VALUES(?,?,?,?,?,?,?,?,?,'OPEN',?)",(datetime.now().isoformat(timespec="seconds"),strategy,symbol.strip(),side,expiry.strip(),int(qty),float(entry),float(stop),float(target),notes))
                conn.commit(); conn.close()
                st.success("Paper trade recorded in the local ledger.")
    ledger = load_trades()
    if not ledger.empty:
        st.markdown("### Open / closed trades")
        st.dataframe(ledger, use_container_width=True, hide_index=True)
        open_trades = ledger[ledger.status=="OPEN"]
        if not open_trades.empty:
            st.markdown("### Close a paper trade")
            with st.form("close_trade"):
                selected_id = st.selectbox("Trade ID",open_trades.id.tolist())
                exit_premium = st.number_input("Observed exit premium (₹)",min_value=0.01,value=100.0,step=0.05)
                if st.form_submit_button("Record exit"):
                    conn=db()
                    conn.execute("UPDATE paper_trades SET exit_premium=?,status='CLOSED' WHERE id=? AND status='OPEN'",(float(exit_premium),int(selected_id)))
                    conn.commit(); conn.close()
                    st.success("Exit recorded.")
                    st.rerun()
        closed=ledger[ledger.status=="CLOSED"].copy()
        if not closed.empty:
            closed["pnl_rupees"]=(closed.exit_premium-closed.entry_premium)*closed.quantity
            z1,z2,z3=st.columns(3)
            z1.metric("Closed trades",len(closed))
            z2.metric("Recorded paper P&L",money(closed.pnl_rupees.sum()))
            z3.metric("Win rate",f'{100*(closed.pnl_rupees>0).mean():.1f}%')
            st.download_button("Download paper ledger",ledger.to_csv(index=False),"atlas_paper_ledger.csv","text/csv")
    else:
        st.info("No paper trades recorded yet.")

elif page == "Data & Controls":
    st.subheader("Data & Controls")
    st.markdown("### Live read-only market snapshot")
    expiry_choice = st.selectbox("Upstox expiry", ["current_week", "current_month"], index=0)
    if st.button("Fetch Upstox + NSE data", type="primary"):
        live_result = {"captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        token = read_upstox_token()
        if token:
            try:
                client_key = f"{token[-6:]}:{expiry_choice}"
                if st.session_state.get("upstox_client_key") != client_key:
                    st.session_state.upstox_client = UpstoxOptionChainClient(access_token=token, expiry=expiry_choice)
                    st.session_state.upstox_client_key = client_key
                up = st.session_state.upstox_client
                opt = up.fetch_snapshot()
                candles = up.fetch_intraday_candles(interval_minutes=5)
                live_result["upstox_spot"] = opt.spot
                live_result["upstox_time"] = opt.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                live_result["upstox_chain"] = window_chain_frame(upstox_chain_frame(opt), opt.spot, wings=8)
                live_result["candles"] = candles
                st.session_state.bars = candles_to_frame(candles)
                st.session_state.data_label = f"Upstox intraday snapshot · {len(candles)} candles"
                st.success("Upstox option chain and intraday candles fetched.")
            except Exception as exc:
                st.error(f"Upstox fetch failed: {exc}")
        else:
            st.warning("Upstox token is not configured. NSE public-site fetch will still be attempted.")
        try:
            nse_client = st.session_state.nse_client
            nse_chain = nse_client.fetch_option_chain("NIFTY")
            nse_spot, nse_spot_time = nse_client.fetch_nifty_spot()
            live_result["nse_chain"] = window_chain_frame(nse_chain_frame(nse_chain), nse_chain.spot, wings=8)
            live_result["nse_time"] = nse_chain.raw_timestamp
            live_result["nse_spot"] = nse_spot
            live_result["nse_spot_time"] = nse_spot_time.strftime("%Y-%m-%d %H:%M:%S")
            st.success("NSE option-chain snapshot fetched.")
        except Exception as exc:
            st.warning(f"NSE public-site snapshot unavailable: {exc}")
        if live_result.get("upstox_chain") is not None or live_result.get("nse_chain") is not None:
            st.session_state.live_market = live_result
            st.rerun()
    st.caption("Refresh is manual, not a tick stream. NSE public-site access is best-effort and may be rate-limited; use an authorised/licensed feed for production decisions.")
    st.divider()
    st.markdown("### Import historical / replay data")
    st.write("Upload a CSV with columns: timestamp, open, high, low, close, volume. Use 5-minute NIFTY candles in IST. Optional: vwap.")
    uploaded = st.file_uploader("Upload OHLCV CSV",type=["csv"])
    if uploaded is not None:
        try:
            frame = pd.read_csv(uploaded)
            clean = normalise_bars(frame)
            st.session_state.bars = clean
            st.session_state.data_label = f"Uploaded CSV · {len(clean):,} bars · {clean.timestamp.min()} to {clean.timestamp.max()}"
            st.success(f"Loaded {len(clean):,} valid rows.")
        except Exception as exc:
            st.error(f"Could not load file: {exc}")
    if st.session_state.bars is not None:
        st.markdown("### Loaded sample")
        st.caption(st.session_state.data_label)
        st.dataframe(st.session_state.bars.tail(20),use_container_width=True,hide_index=True)
        st.download_button("Download normalised data",st.session_state.bars.to_csv(index=False),"atlas_normalised_market_data.csv","text/csv")
        if st.button("Clear loaded data"):
            st.session_state.bars=None
            st.session_state.data_label="No dataset loaded"
            st.rerun()
    st.divider()
    st.markdown("### Connectivity")
    st.write("**Upstox:**", "Read-only API adapter included; token status: configured" if read_upstox_token() else "Not configured")
    st.write("**NSE:**", "Public-site adapter included; not a licensed institutional feed")
    st.info("Upstox access is read-only. NSE public-site snapshots are best-effort and may be rate-limited; obtain appropriate exchange data entitlements for production use. The app never routes broker orders.")
    st.markdown("### Risk controls — initial research defaults")
    st.write("- Starting paper capital: ₹20,000")
    st.write("- Maximum planned loss per trade: ₹300")
    st.write("- Daily loss stop: ₹600")
    st.write("- Maximum two entries per day; one open position at a time")
    st.write("- Minimum planned reward/risk: 2:1")
    st.write("- No overnight positions; no broker order routing")
    st.caption("Lot size, premium affordability and exchange contract specifications must be checked against the current contract master before any option-level simulation.")

st.divider()
st.caption("ATLAS Research Build 0.1 · Not investment advice · Paper research only · Manual market snapshots · Option-level backtesting not yet enabled.")
