import os
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .strategies import MarketSnapshot, evaluate

app = FastAPI(title="ATLAS Research API", version="0.1.0")
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class SnapshotRequest(BaseModel):
    timestamp: str
    spot: float = Field(gt=0)
    opening_range_high: float
    opening_range_low: float
    vwap: float
    volume_ratio: float = Field(ge=0)
    close: float
    previous_close: float
    retest_confirmed: bool = False
    premium_confirmed: bool = False
    opposite_structure_break: bool = False

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ATLAS Research API", "time_utc": datetime.now(timezone.utc).isoformat()}

@app.get("/api/status")
def status():
    mode = os.getenv("ATLAS_MARKET_DATA_MODE", "disabled")
    configured = bool(os.getenv("UPSTOX_ACCESS_TOKEN"))
    return {
        "market_data": "configured" if configured else "not_configured",
        "mode": mode if configured else "disabled",
        "broker_orders": "disabled",
        "message": "No live market data is shown until a valid read-only data connection is configured."
    }

@app.get("/api/strategies")
def strategies():
    return [
        {"id": "A", "name": "Breakout continuation", "description": "Opening-range break with VWAP, volume, retest and premium confirmation."},
        {"id": "B", "name": "Failed-breakout reversal", "description": "Failed range break, close back inside, opposite structure break and retest."},
        {"id": "C", "name": "Adaptive selector", "description": "Selects a qualifying A/B setup only when there is no unresolved conflict."}
    ]

@app.post("/api/signal/{strategy}")
def signal(strategy: str, body: SnapshotRequest):
    key = strategy.upper()
    if key not in {"A", "B", "C"}:
        raise HTTPException(status_code=404, detail="Strategy must be A, B or C.")
    snapshot = MarketSnapshot(**body.model_dump())
    return evaluate(key, snapshot)

@app.get("/api/market/overview")
def market_overview():
    return {
        "underlying": "NIFTY",
        "market_data_status": "UNAVAILABLE",
        "spot": None,
        "as_of": None,
        "message": "Connect an authorized Upstox read-only token to load live prices. No placeholder prices are generated."
    }

@app.get("/api/research/requirements")
def research_requirements():
    return {
        "required_fields": ["timestamp", "instrument", "expiry", "strike", "option_type", "open", "high", "low", "close", "volume", "open_interest"],
        "required_for_realistic_options_backtest": ["contract-level historical candles", "bid/ask or defensible spread model", "fees", "slippage", "timestamped underlying candles"],
        "validation_gate": {"minimum_completed_trades": 200, "minimum_out_of_sample_profit_factor": 1.30, "positive_out_of_sample_expectancy": True},
        "warning": "Spot-only data is not sufficient to calculate option premium P&L."
    }
