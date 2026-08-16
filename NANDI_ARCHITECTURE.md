# Nandi decision-system architecture

## Product objective

Nandi is being built as an explainable NIFTY trading-research system for Shiv and, after validation and production hardening, a larger user base. It is not a collection of unrelated strategy pages. Every live entry must pass one shared decision path:

1. **Fundamental Desk** — global risk, GIFT NIFTY, currency/commodities, institutional flows, macro/policy, NIFTY heavyweight earnings and event risk.
2. **Technical Lab** — 25 transparent indicators grouped into trend, momentum, volatility, structure and participation families.
3. **OI & Execution** — the existing NSE ATM ±5 option-chain engine, premium confirmation, location, liquidity, reward-risk, freshness and session gates.
4. **Confluence Gate** — approves only the side proposed by the OI engine when the required pillars agree. It never creates or reverses a trade by itself.
5. **Risk Lifecycle** — confirmation, frozen option-premium and NIFTY levels, immediate invalidation/targets, minimum/maximum hold, reversal cooldown, persistence and results.

## Live decision rule

The confluence score is a setup-quality score:

- OI and execution quality: 45%
- technical family consensus: 35%
- fundamental context: 20%

This weighting does not mean that an 80 score is an 80% win probability. Probability claims require calibration against untouched live and walk-forward results.

New entries are blocked when:

- the OI engine has no approved side;
- technical coverage is insufficient;
- technical families are sideways or oppose the proposed side;
- fundamental coverage is missing/stale;
- fundamentals materially oppose the proposed side;
- NSE data/session/freshness or existing risk gates fail.

Fresh neutral fundamentals may allow an otherwise aligned technical + OI setup, because neutral evidence is not the same as missing evidence.

Both the OI proposal and final weighted confluence score must meet the configured BUY threshold. For an approved side, Nandi selects the nearest-expiry ATM contract and builds a frozen option-premium plan from the current NSE quote: ask price when the order book is valid, otherwise LTP, a 5% premium stop, 1.5R first target and 2.5R second target. Missing premiums or a bid/ask spread above 3% block BUY. These are transparent research rules, not a promised win rate or guaranteed executable fill. Recorded entry/exit premiums feed a separate premium win-rate result; missing values are not estimated.

## Technical design

The Technical Lab exposes exactly 25 readings:

- **Trend:** close/SMA 5, close/SMA 20, SMA 5/20, close/EMA 9, EMA 9/21, MACD line, MACD histogram, Supertrend, DMI/ADX and Aroon.
- **Momentum:** RSI, Stochastic %K/%D, Williams %R, CCI, ROC and absolute momentum.
- **Volatility:** Bollinger position, Keltner channel, Donchian breakout and ATR expansion.
- **Structure:** Heikin-Ashi direction and pivot position.
- **Participation:** session VWAP and OBV slope.

Indicators vote within their family first. The five family outputs then form the technical consensus. This prevents a large group of correlated trend indicators from overpowering other types of evidence. Warm-up and unavailable-volume readings abstain rather than becoming neutral or guessed votes.

Upstox V3 supplies raw historical and current-day candles; Nandi calculates the indicators locally so every value is reproducible. A rolling ten-calendar-day history window prevents long-lookback indicators from remaining in warm-up after the opening bell. The focused Nandi Top 10 view spans trend, momentum, volatility and structure, while the decision gate still uses all 25 through family-balanced voting. The Top 10 is a product selection, not an exchange-published popularity ranking.

The Command Center separately embeds TradingView's hosted `NSE:NIFTY` Advanced Chart for operator analysis and shows the current nearest-expiry ATM CE and PE Upstox premium candles side by side. A paper-validation confirmation strategy compares only matching completed timestamps across all three charts. It remains outside the final confluence weight until recorded results justify promotion, avoiding double-counting premium evidence already present in the OI/execution pillar.

## Fundamental design

The current desk accepts an authenticated, sourced snapshot and stores every update in SQLite. Each factor contains:

- direction;
- impact;
- evidence confidence;
- observed time and maximum age;
- source;
- audit note.

The live gate treats unknown or stale factors as missing. Automated feeds must later implement this same contract; they must not bypass freshness, source or audit fields.

## Strategy horizons

Scalping, intraday, weekly-expiry, monthly-expiry and swing models must be validated separately. They may share evidence services, but they must not share one claimed win rate or mix trades into a fictional combined result.

## Accuracy programme

The requested 70–80% target is a research acceptance target, not a guarantee. Promotion to a public live model requires:

1. chronological replay with no future-data leakage;
2. train/validation/untouched test windows;
3. walk-forward evaluation across trend, range, volatile and event regimes;
4. minimum trade-count and confidence-interval requirements;
5. slippage, spread, rejected-data and stale-feed modelling;
6. paper-live shadow operation;
7. score calibration and drift monitoring;
8. an automatic kill switch when data quality or performance degrades.

## Delivery phases

### Phase 1 — implemented in this branch

- separate Fundamental Desk and Technical Lab;
- 25-indicator family consensus;
- persisted sourced fundamental snapshots;
- one confluence gate around the existing OI engine;
- missing/conflicting pillars default to NO TRADE;
- Command Center shows all three pillars and the unified result.

### Phase 2 — data and replay parity

- authorised automated global/macro/flow/news providers;
- persist completed technical candles and indicator snapshots;
- replay fundamentals, technicals and OI using the exact live confluence path;
- separate horizon-specific strategy configurations.

### Phase 3 — validation and paper pilot

- walk-forward and untouched-period reports;
- per-regime/per-time-of-day results;
- option-premium fills, spread and slippage;
- shadow alerts with no automatic broker orders;
- calibrated thresholds based on evidence, not a desired percentage.

### Phase 4 — multi-user production

- private repository and production secrets management;
- licensed market data and provider rate-limit controls;
- API/backend separated from Streamlit presentation;
- durable database, queues, monitoring, audit logs and disaster recovery;
- real user accounts, roles, subscriptions and per-user alert controls;
- security, data-license and professional regulatory/compliance review before public launch.

## Non-negotiable safeguards

- no guessed trade when a source is missing;
- no score described as guaranteed probability;
- no indicator promoted into the live gate without replay and paper validation;
- no broker-order endpoint in the research application;
- no secret committed to GitHub;
- no live model change without tests and a versioned release record.
