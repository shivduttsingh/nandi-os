# Nandi V2

Nandi V2 is a private, explainable NIFTY options research dashboard built around one unified decision engine.

## Current architecture

- **NSE option chain:** nearest expiry, ATM ±5 strikes only.
- **NSE NIFTY spot:** used for the decision engine and momentum history.
- **TradingView:** embedded NIFTY chart display only; it is not a hidden signal feed.
- **Unified engine:** independently scores CE and PE from market structure, rolling OI positioning, premium confirmation, location, momentum, volume, reward-risk and data freshness.
- **Rolling OI:** Nandi compares successive successful NSE snapshots instead of treating exchange day-level COI as short-term movement. The first snapshot is a neutral baseline.
- **Trade gate:** BUY CE or BUY PE requires score 75+ by default, directional edge, fresh data and no hard blocker.
- **Confirmation:** default three distinct fresh NSE option-chain snapshots on the same side before BUY.
- **NSE session gate:** closed/pre-market/weekend/configured-holiday states cannot emit a live BUY.
- **Email gate:** only confirmed BUY CE / BUY PE setups at score 80+ are eligible for email; successful entry alerts are deduplicated.
- **Trade lifecycle:** PREPARE -> ACTIVE -> HOLD -> BOOK PARTIAL / TRAIL -> EXIT.
- **Persistence:** decisions, lifecycle transitions, alerts and captured replay frames are stored in SQLite.
- **Replay:** stored NSE frames can be deterministically re-run through the V2 engine and the same confirmation/lifecycle logic.
- **No broker orders:** research and paper-observation only.
- **No Upstox fallback:** missing or stale NSE data produces NO TRADE.

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

[nse]
holidays = ["YYYY-MM-DD"]
```

Do not commit real credentials.

## Application pages

- **Live Decision:** TradingView NIFTY chart, final CE/PE/NO TRADE state, entry, stop, targets and persistent lifecycle state.
- **Evidence:** score components, limited ATM ±5 NSE table and freshness/session status.
- **History:** decision history and persisted lifecycle transitions.
- **Replay:** deterministic replay of NSE frames captured by Nandi.
- **Settings:** decision thresholds, refresh cadence and SMTP configuration status.

## Important data note

The included NSE public-site adapter is conservative and rate-limited. The screen can recalculate every second from the latest valid state, but it does not claim that NSE OI changes every second. Each source timestamp is shown separately. For production-grade or licensed real-time use, replace `NSEPublicClient` with an authorised NSE Data feed while preserving the same models and engine interface.

The Nandi score is a setup-quality score, not a guaranteed probability of profit.

See `NANDI_V2_RELEASE.md` for the deployment checklist.
