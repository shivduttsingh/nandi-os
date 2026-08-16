from nandi_v2.nse import NSEPublicClient, parse_option_chain_payload


def _payload(expiry: str = "13-Aug-2026") -> dict:
    return {"records": {"timestamp": "06-Aug-2026 12:15:00", "underlyingValue": 24642.5, "expiryDates": [expiry, "20-Aug-2026"], "data": [
        {"strikePrice": 24600, "expiryDate": expiry, "CE": {"lastPrice": 75, "change": 10, "openInterest": 1000, "changeinOpenInterest": -100, "totalTradedVolume": 5000, "impliedVolatility": 12, "bidprice": 74.5, "askPrice": 75.5}, "PE": {"lastPrice": 40, "change": -5, "openInterest": 2000, "changeinOpenInterest": 200, "totalTradedVolume": 6000, "impliedVolatility": 13}},
        {"strikePrice": 24600, "expiryDate": "20-Aug-2026", "CE": {"lastPrice": 100}, "PE": {"lastPrice": 100}},
    ]}}


def test_parse_option_chain_payload_selects_nearest_expiry() -> None:
    snapshot = parse_option_chain_payload(_payload())
    assert snapshot.expiry == "13-Aug-2026"
    assert snapshot.spot == 24642.5
    assert len(snapshot.rows) == 1
    assert snapshot.rows[0].ce.ltp == 75
    assert snapshot.rows[0].ce.bid == 74.5
    assert snapshot.rows[0].ce.ask == 75.5
    assert snapshot.rows[0].pe.change_oi == 200


def test_v3_client_resolves_expiry_and_fetches_current_endpoint() -> None:
    class FakeClient(NSEPublicClient):
        def __init__(self) -> None:
            super().__init__()
            self.paths: list[str] = []

        def _json(self, path: str) -> dict:
            self.paths.append(path)
            if "option-chain-contract-info" in path:
                return {"expiryDates": ["13-Aug-2026", "20-Aug-2026"]}
            if "option-chain-v3" in path:
                return _payload()
            raise AssertionError(path)

    client = FakeClient()
    snapshot = client.fetch_option_chain("NIFTY")
    assert snapshot.expiry == "13-Aug-2026"
    assert "option-chain-contract-info?symbol=NIFTY" in client.paths[0]
    assert "option-chain-v3?" in client.paths[1]
    assert "type=Indices" in client.paths[1]
    assert "symbol=NIFTY" in client.paths[1]
    assert "expiry=13-Aug-2026" in client.paths[1]


def test_first_v3_snapshot_is_neutral_rolling_baseline() -> None:
    class FakeClient(NSEPublicClient):
        def _json(self, path: str) -> dict:
            if "option-chain-contract-info" in path:
                return {"expiryDates": ["13-Aug-2026"]}
            return _payload()

    snapshot = FakeClient().fetch_option_chain("NIFTY")
    assert snapshot.rows[0].ce.change == 0
    assert snapshot.rows[0].ce.change_oi == 0
    assert snapshot.rows[0].pe.change == 0
    assert snapshot.rows[0].pe.change_oi == 0
