# Nandi V2

Nandi V2 is a private, explainable NIFTY options research dashboard.

## Current architecture

- **NSE option chain:** nearest expiry, ATM ±5 strikes only.
- **NSE NIFTY spot:** used for the decision engine and momentum history.
- **TradingView:** embedded NIFTY chart display only; it is not a hidden signal feed.
- **Unified engine:** independently scores CE and PE from market structure, OI positioning, premium confirmation, location, momentum, volume, risk-reward and data freshness.
- **Trade gate:** BUY CE or BUY PE requires score 75+ by default and no hard blocker.
- **Email gate:** only confirmed BUY CE / BUY PE setups at score 80+ are eligible for email.
- **No broker orders:** research and paper-observation only.
- **No Upstox fallback:** missing NSE data produces NO TRADE.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Configure credentials with environment variables or Streamlit Secrets:

```toml
[auth]
username = "your-user"
password = "your-password"

[alerts]
host = "smtp.example.com"
port = 587
username = "smtp-user"
password = "smtp-password"
sender = "sender@example.com"
recipient = "recipient@example.com"
use_tls = true
```

## Important data note

The included NSE public-site adapter is conservative and rate-limited. The screen can recalculate every second from the latest valid state, but it does not claim that NSE OI changes every second. Each source timestamp is shown separately. For production-grade real-time use, replace `NSEPublicClient` with an authorised NSE Data feed while preserving the same models and engine interface.
