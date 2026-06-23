import base64
import hmac
from pathlib import Path
from datetime import datetime
import streamlit as st

APP_DIR = Path(__file__).resolve().parent

def find_asset(*names):
    for name in names:
        p1 = APP_DIR / name
        p2 = APP_DIR / "assets" / name
        if p1.exists():
            return p1
        if p2.exists():
            return p2
    return None

def image_to_data_uri(path):
    if not path or not path.exists():
        return None
    mime = "image/png"
    if path.suffix.lower() in [".jpg", ".jpeg"]:
        mime = "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"

st.set_page_config(page_title="Nandi OS", page_icon="🐂", layout="wide", initial_sidebar_state="expanded")

LOGO_PATH = find_asset("nandi_bull_logo.png", "download", "nandi_logo.png")
LOGO_URI = image_to_data_uri(LOGO_PATH)

def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if st.session_state.authenticated:
        return

    st.markdown("""
    <style>
    .stApp {
        background: radial-gradient(circle at 50% 10%, rgba(57,255,20,.16), transparent 28%), linear-gradient(180deg,#020402,#050805);
        color: white;
    }
    header[data-testid="stHeader"], #MainMenu, footer {display:none!important;}
    .block-container{max-width:620px;padding-top:120px;}
    .login-card{
        border:1px solid rgba(57,255,20,.55); border-radius:28px; padding:38px;
        background:linear-gradient(180deg,rgba(8,24,10,.94),rgba(4,8,5,.98));
        box-shadow:0 0 60px rgba(57,255,20,.22); text-align:center;
    }
    .login-title{font-size:3rem;font-weight:950;color:#39ff14;text-shadow:0 0 25px rgba(57,255,20,.3);}
    .login-sub{color:#a8ffc4;margin-bottom:24px;}
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="login-card">
        <div class="login-title">🐂 NANDI OS</div>
        <div class="login-sub">Private access only</div>
    </div>
    """, unsafe_allow_html=True)

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Unlock Nandi", use_container_width=True):
        try:
            correct_user = st.secrets["ADMIN_USERNAME"]
            correct_pass = st.secrets["ADMIN_PASSWORD"]
        except Exception:
            st.error("Security secrets are not configured yet. Add ADMIN_USERNAME and ADMIN_PASSWORD in Streamlit Secrets.")
            st.stop()

        if hmac.compare_digest(username, correct_user) and hmac.compare_digest(password, correct_pass):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Access denied")
    st.stop()

check_password()

