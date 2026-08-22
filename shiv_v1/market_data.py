from __future__ import annotations

from urllib.parse import quote, urlencode

from nandi_v2.models import OptionChainSnapshot
from nandi_v2.nse import NSEPublicClient


class ShivNSEPublicClient(NSEPublicClient):
    """Shiv-specific NSE reader that tolerates an unchanged exchange timestamp.

    NSE often keeps the final option-chain timestamp unchanged after the session.
    Stable Nandi deliberately rejects that condition for live trading. Shiv is an
    R&D terminal, so it may display the last exchange snapshot while preserving
    zero/rolling deltas and letting the separate session gate block new paper
    entries outside the regular NSE session.
    """

    def fetch_option_chain(self, symbol: str = "NIFTY", expiry: str | None = None) -> OptionChainSnapshot:
        symbol = symbol.strip().upper() or "NIFTY"
        selected_expiry = expiry or self._nearest_expiry(symbol)
        query = urlencode({"type": "Indices", "symbol": symbol, "expiry": selected_expiry})
        payload = self._json(self.OPTION_CHAIN_V3_API.format(query=query))
        raw_snapshot = self._parse_option_chain(payload, selected_expiry)

        previous = self._previous_chain
        rolling = self.rolling_snapshot(raw_snapshot, previous)
        if previous is not None and raw_snapshot.timestamp <= previous.timestamp:
            return OptionChainSnapshot(
                timestamp=raw_snapshot.timestamp,
                expiry=raw_snapshot.expiry,
                spot=raw_snapshot.spot,
                rows=rolling.rows,
                source=f"{raw_snapshot.source} · last exchange snapshot",
                raw_timestamp=raw_snapshot.raw_timestamp,
            )

        self._previous_chain = raw_snapshot
        return rolling
