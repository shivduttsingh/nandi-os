# ATLAS — NIFTY Options Research Terminal

ATLAS replaces the former Nandi user interface in this repository. It is a browser-based Streamlit research terminal for NIFTY intraday options, strategy comparison and paper-trade recordkeeping.

## Current release: Research Build 0.1

### Included
- Strategy A: opening-range breakout continuation.
- Strategy B: failed-breakout reversal.
- Strategy C: combined selector that rejects conflicting same-bar directions.
- CSV import for timestamped 5-minute NIFTY OHLCV research data.
- Historical signal review and underlying-points backtest with configurable reward/risk and cost assumption.
- SQLite paper ledger for manually recording observed option symbol, CE/PE, expiry, quantity, entry premium, stop, target and exit premium.
- ₹20,000 starting paper-capital display and initial research risk controls.
- No broker-order routing.

### Important limitations — do not mistake these for completed capabilities
- **Live market data is not connected in this release.** No live NIFTY spot, option-chain OI, IV, Greeks, bid/ask or option premium is displayed.
- The included backtest evaluates signals on the NIFTY underlying OHLCV series. It is **not** an options-premium backtest and does not model option expiry, volatility, theta, spreads, slippage at option quotes, lot-size affordability or exchange/broker charges.
- The paper ledger records manually observed option premiums. It does not submit orders or automatically fill trades.
- SQLite storage on a hosted ephemeral filesystem may not survive app restarts or container replacement. Export the ledger regularly until durable database storage is configured.
- No performance claim, win rate or expected monthly income is asserted. A strategy must pass out-of-sample, walk-forward and option-level paper validation before it is considered eligible for real capital.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app uses Python as its Streamlit server runtime; the user experience is a browser website, not a notebook.

## CSV format

Required columns:

```csv
timestamp,open,high,low,close,volume
2026-11-20 09:15:00,26000,26020,25990,26010,12345
```

Optional column: `vwap`. Use consistent 5-minute candles in India Standard Time. The opening-range logic expects at least two candles between 09:15 and 09:30.

## Risk and safety
- Research capital display: ₹20,000.
- Initial per-trade planned risk limit: ₹300.
- Initial daily loss limit: ₹600.
- Maximum two entries per day; one concurrent paper position.
- Minimum planned reward/risk target: 2:1.
- No overnight positions.
- No live broker orders or order-routing API.

These are initial configuration defaults, not evidence of suitability or profitability. Current exchange lot sizes and contract specifications must be checked before any option-level simulation.

## Next engineering gates
1. Secure read-only Upstox integration for spot/candles and authorised option-market data.
2. Licensed/authorised NSE option-chain data with source timestamps, freshness checks and rate-limit controls.
3. Option contract master, expiry/strike selection and realistic bid/ask paper fills.
4. Chronological backtest engine with transaction costs, expiry mapping, no look-ahead bias and walk-forward splits.
5. Durable database, scheduled data workers, health monitoring and audit logs.
6. At least 30 sessions of forward paper validation, with sufficient trade count and confidence intervals, before strategy promotion.

ATLAS is research software only. It is not investment advice and does not guarantee returns.
