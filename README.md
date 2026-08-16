# Nandi V2

Nandi V2 is a private, explainable NIFTY options research dashboard built around one unified decision path: fundamental context, technical-family consensus, OI/execution evidence and a stable risk lifecycle.

## Current architecture

- **NSE option chain:** nearest expiry, ATM ±5 strikes only.
- **NSE NIFTY spot:** used for the live price and as the chart fallback.
- **Upstox NIFTY candles:** optional read-only V3 historical + intraday OHLCV supplies enough completed 15-minute history for indicator warm-up, current-day market structure and the candlestick chart.
- **TradingView:** Lightweight Charts™ renders the Upstox candles; the hosted NSE widget is not used as a signal feed.
- **Unified engine:** independently scores CE and PE from market structure, rolling OI positioning, premium confirmation, location, momentum, volume, reward-risk and data freshness.
- **Fundamental Desk:** stores sourced, freshness-gated global, macro, flow, heavyweight-earnings and event-risk inputs.
- **Technical Lab:** exposes 25 indicators grouped into five evidence families so correlated indicators cannot dominate the decision by count alone.
- **Confluence gate:** the OI engine proposes the side; technical and fundamental pillars may confirm or veto it. Missing, stale, sideways or conflicting pillars block a new trade.
- **Rolling OI:** Nandi compares successive successful NSE snapshots instead of treating exchange day-level COI as short-term movement. The first snapshot is a neutral baseline.
- **Trade gate:** BUY CE or BUY PE requires score 75+ by default, directional edge, fresh data and no hard blocker.
- **Confirmation:** default three distinct fresh NSE option-chain snapshots on the same side before BUY.
- **NSE session gate:** closed/pre-market/weekend/configured-holiday states cannot emit a live BUY.
- **Email gate:** only confirmed BUY CE / BUY PE setups at score 80+ are eligible for email; successful entry alerts are deduplicated.
- **Trade lifecycle:** PREPARE -> ACTIVE -> HOLD -> BOOK PARTIAL / TRAIL -> EXIT, with a default 15-minute direction lock and 5-minute reversal cooldown. Stops and targets remain immediate risk exits.
- **Persistence:** decisions, lifecycle transitions, alerts and captured replay frames are stored in SQLite.
- **Results:** completed lifecycle events are summarized daily, weekly and monthly in underlying NIFTY points.
- **Replay:** stored NSE frames can be deterministically re-run through the V2 engine and the same confirmation/lifecycle logic.
- **No broker orders:** research and paper-observation only.
- **No broker fallback:** missing or stale NSE option-chain data produces NO TRADE. Missing Upstox candles fall back to the NSE spot chart and never trigger broker activity.

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

[upstox]
access_token = "YOUR_READ_ONLY_ACCESS_TOKEN"
```

The Upstox token is optional, but required for the candlestick chart and completed-candle structure. Do not commit real credentials.

## Application pages

- **Command Center:** Upstox/TradingView-style NIFTY chart, three-pillar agreement, stable CE/PE lifecycle state, entry, stop and targets.
- **Fundamental Desk:** sourced market-context snapshot, coverage, freshness and bias.
- **Technical Lab:** Nandi Top 10 operator view, family consensus and all 25 individual indicator calculations, with source/range/coverage visibility.
- **OI & Execution:** score components, limited ATM ±5 NSE table and freshness/session status.
- **History:** decision history and persisted lifecycle transitions.
- **Replay:** deterministic replay of NSE frames captured by Nandi.
- **Results:** daily, weekly and monthly completed-trade summaries plus an auditable ledger.
- **Settings:** decision thresholds, stable hold/cooldown controls, refresh cadence and configuration status.

## Important data note

The included NSE public-site adapter is conservative and rate-limited. The screen can recalculate every second from the latest valid state, but it does not claim that NSE OI changes every second. Each source timestamp is shown separately. The forming Upstox candle is displayed but excluded from completed-candle market structure. For production-grade or licensed real-time use, replace `NSEPublicClient` with an authorised NSE Data feed while preserving the same models and engine interface.

The Nandi score is a setup-quality score, not a guaranteed probability of profit. New fundamental inputs initially come from the authenticated research desk; authorised automated providers can later write to the same auditable factor contract.

See `NANDI_ARCHITECTURE.md` for the complete two-pillar design, accuracy programme and multi-user delivery phases.

See `NANDI_V2_RELEASE.md` for the deployment checklist.
