from datetime import datetime

from nandi_oi.upstox import UpstoxOptionChainClient


def row(strike, spot, ce_oi, ce_ltp, pe_oi, pe_ltp):
    return {
        "expiry": "2026-07-23",
        "strike_price": strike,
        "underlying_spot_price": spot,
        "call_options": {
            "instrument_key": f"CE-{strike}",
            "market_data": {"oi": ce_oi, "prev_oi": 1000, "ltp": ce_ltp, "close_price": 100, "volume": 500, "bid_price": ce_ltp - 1, "ask_price": ce_ltp + 1},
        },
        "put_options": {
            "instrument_key": f"PE-{strike}",
            "market_data": {"oi": pe_oi, "prev_oi": 1000, "ltp": pe_ltp, "close_price": 100, "volume": 500, "bid_price": pe_ltp - 1, "ask_price": pe_ltp + 1},
        },
    }


def test_adapter_uses_actual_snapshot_deltas_after_first_call():
    client = UpstoxOptionChainClient(access_token="not-used")
    first = [row(25000, 25010, 900, 105, 1100, 95)]
    second = [row(25000, 25020, 850, 110, 1150, 90)]
    second[0]["call_options"]["market_data"]["volume"] = 650
    one = client.parse_chain(first, datetime(2026, 7, 20, 9, 30))
    two = client.parse_chain(second, datetime(2026, 7, 20, 9, 30, 30))
    assert one.legs[0].change_oi == -100
    assert one.legs[0].change_ltp == 5
    assert two.legs[0].change_oi == -50
    assert two.legs[0].change_ltp == 5
    assert two.legs[0].volume == 150
    assert two.spot_change == 10
    assert two.recent_high == 25010