st.markdown("""
<style>
:root{--green:#39ff14;--green2:#00ff66;--soft:#a8ffc4;--muted:#b7c7b9;--border:rgba(57,255,20,.45);}
.stApp{
    background: radial-gradient(circle at 30% 5%, rgba(57,255,20,.12), transparent 28%),
    radial-gradient(circle at 75% 15%, rgba(0,255,102,.08), transparent 25%),
    linear-gradient(180deg,#010301,#050805 45%,#020302); color:#f4fff5;
}
header[data-testid="stHeader"]{background:transparent;height:0;}
#MainMenu, footer, div[data-testid="stToolbar"], div[data-testid="stDecoration"]{display:none!important;}
.block-container{padding:1.4rem 2.4rem 2rem 2.4rem;max-width:1600px;}
section[data-testid="stSidebar"]{background:linear-gradient(180deg,rgba(57,255,20,.08),rgba(0,0,0,0) 35%),#050805;border-right:1px solid var(--border);}
[data-testid="stSidebar"] [role="radiogroup"] label{border:1px solid rgba(57,255,20,.12);border-radius:16px;padding:.55rem .75rem;background:rgba(255,255,255,.02);margin-bottom:.35rem;}
[data-testid="stWidgetLabel"]{display:none;}
.brand{display:flex;align-items:center;gap:14px;margin:8px 0 30px 0;}
.brand img{width:70px;height:70px;filter:drop-shadow(0 0 20px rgba(57,255,20,.55));}
.brand-fallback{font-size:64px;filter:drop-shadow(0 0 20px rgba(57,255,20,.55));}
.brand-title{font-size:2rem;font-weight:900;letter-spacing:-.05em}.brand-title span{color:var(--green)}
.brand-sub{font-size:.78rem;color:var(--soft);margin-top:2px}.nav-label{color:var(--green);font-weight:800;font-size:.8rem;letter-spacing:.15em;margin-bottom:10px}
.status-box{margin-top:210px;border:1px solid rgba(57,255,20,.35);border-radius:18px;padding:18px;background:rgba(4,25,8,.58);box-shadow:0 0 30px rgba(57,255,20,.08)}
.status-title{color:var(--green);font-size:1.1rem;font-weight:800;margin-bottom:8px}.status-dot{display:inline-block;width:11px;height:11px;border-radius:50%;background:var(--green);box-shadow:0 0 14px var(--green);margin-right:8px}
.topbar{display:flex;justify-content:flex-end;align-items:center;gap:16px;margin-bottom:16px;color:#fff;}
.icon-pill{width:50px;height:50px;border-radius:50%;border:1px solid rgba(57,255,20,.45);display:grid;place-items:center;background:rgba(255,255,255,.03);box-shadow:0 0 22px rgba(57,255,20,.10);font-size:1.35rem;}
.hero{min-height:300px;border:1px solid rgba(57,255,20,.38);border-radius:28px;padding:34px 38px;background:radial-gradient(circle at 20% 45%, rgba(57,255,20,.18), transparent 24%),radial-gradient(circle at 100% 100%, rgba(57,255,20,.16), transparent 26%),linear-gradient(135deg,rgba(10,23,12,.94),rgba(2,6,3,.98));box-shadow:0 0 0 1px rgba(57,255,20,.06),0 0 70px rgba(57,255,20,.10);position:relative;overflow:hidden;}
.hero:after{content:"";position:absolute;right:-60px;bottom:-30px;width:520px;height:220px;opacity:.55;background:linear-gradient(135deg, transparent 0 35%, rgba(57,255,20,.3) 36%, transparent 38% 100%),radial-gradient(circle at 80% 30%, rgba(57,255,20,.7), transparent 3%),linear-gradient(20deg, transparent 40%, rgba(57,255,20,.28) 41%, transparent 43%);clip-path:polygon(0 100%,28% 42%,42% 74%,58% 30%,76% 65%,100% 10%,100% 100%);}
.hero-content{display:flex;align-items:center;gap:40px;position:relative;z-index:2}
.hero-logo-wrap{width:245px;height:245px;border-radius:50%;border:3px solid rgba(57,255,20,.75);display:grid;place-items:center;box-shadow:0 0 45px rgba(57,255,20,.34)}
.hero-logo-wrap img{width:205px;height:205px;filter:drop-shadow(0 0 26px rgba(57,255,20,.65))}
.hero-fallback{font-size:150px;filter:drop-shadow(0 0 26px rgba(57,255,20,.65))}
.hero h1{font-size:5rem;line-height:.9;margin:0;font-weight:950;letter-spacing:.12em;color:var(--green);text-shadow:0 0 28px rgba(57,255,20,.25)}
.hero h1 span{color:white;text-shadow:none;letter-spacing:0}.hero h2{font-size:1.7rem;color:var(--green2);font-weight:500;margin:20px 0 26px}
.quote{font-size:1.05rem;color:#f2fff4}.quote b{color:var(--green);font-size:2rem}
.card{border:1px solid rgba(57,255,20,.32);border-radius:22px;padding:26px 28px;background:linear-gradient(180deg,rgba(12,32,15,.88),rgba(5,12,6,.94));box-shadow:0 0 45px rgba(57,255,20,.08);min-height:155px;}
.card-icon{font-size:2.3rem;color:var(--green);margin-bottom:12px}.card-label{font-size:1rem;color:#fff;margin-bottom:8px}.card-value{font-size:2.4rem;font-weight:900;color:#fff;line-height:1}.card-sub{color:var(--green);margin-top:10px;font-size:1rem}
.panel{border:1px solid rgba(57,255,20,.32);border-radius:24px;padding:28px;background:linear-gradient(180deg,rgba(8,22,10,.88),rgba(4,9,5,.95));box-shadow:0 0 45px rgba(57,255,20,.08);min-height:320px;}
.panel-title{color:var(--green);font-size:1.35rem;font-weight:850;margin-bottom:18px}.item{display:flex;gap:16px;align-items:flex-start;margin:18px 0;color:#fff}.num{min-width:34px;height:34px;border-radius:50%;border:1px solid var(--green);color:var(--green);display:grid;place-items:center;font-weight:800;background:rgba(57,255,20,.08)}
.small{color:var(--muted);font-size:.92rem}.action-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.action{border:1px solid rgba(57,255,20,.25);border-radius:18px;padding:24px;background:rgba(57,255,20,.05);font-size:1.1rem;color:#fff}.action b{color:var(--green);font-size:1.6rem;margin-right:10px}.big-action{grid-column:1/3;color:var(--green);font-weight:850}
</style>
""", unsafe_allow_html=True)

logo_html = f'<img src="{LOGO_URI}" />' if LOGO_URI else '<div class="brand-fallback">🐂</div>'
hero_logo_html = f'<img src="{LOGO_URI}" />' if LOGO_URI else '<div class="hero-fallback">🐂</div>'

