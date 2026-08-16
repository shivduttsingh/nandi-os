# Nandi V2 Release Notes

## Purpose

Nandi V2 replaces the previous collection of separate strategy screens with one explainable NIFTY decision engine. It is research software only and does not place broker orders.

## Live architecture

- **Market data:** NSE adapter for NIFTY spot and option-chain evidence, plus optional read-only Upstox V3 NIFTY candles.
- **Chart:** TradingView Lightweight Charts™ renders Upstox OHLC candles; the hosted TradingView NSE widget is not a hidden decision-data feed.
- **Strike window:** ATM plus five strikes above and five below.
- **Decision states:** `BUY CE`, `BUY PE`, `PREPARE CE`, `PREPARE PE`, `NO TRADE`.
- **Trade threshold:** default 75/100 setup-quality score.
- **Email threshold:** default 80/100 after fresh-snapshot confirmation.
- **Confirmation:** default three distinct NSE option-chain snapshots on the same side.
- **Session gate:** live BUY decisions are blocked outside the configured NSE regular session.

## Evidence model

The unified score combines:

- NIFTY market structure
- rolling OI positioning
- option-premium confirmation
- support/resistance location
- momentum
- option volume
- reward-risk
- data freshness

The score is a setup-quality score, not a guaranteed win probability.

## Rolling OI

NSE cumulative fields are converted into changes between Nandi's own successful snapshots. The first snapshot is a neutral baseline. A repeated/non-advancing NSE option-chain timestamp is rejected and cannot advance confirmation.

## Trade lifecycle

Nandi manages research state through:

`PREPARE -> ACTIVE -> HOLD -> BOOK PARTIAL / TRAIL -> EXIT`

The lifecycle holds a confirmed direction for 15 minutes by default, so opposite evidence becomes a warning instead of an instant CE/PE flip. After an exit, a default 5-minute cooldown blocks immediate reversal. Spot invalidation, Target 1 and Target 2 remain immediate risk actions. Lifecycle transitions are persisted in SQLite and restored after an app restart. Active states from an earlier trading date are not resumed as current-day trades.

## Persistence and replay

SQLite stores:

- decision history
- successful/failed alert records
- trade lifecycle events
- captured NSE replay frames and matching market context

The Results page reconstructs completed ACTIVE-to-EXIT trades from those events and reports daily, weekly and monthly totals in underlying NIFTY points. It does not invent option-premium P&L.

The Replay page re-runs the V2 engine deterministically on captured frames using the same fresh-snapshot confirmation rule as live mode. Nandi never invents missing historical frames.

## Streamlit Secrets

```toml
[auth]
username = "YOUR_PRIVATE_USERNAME"
password = "YOUR_PRIVATE_PASSWORD"

[alerts]
host = "smtp.example.com"
port = 587
username = "YOUR_SMTP_USERNAME"
password = "YOUR_SMTP_PASSWORD"
sender = "YOUR_SENDER_EMAIL"
recipient = "YOUR_PERSONAL_ALERT_EMAIL"
use_tls = true

[nse]
holidays = ["YYYY-MM-DD"]

[upstox]
access_token = "YOUR_READ_ONLY_ACCESS_TOKEN"
```

The Upstox section is optional but required for the 15-minute candlestick chart. Do not commit real credentials.

## Deployment checklist

1. Confirm Streamlit Secrets contain real private auth credentials.
2. Configure SMTP secrets if 80+ entry emails are required.
3. Add current NSE trading holidays to the `nse.holidays` list.
4. Confirm the deployment has persistent storage if decision/replay history must survive container replacement.
5. Configure a read-only Upstox token if the 15-minute candlestick chart is required.
6. Confirm the chart labels Upstox as its data source and renders with TradingView Lightweight Charts™.
7. Confirm only completed 15-minute candles enter market-structure context.
8. Confirm NSE data timestamps are visible and advancing during market hours.
9. Confirm the first rolling OI snapshot remains neutral and no BUY is emitted solely from the baseline.
10. Confirm a BUY requires the configured number of distinct fresh NSE snapshots.
11. Confirm an opposite signal cannot flip an active trade during its minimum hold, while a stop can still exit immediately.
12. Confirm one successful 80+ entry setup produces only one email per day/side/strike/expiry.
13. Confirm CI is green before merging to `main`.

## Production NSE note

The current adapter is intentionally isolated behind the V2 data models. For production-grade or licensed real-time operation, replace the public-site adapter with an authorised NSE Data feed while keeping the decision engine interface unchanged.
