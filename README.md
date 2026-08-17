# Nandi V2

Nandi V2 is a private, explainable NIFTY options research dashboard built around one unified decision path: fundamental context, technical-family consensus, OI/execution evidence and a stable risk lifecycle.

## Current architecture

- **NSE option chain:** nearest expiry, ATM ±5 strikes only.
- **NSE NIFTY spot:** used for the live price and limited decision-context fallback; it is never converted into guessed OHLC candles.
- **Upstox candles:** optional read-only V3 historical + intraday OHLCV supplies the visible NIFTY 50 chart, completed 15-minute technical evidence and the exact nearest-expiry ATM CE/PE premium charts.
- **Chart rendering:** NIFTY 50 and both ATM option charts use the same candlestick renderer and Upstox market-data source. The chart path contains no broker-order action.
- **NIFTY + ATM confirmation:** an additional paper-validation strategy compares matching completed NIFTY, ATM CE and ATM PE candles and reports CONFIRM CE, CONFIRM PE, WAIT or UNAVAILABLE. Its agreement score is not a win probability and does not change the final BUY gate before validation.
- **Unified engine:** independently scores CE and PE from market structure, rolling OI positioning, premium confirmation, location, momentum, volume, reward-risk and data freshness.
- **Fundamental Desk:** stores sourced, freshness-gated global, macro, flow, heavyweight-earnings and event-risk inputs.
- **Technical Lab:** exposes 25 indicators grouped into five evidence families so correlated indicators cannot dominate the decision by count alone.
- **Confluence gate:** the OI engine proposes the side; technical and fundamental pillars may confirm or veto it. Missing, stale, sideways or conflicting pillars block a new trade.
- **Option execution plan:** every confirmed setup identifies the nearest-expiry ATM CE/PE contract, current NSE quote, conservative ask/LTP entry reference, 5% premium stop and 1.5R/2.5R premium targets. Missing premiums and bid/ask spreads above 3% block BUY. Premium monitoring follows the configured option-chain refresh and is not broker tick data.
- **Rolling OI:** Nandi compares successive successful NSE snapshots instead of treating exchange day-level COI as short-term movement. The first snapshot is a neutral baseline.
- **Trade gate:** BUY CE or BUY PE requires both the OI proposal and final weighted confluence score to reach 75+ by default, plus directional edge, fresh data and no hard blocker.
- **Confirmation:** default three distinct fresh NSE option-chain snapshots on the same side before BUY.
- **NSE session gate:** closed/pre-market/weekend/configured-holiday states cannot emit a live BUY.
- **Email gate:** only confirmed BUY CE / BUY PE setups at score 80+ are eligible for email; successful entry alerts are deduplicated.
- **Trade lifecycle:** PREPARE -> ACTIVE -> HOLD -> BOOK PARTIAL / TRAIL -> EXIT, with a default 15-minute minimum hold, 45-minute maximum hold and 5-minute reversal cooldown. Spot or premium stops and targets remain immediate risk exits.
- **Persistence:** decisions, lifecycle transitions, alerts and captured replay frames are stored in SQLite.
- **Results:** completed lifecycle events are summarized daily, weekly and monthly in recorded option-premium results when available, alongside underlying NIFTY points. Missing premiums are never estimated.
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

The Upstox token is optional, but required for technical candles, completed-candle structure and the live ATM CE/PE premium charts. Do not commit real credentials.

## Application pages

- **Command Center:** read-only Upstox NIFTY 50 chart, side-by-side live ATM CE/PE charts, chart-confirmation strategy, three-pillar agreement, exact contract plan, premium/spot risk levels and hold guidance.
- **NIFTY Option Charts:** dedicated Upstox NIFTY 50 chart plus auto-rolling nearest-expiry ATM CE and PE premium charts, all read only.
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

See `NANDI_ARCHITECTURE.md` for the complete three-pillar design, accuracy programme and multi-user delivery phases.

See `NANDI_V2_RELEASE.md` for the deployment checklist.