with st.sidebar:
    st.markdown(f"""
    <div class="brand">{logo_html}<div><div class="brand-title"><span>NANDI</span> OS</div><div class="brand-sub">Personal AI Operating System</div></div></div>
    <div class="nav-label">NAVIGATION</div>
    """, unsafe_allow_html=True)
    page = st.radio("Navigation", ["🏠 Dashboard", "💬 Chat", "🧠 Memory", "🎯 Goals", "📈 Finance", "📄 Resume Vault", "⚙️ Settings"])
    st.markdown("""
    <div class="status-box"><div class="status-title">Nandi Status</div><div><span class="status-dot"></span><span style="color:#39ff14;font-weight:700">Online</span></div><div class="small" style="margin-top:14px">Always here. Always learning.</div></div>
    <div style="margin-top:18px;border:1px solid rgba(57,255,20,.18);border-radius:14px;padding:14px;color:#dfffe8">⌘ Version 2.1 Secure</div>
    """, unsafe_allow_html=True)
    if st.button("Logout"):
        st.session_state.authenticated = False
        st.rerun()

if page == "🏠 Dashboard":
    today = datetime.now().strftime("%d %B %Y | %A")
    st.markdown(f"""
    <div class="topbar"><div><div style="font-size:1.15rem;font-weight:800">Good morning, Shivdutt 👋</div><div class="small">{today}</div></div><div class="icon-pill">🔔</div><div class="icon-pill">🌙</div><div class="icon-pill">👤</div></div>
    """, unsafe_allow_html=True)
    st.markdown(f"""
    <section class="hero"><div class="hero-content"><div class="hero-logo-wrap">{hero_logo_html}</div><div><h1>NANDI <span>OS</span></h1><h2>Personal AI Operating System</h2><div class="quote"><b>“</b> Discipline today, freedom tomorrow.<br><span style="color:#39ff14">– Nandi</span></div></div></div></section>
    """, unsafe_allow_html=True)
    st.write("")
    c1,c2,c3,c4 = st.columns(4)
    cards = [("📋","Tasks Pending","3","Stay focused"),("💼","Applications","2","In Progress"),("📈","Market Overview","+0.82%","NIFTY 50 Today"),("₿","Bitcoin (BTC)","$66,215","Live Price")]
    for col, (ic,label,value,sub) in zip([c1,c2,c3,c4], cards):
        with col:
            st.markdown(f"""<div class="card"><div class="card-icon">{ic}</div><div class="card-label">{label}</div><div class="card-value">{value}</div><div class="card-sub">{sub}</div></div>""", unsafe_allow_html=True)
    st.write("")
    p1,p2 = st.columns([1.25,1])
    with p1:
        st.markdown("""
        <div class="panel"><div class="panel-title">📅 Today's Overview</div>
        <div class="item"><div class="num">3</div><div><b>Job applications pending</b><div class="small">Keep going!</div></div></div>
        <div class="item"><div class="num">1</div><div><b>Resume updated</b><div class="small">You're improving every day.</div></div></div>
        <div class="item"><div class="num">↗</div><div><b>NIFTY is up 0.82%</b><div class="small">Markets are looking positive.</div></div></div>
        <div class="item"><div class="num">₿</div><div><b>BTC is trading at $66,215</b><div class="small">Stay informed. Stay ahead.</div></div></div></div>
        """, unsafe_allow_html=True)
    with p2:
        st.markdown("""
        <div class="panel"><div class="panel-title">⚡ Quick Actions</div><div class="action-grid">
        <div class="action"><b>＋</b>New Task</div><div class="action"><b>◎</b>Add Goal</div>
        <div class="action"><b>⇧</b>Upload Resume</div><div class="action"><b>▥</b>Market Watch</div>
        <div class="action big-action">💬 Start Chat with Nandi</div></div></div>
        """, unsafe_allow_html=True)
elif page == "💬 Chat":
    st.markdown(f"""<section class="hero"><div class="hero-content"><div class="hero-logo-wrap">{hero_logo_html}</div><div><h1>CHAT</h1><h2>Talk to Nandi</h2><div class="quote">AI model connection will be added next.</div></div></div></section>""", unsafe_allow_html=True)
    question = st.text_input("Ask Nandi")
    if question:
        st.success(f"You asked: {question}")
else:
    st.markdown(f"""<section class="hero"><div class="hero-content"><div class="hero-logo-wrap">{hero_logo_html}</div><div><h1>{page.split(' ',1)[1].upper()}</h1><h2>Nandi OS Module</h2><div class="quote">This module will be connected next.</div></div></div></section>""", unsafe_allow_html=True)
