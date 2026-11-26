# ATLAS — NIFTY Options Research Terminal

ATLAS is a browser-based Streamlit research workspace for NIFTY options. It is paper-research only and never routes broker orders.

## Current website work

- Command Centre, existing A/B/C strategy logic, historical OHLCV backtest and paper ledger retained.
- Universal Chart Explorer: chart any loaded or uploaded CSV/Excel dataset, select date ranges, X/Y columns, multiple series, chart types, OHLC candlesticks, heatmaps, overlays, and inspect/download the underlying rows.
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
- The current underlying-candle backtest is not a realistic options-premium backtest. It does not establish option strategy profitability.
- Research-candidate strategies are hypotheses only until they pass chronological out-of-sample, walk-forward and prospective paper validation.
- No performance claim or guaranteed accuracy is made.

## Safety

- Paper trading only.
- No live broker orders or order routing.
- Never put API tokens in source code, uploaded files or public browser JavaScript. Use private Streamlit secrets or environment variables.
