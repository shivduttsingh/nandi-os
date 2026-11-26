# ATLAS — NIFTY Options Research Terminal

ATLAS is a browser-based Streamlit research workspace for NIFTY options. It is paper-research only and never routes broker orders.

## Current website work

- Command Centre, existing A/B/C strategy logic, historical OHLCV backtest and paper ledger retained.
- Universal Chart Explorer: chart loaded, uploaded or Upstox-retrieved datasets, select date ranges, X/Y columns, multiple series, chart types, OHLC candlesticks, heatmaps, overlays, and inspect/download the underlying rows.
- Historical Options Replay: reloads timestamped option-chain snapshots from the capture archive, tests individual OI evidence gates and OI V1, and displays the trade ledger and cumulative premium-point curve.
- Data-integrity checks: rejects invalid OHLCV ranges and negative volume; intraday underlying backtests do not carry positions across sessions.
- Open Strategy Library: A/B/C plus an expandable catalogue of strategy hypotheses. Research candidates are not represented as validated or live strategies.
- Research & Model Governance: data-quality checks, evidence reporting and explicit limits on current backtest claims.
- Read-only Upstox and best-effort NSE adapters remain separate from the charting workspace.

## Run locally

\`\`\`bash
pip install -r requirements.txt
streamlit run app.py
\`\`\`

## Chart any data

Open **Chart Explorer** and upload one or more CSV or Excel files. For any dataset, choose available columns, date range, chart type, and multiple numeric series. Plotly charts support interactive zoom, pan, hover and range selection. The uploaded data is held in the current app session; persistent shared storage still requires a configured database.

## Data and validation limitations

- Live data requires the user's own read-only Upstox token and valid API entitlements.
- NSE public website access is best-effort and is not a substitute for licensed exchange data.
- Upstox historical candle availability and request windows vary by interval. Option-chain snapshots are not the same as a full historical option-chain archive.
- The underlying-candle A/B/C backtest is a spot-structure prototype, not an options-premium result.
- Historical Options Replay uses archived option-chain snapshots and the current replay engine's premium stop/target assumptions. It is not a tick-accurate execution simulator: bid/ask slippage, intrabar path, exchange charges, brokerage, taxes and complete contract lifecycle must be added and independently validated before relying on P&L.
- Option-chain history only begins when the worker is running. It cannot recreate past snapshots that were never collected; current worker capture is nearest-weekly unless explicitly extended.
- Research-candidate strategies are hypotheses only until they pass chronological out-of-sample, walk-forward and prospective paper validation.
- No performance claim or guaranteed accuracy is made.

## Safety

- Paper trading only.
- No live broker orders or order routing.
- Never put API tokens in source code, uploaded files or public browser JavaScript. Use private Streamlit secrets or environment variables.


## Continuous option-chain capture (local / Docker)

The repository includes a separate read-only capture worker. It polls the Upstox NIFTY option chain during the NSE session, runs the research engine, and persists timestamped snapshots, decisions, worker health, alerts and daily summaries in SQLite. It does not place orders.

1. Copy `.env.example` to `.env` and set a valid read-only `UPSTOX_ACCESS_TOKEN`.
2. Set `NANDI_NSE_HOLIDAYS` to the official holiday dates for the year.
3. Run `docker compose up -d --build`.
4. Check `docker compose logs -f worker` and `docker compose ps`.

The dashboard and worker share the Docker named volume at `/app/data`. This is durable across container restarts on that host, but it is not a cloud database and does not synchronize separate Streamlit Cloud instances. The dashboard's paper ledger and the worker's option archive are separate SQLite databases within that volume. For a hosted deployment, provision a persistent database/storage service and configure private credentials before relying on long-term archives. Free tiers have quotas and may sleep; uninterrupted collection requires an always-on host or a continuously running local machine.

Preflight without requesting market data: `docker compose run --rm worker python worker.py --check`.
