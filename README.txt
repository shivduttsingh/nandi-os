from pathlib import Path
import base64
from datetime import datetime
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
ASSETS = APP_DIR / "assets"
LOGO = ASSETS / "nandi_bull_logo.png"
MOUNTAIN = ASSETS / "nandi_mountain.png"

def img_uri(path: Path) -> str:
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("utf-8")

logo_uri = img_uri(LOGO)
mountain_uri = img_uri(MOUNTAIN)

st.set_page_config(page_title="Nandi OS", page_icon=LOGO if LOGO.exists() else "🐂", layout="wide")

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
:root {{
  --bg:#020402;
  --panel:#071009;
  --panel2:#0a170d;
  --green:#39ff14;
  --green2:#00ff66;
  --text:#f5fff6;
  --muted:#aab7ad;
  --line:rgba(57,255,20,.35);
  --line2:rgba(57,255,20,.18);
  --glow:0 0 22px rgba(57,255,20,.25),0 0 70px rgba(57,255,20,.08);
}}
* {{ font-family:Inter,Segoe UI,Arial,sans-serif; }}
html,body,.stApp {{
  background: radial-gradient(circle at 50% 0%, rgba(57,255,20,.08), transparent 26%), linear-gradient(180deg,#010201,#020402 40%,#000 100%);
  color:var(--text);
}}
header[data-testid="stHeader"], div[data-testid="stToolbar"], div[data-testid="stDecoration"], #MainMenu, footer {{ display:none!important; }}
.block-container {{ padding:0!important; max-width:100%!important; }}
section[data-testid="stSidebar"] {{ display:none!important; }}

.nandi-shell {{
  min-height:100vh;
  display:grid;
  grid-template-columns: 325px 1fr;
  background:rgba(0,0,0,.72);
}}
.nandi-sidebar {{
  border-right:1px solid var(--line);
  background:linear-gradient(180deg,rgba(57,255,20,.06),rgba(0,0,0,0) 30%),#030604;
  padding:38px 24px 24px;
  position:relative;
}}
.brand {{ display:flex;align-items:center;gap:16px;margin-bottom:48px; }}
.brand img {{ width:82px;height:82px;border-radius:50%;filter:drop-shadow(0 0 22px rgba(57,255,20,.8)); }}
.brand-title {{ font-size:30px;font-weight:900;letter-spacing:-.03em;line-height:1; }}
.brand-title .green {{ color:var(--green); }}
.brand-sub {{ color:var(--green);font-size:11px;margin-top:8px; }}
.nav-title {{ color:var(--green);font-weight:800;letter-spacing:.09em;font-size:13px;margin:22px 0 16px; }}
.nav-item {{
  height:52px;border-radius:12px;display:flex;align-items:center;gap:16px;padding:0 16px;margin:10px 0;
  color:#fff;font-size:17px;font-weight:600;border:1px solid transparent;
}}
.nav-item.active {{ background:linear-gradient(90deg,rgba(57,255,20,.22),rgba(57,255,20,.07)); border-color:rgba(57,255,20,.55); box-shadow:var(--glow); }}
.nav-item span {{ color:#9cff96;font-size:24px; width:28px;text-align:center; }}
.status {{ position:absolute;left:24px;right:24px;bottom:100px;border:1px solid var(--line2);border-radius:18px;padding:20px;background:rgba(8,22,10,.72); }}
.status h3 {{ color:var(--green);margin:0 0 12px;font-size:18px; }}
.dot {{ display:inline-block;width:13px;height:13px;border-radius:50%;background:var(--green);box-shadow:0 0 16px var(--green);margin-right:10px; }}
.version {{ position:absolute;left:24px;right:24px;bottom:28px;border:1px solid rgba(57,255,20,.16);border-radius:14px;padding:14px 18px;color:#dfffe3;background:rgba(57,255,20,.04); }}
.main {{ padding:28px 34px 34px; }}
.topbar {{ height:62px;display:flex;justify-content:flex-end;align-items:center;gap:18px;margin-bottom:12px; }}
.greeting {{ margin-right:20px;text-align:left; }}
.greeting b {{ font-size:18px; }}
.greeting div {{ color:var(--muted);font-size:14px;margin-top:5px; }}
.round-btn {{ width:50px;height:50px;border-radius:50%;display:grid;place-items:center;border:1px solid var(--line);color:#dfffe2;background:rgba(255,255,255,.03);font-size:24px;box-shadow:0 0 24px rgba(57,255,20,.08); }}
.hero {{
  border:1px solid var(--line);border-radius:22px;min-height:268px;display:grid;grid-template-columns: 340px 1fr 420px;align-items:center;
  background:linear-gradient(90deg,rgba(8,22,10,.85),rgba(3,8,4,.95));overflow:hidden;box-shadow:var(--glow);position:relative;
}}
.hero:before {{ content:"";position:absolute;inset:0;background:radial-gradient(circle at 22% 30%,rgba(57,255,20,.12),transparent 24%),radial-gradient(circle at 72% 10%,rgba(57,255,20,.08),transparent 20%);pointer-events:none; }}
.hero-logo {{ position:relative;z-index:2;text-align:center; }}
.hero-logo img {{ width:275px;height:275px;border-radius:50%;filter:drop-shadow(0 0 40px rgba(57,255,20,.65)); }}
.hero-text {{ position:relative;z-index:2; }}
.hero-text h1 {{ font-size:66px;line-height:.95;letter-spacing:.08em;margin:0;font-weight:900;color:var(--green);text-shadow:0 0 28px rgba(57,255,20,.3); }}
.hero-text h1 em {{ color:#fff;font-style:normal;letter-spacing:.01em;text-shadow:none; }}
.hero-text h2 {{ color:var(--green);font-size:26px;font-weight:500;margin:18px 0 28px; }}
.quote {{ color:#fff;font-size:17px;line-height:1.7; }}
.quote b {{ color:var(--green);font-size:34px;line-height:0; }}
.hero-mountain {{ align-self:stretch;background-image:linear-gradient(90deg,rgba(3,8,4,.1),rgba(3,8,4,.05)),url('{mountain_uri}');background-size:cover;background-position:center;opacity:.92; }}
.card-row {{ display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin-top:22px; }}
.kpi {{ border:1px solid var(--line2);border-radius:18px;min-height:138px;background:linear-gradient(180deg,rgba(10,25,12,.82),rgba(4,9,5,.92));display:grid;grid-template-columns:70px 1fr 25px;align-items:center;padding:22px;box-shadow:0 0 30px rgba(57,255,20,.06); }}
.kpi-icon {{ color:var(--green);font-size:42px;text-shadow:0 0 18px rgba(57,255,20,.55); }}
.kpi-label {{ color:#fff;font-size:15px;font-weight:600;margin-bottom:9px; }}
.kpi-value {{ color:#fff;font-size:34px;font-weight:800;line-height:1; }}
.kpi-sub {{ color:var(--green);font-size:15px;margin-top:10px; }}
.arrow {{ color:var(--green);font-size:30px; }}
.lower {{ display:grid;grid-template-columns:1.15fr .85fr;gap:20px;margin-top:20px; }}
.panel {{ border:1px solid var(--line2);border-radius:22px;background:linear-gradient(180deg,rgba(9,20,11,.85),rgba(4,8,5,.95));min-height:315px;padding:26px 28px;box-shadow:0 0 36px rgba(57,255,20,.06); }}
.panel h3 {{ margin:0 0 22px;color:var(--green);font-size:20px; }}
.overview-grid {{ display:grid;grid-template-columns:1fr 1fr;gap:18px; }}
.list-item {{ display:flex;gap:15px;margin:18px 0;align-items:flex-start; }}
.num {{ width:34px;height:34px;border-radius:50%;display:grid;place-items:center;border:1px solid var(--green);color:var(--green);box-shadow:0 0 14px rgba(57,255,20,.16);font-weight:800; }}
.li-title {{ color:#fff;font-size:17px;font-weight:600; }}
.li-sub {{ color:var(--muted);font-size:14px;margin-top:5px; }}
.priority .num {{ background:rgba(57,255,20,.08); }}
.action-grid {{ display:grid;grid-template-columns:1fr 1fr;gap:18px; }}
.action {{ height:84px;border-radius:14px;border:1px solid var(--line2);background:rgba(57,255,20,.055);display:flex;align-items:center;gap:18px;padding:0 24px;color:#fff;font-size:18px;font-weight:600; }}
.action span {{ color:var(--green);font-size:34px; }}
.action.big {{ grid-column:1/3;color:var(--green); }}
@media(max-width:1200px){{.nandi-shell{{grid-template-columns:260px 1fr}}.hero{{grid-template-columns:260px 1fr}}.hero-mountain{{display:none}}.card-row{{grid-template-columns:repeat(2,1fr)}}.lower{{grid-template-columns:1fr}}.hero-text h1{{font-size:46px}}.hero-logo img{{width:220px;height:220px}}}}
</style>
""", unsafe_allow_html=True)

now = datetime.now().strftime("%B %d, %Y | %A")
logo_html = f'<img src="{logo_uri}" alt="Nandi bull logo">' if logo_uri else '<span>🐂</span>'

st.markdown(f"""
<div class="nandi-shell">
  <aside class="nandi-sidebar">
    <div class="brand">
      {logo_html}
      <div><div class="brand-title"><span class="green">NANDI</span> OS</div><div class="brand-sub">Personal AI Operating System</div></div>
    </div>
    <div class="nav-title">NAVIGATION</div>
    <div class="nav-item active"><span>⌂</span>Dashboard</div>
    <div class="nav-item"><span>☵</span>Chat</div>
    <div class="nav-item"><span>♙</span>Memory</div>
    <div class="nav-item"><span>◎</span>Goals</div>
    <div class="nav-item"><span>↗</span>Finance</div>
    <div class="nav-item"><span>▤</span>Resume Vault</div>
    <div class="nav-item"><span>⚙</span>Settings</div>
    <div class="status"><h3>Nandi Status</h3><div><span class="dot"></span><b style="color:var(--green)">Online</b></div><p style="color:var(--muted);font-size:14px;margin:16px 0 0">Always here. Always learning.</p></div>
    <div class="version">⌘ &nbsp; Version 2.0.0</div>
  </aside>
  <main class="main">
    <div class="topbar"><div class="greeting"><b>Good morning, Shivdutt 👋</b><div>{now}</div></div><div class="round-btn">♢</div><div class="round-btn">☾</div><div class="round-btn">♙</div></div>
    <section class="hero">
      <div class="hero-logo">{logo_html}</div>
      <div class="hero-text"><h1>NANDI <em>OS</em></h1><h2>Personal AI Operating System</h2><div class="quote"><b>“</b> Discipline today, freedom tomorrow.<br><span style="color:var(--green)">– Nandi</span></div></div>
      <div class="hero-mountain"></div>
    </section>
    <section class="card-row">
      <div class="kpi"><div class="kpi-icon">▣</div><div><div class="kpi-label">Tasks Pending</div><div class="kpi-value">3</div><div class="kpi-sub">Stay focused</div></div><div class="arrow">›</div></div>
      <div class="kpi"><div class="kpi-icon">▤</div><div><div class="kpi-label">Applications</div><div class="kpi-value">2</div><div class="kpi-sub">In Progress</div></div><div class="arrow">›</div></div>
      <div class="kpi"><div class="kpi-icon">↗</div><div><div class="kpi-label">Market Overview</div><div class="kpi-value">+0.82%</div><div class="kpi-sub">NIFTY 50 Today</div></div><div class="arrow">›</div></div>
      <div class="kpi"><div class="kpi-icon">₿</div><div><div class="kpi-label">Bitcoin (BTC)</div><div class="kpi-value">$66,215</div><div class="kpi-sub">Live Price</div></div><div class="arrow">›</div></div>
    </section>
    <section class="lower">
      <div class="panel"><h3>▣ &nbsp; Today's Overview</h3><div class="overview-grid"><div><div class="list-item"><div class="num">3</div><div><div class="li-title">Job applications pending</div><div class="li-sub">Keep going!</div></div></div><div class="list-item"><div class="num">1</div><div><div class="li-title">Resume updated</div><div class="li-sub">You're improving every day.</div></div></div><div class="list-item"><div class="num">↗</div><div><div class="li-title">NIFTY is up 0.82%</div><div class="li-sub">Markets are looking positive.</div></div></div><div class="list-item"><div class="num">₿</div><div><div class="li-title">BTC is trading at $66,215</div><div class="li-sub">Stay informed. Stay ahead.</div></div></div></div><div class="priority"><h3>☆ &nbsp; Today's Priority</h3><div class="list-item"><div class="num">1</div><div class="li-title">Goldman follow-up</div></div><div class="list-item"><div class="num">2</div><div class="li-title">Resume refinement</div></div><div class="list-item"><div class="num">3</div><div class="li-title">Credit analysis study</div></div><div class="list-item"><div class="num">4</div><div class="li-title">Learn something new</div></div></div></div></div>
      <div class="panel"><h3>⚡ &nbsp; Quick Actions</h3><div class="action-grid"><div class="action"><span>＋</span>New Task</div><div class="action"><span>◎</span>Add Goal</div><div class="action"><span>⇧</span>Upload Resume</div><div class="action"><span>▥</span>Market Watch</div><div class="action big"><span>☵</span>Start Chat with Nandi</div></div></div>
    </section>
  </main>
</div>
""", unsafe_allow_html=True)
