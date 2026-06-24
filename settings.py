from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
KNOWLEDGE_DIR = DATA_DIR / "knowledge_base"
MEMORY_DB = DATA_DIR / "memory.db"
JOURNAL_DB = DATA_DIR / "paper_journal.db"
LOG_FILE = LOGS_DIR / "nandi.log"

APP_NAME = "Nandi OS"
APP_VERSION = "2.1 Interactive Command Center"
APP_MODE = "Private Financial Intelligence Mode"
OWNER_NAME = "Shivdutt"

CAPITAL = 25000
RISK_PER_RESEARCH = 0.02

DEFAULT_SYMBOLS = {
    "NIFTY 50": "^NSEI",
    "BANK NIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "INDIA VIX": "^INDIAVIX",
    "USD/INR": "INR=X",
}

INDIAN_MARKET_SCOPE = [
    "NSE equities",
    "NIFTY and BANK NIFTY index research",
    "Indian index options scenario study",
    "IPO research checklist",
    "Indian commodities watchlist",
    "USD/INR and INR currency pair research",
]

ADVISOR_PROFILE = (
    "Nandi is Shivdutt's private AI financial intelligence advisor. "
    "It is designed to combine technical, fundamental, macro, risk, sentiment, "
    "and journal-based research into disciplined decision support for Indian markets."
)

RESEARCH_DISCLAIMER = (
    "Nandi Finance is a private financial intelligence and decision-support system for Shivdutt. "
    "It is not a public advisory service, broker execution system, or guaranteed-profit engine. "
    "Nandi can guide, score, warn, and explain setups, but final execution decisions remain with the user."
)

for folder in [DATA_DIR, LOGS_DIR, KNOWLEDGE_DIR]:
    folder.mkdir(parents=True, exist_ok=True)
