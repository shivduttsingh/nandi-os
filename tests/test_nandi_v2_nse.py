from nandi_v2.nse import parse_option_chain_payload


def test_parse_option_chain_payload_selects_nearest_expiry() -> None:
    payload = {"records": {"timestamp": "06-Aug-2026 12:15:00", "underlyingValue": 24642.5, "expiryDates": ["13-Aug-2026", "20-Aug-2026"], "data": [
        {"strikePrice": 24600, "expiryDate": "13-Aug-2026", "CE": {"lastPrice": 75, "change": 10, "openInterest": 1000, "changeinOpenInterest": -100, "totalTradedVolume": 5000, "impliedVolatility": 12}, "PE": {"lastPrice": 40, "change": -5, "openInterest": 2000, "changeinOpenInterest": 200, "totalTradedVolume": 6000, "impliedVolatility": 13}},
        {"strikePrice": 24600, "expiryDate": "20-Aug-2026", "CE": {"lastPrice": 100}, "PE": {"lastPrice": 100}},
    ]}}
    snapshot = parse_option_chain_payload(payload)
    assert snapshot.expiry == "13-Aug-2026"
    assert snapshot.spot == 24642.5
    assert len(snapshot.rows) == 1
    assert snapshot.rows[0].ce.ltp == 75
    assert snapshot.rows[0].pe.change_oi == 200
