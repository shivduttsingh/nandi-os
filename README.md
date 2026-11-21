# ATLAS — Options Research & Paper Trading

ATLAS is a browser-based, paper-only research platform for NIFTY and SENSEX index options. It does not place broker orders.

## Architecture
- `frontend/`: React + TypeScript + Vite web application.
- `backend/`: FastAPI service, strategy engines, CSV research and paper-ledger API.
- PostgreSQL: persistent research runs and paper-trade records.
- Docker Compose: local full-stack development.

## Strategy definitions
- **A — Breakout continuation:** opening-range break, close outside range, VWAP/volume and option-premium confirmation, then retest/continuation.
- **B — Failed-breakout reversal:** failed opening-range break, close back inside range, opposite structure break and retest.
- **C — Adaptive selector:** chooses A or B only when its complete rules qualify; otherwise returns NO TRADE. It does not force a signal.

## Research integrity
- No fabricated live prices, option-chain values, Greeks, fills or historical results.
- Missing/stale market data must be visibly marked unavailable.
- Backtests require timestamped underlying and contract-level option candles. A spot-only CSV cannot be presented as options P&L.
- Fees, slippage and spread assumptions must be included in reported results.
- Walk-forward/out-of-sample results are reported separately from in-sample results.
- Minimum validation gate before a strategy is called validated: 200 completed trades, positive out-of-sample expectancy and profit factor above 1.30. These are gates, not promised outcomes.

## Run locally
```bash
cp .env.example .env
docker compose up --build
```
Frontend: http://localhost:5173  
API docs: http://localhost:8000/docs

## Data setup
Configure Upstox credentials as environment variables on the server. Use Upstox and permitted NSE/public reference data in read-only mode. Never put API secrets in the frontend or commit them to Git.

## Deployment note
The previous Streamlit deployment is not a compatible host for this React/FastAPI application. Keep it intact until a compatible host is configured and the new application passes acceptance tests.