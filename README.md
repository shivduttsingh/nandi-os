# Nandi OS V2.1 Interactive Command Center

Nandi OS V2.1 is a private Streamlit command center for Shivdutt. It uses a clean white interface, green Nandi bull branding, clickable modules, local memory, Nandi Chat, Goals, Foundation, and the Nandi Financial Research Model One (NFRM-1).

The goal is not only to look strong. The base should actually be strong: private login, modular architecture, local memory, audit logs, risk discipline, and a clear advisor profile.

## What is included

- Private login using Streamlit secrets.
- White command-center dashboard with a green Nandi bull SVG mark.
- Clickable dashboard cards for Finance Research, Nandi Chat, Memory Core, and Goals.
- Live status cards for market pulse, memory, research engine, and today's focus.
- Optional 60-second auto-refresh while the app is open.
- Local SQLite memory.
- Paper journal database for finance research events.
- Foundation page for security, audit trail, risk engine, memory, and model-routing readiness.
- Local audit logging for login, memory, research, and paper journal actions.
- Modular folders for core engine, finance engine, config, data, and logs.

## Finance mode

Nandi Finance is Shivdutt's private financial intelligence advisor. It can guide, score, warn, explain, and remember. It is not a public advisory service, broker execution system, or guaranteed-profit engine.

The advisor layer should always prefer evidence over excitement:

Preferred language:

- Research View
- Advisor View
- Bullish Setup Detected
- Bearish Setup Detected
- No Valid Setup
- Call-side price movement scenario
- Put-side price movement scenario
- Market Structure
- Confidence Score
- Risk Zone
- Invalidation Level
- Research Zone
- Paper Journal
- Watchlist Candidate
- Avoid for Now
- Wait for Confirmation
- High Risk
- Attractive Setup

Default capital assumption is Rs. 25,000 with 2% max risk per research idea, so the research risk budget is Rs. 500.

## Foundation rules

- Private-first system for Shivdutt, not a public advisory product.
- Indian-market focus before global expansion.
- Risk control before opportunity chasing.
- Every serious output should include evidence, confidence, risk zone, and invalidation.
- Memory and journal should improve discipline over time.
- Important actions should be auditable in local logs.
- AI model routing should stay modular so Nandi can grow beyond one provider.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Secrets

Create `.streamlit/secrets.toml`:

```toml
ADMIN_USERNAME = "your_username"
ADMIN_PASSWORD = "your_password"
```

## Project structure

```text
nandi_os_v2/
  app.py
  requirements.txt
  README.md
  setup_v2.py
  config/
  core/
    foundation.py
  data/
  finance/
  logs/
  .streamlit/
```

## Next development ideas

- Real task database for Goals.
- Daily Updates and Market Pulse history.
- Knowledge base search.
- Background jobs.
- Multi-model routing for OpenAI, Gemini, DeepSeek, and local models.
