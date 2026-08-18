from datetime import date, datetime

from nandi_oi.upstox import UpstoxAPIError, UpstoxOptionChainClient


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
    one = client.parse_chain(first, datetime(2026, 7, 20, 9, 30))
    two = client.parse_chain(second, datetime(2026, 7, 20, 9, 30, 30))
    assert one.legs[0].change_oi == -100
    assert one.legs[0].change_ltp == 5
    assert two.legs[0].change_oi == -50
    assert two.legs[0].change_ltp == 5
    assert two.spot_change == 10
    assert two.recent_high == 25010


def test_adapter_defaults_capture_time_to_ist_clock():
    client = UpstoxOptionChainClient(access_token="not-used")
    snapshot = client.parse_chain([row(25000, 25010, 900, 105, 1100, 95)])
    assert snapshot.timestamp.tzinfo is None


def test_sample_token_is_rejected_before_any_network_request():
    client = UpstoxOptionChainClient(access_token="PASTE_YOUR_ANALYTICS_TOKEN_HERE")
    try:
        client.fetch_raw_chain()
    except UpstoxAPIError as exc:
        assert "sample placeholder" in str(exc)
    else:
        raise AssertionError("Expected a sample token to be rejected")


def test_intraday_candles_use_v3_interval_and_sort_oldest_first():
    class CandleClient(UpstoxOptionChainClient):
        def _get_v3(self, path):
            assert path == "/historical-candle/intraday/NSE_INDEX%7CNifty%2050/minutes/15"
            return {"status": "success", "data": {"candles": [
                ["2026-08-12T09:30:00+05:30", 25020, 25040, 25010, 25035, 0, 0],
                ["2026-08-12T09:15:00+05:30", 25000, 25025, 24990, 25020, 0, 0],
            ]}}

    candles = CandleClient(access_token="test").fetch_intraday_candles(15)

    assert [item.timestamp for item in candles] == [
        datetime(2026, 8, 12, 9, 15), datetime(2026, 8, 12, 9, 30),
    ]
    assert candles[-1].open == 25020
    assert candles[-1].close == 25035


def test_intraday_candles_ignore_bad_rows_and_reject_bad_interval():
    class CandleClient(UpstoxOptionChainClient):
        def _get_v3(self, _path):
            return {"status": "success", "data": {"candles": [
                ["bad-time", 1, 2, 1, 2],
                ["2026-08-12T09:15:00+05:30", 25000, 25025, 24990, 25020, 0, 0],
            ]}}

    client = CandleClient(access_token="test")
    assert len(client.fetch_intraday_candles(5)) == 1
    try:
        client.fetch_intraday_candles(0)
    except ValueError as exc:
        assert "between 1 and 300" in str(exc)
    else:
        raise AssertionError("Expected an invalid candle interval to be rejected")


def test_atm_instruments_are_resolved_from_exact_expiry_and_nearest_strike():
    class ChainClient(UpstoxOptionChainClient):
        def _get(self, path, params):
            assert path == "/option/chain"
            assert params["expiry_date"] == "2026-08-20"
            data = [
                row(25000, 25062, 1000, 100, 1000, 90),
                row(25050, 25062, 1000, 80, 1000, 110),
                row(25100, 25062, 1000, 60, 1000, 130),
            ]
            for item in data:
                item["expiry"] = "2026-08-20"
            return {"status": "success", "data": data}

    pair = ChainClient(access_token="test").resolve_atm_option_instruments(
        "20-Aug-2026", 25062,
    )

    assert pair.strike == 25050
    assert pair.expiry == "2026-08-20"
    assert pair.ce_instrument_key == "CE-25050"
    assert pair.pe_instrument_key == "PE-25050"


def test_atm_plus_minus_two_instruments_are_resolved_in_strike_order():
    class ChainClient(UpstoxOptionChainClient):
        def _get(self, path, params):
            assert path == "/option/chain"
            assert params["expiry_date"] == "2026-08-20"
            data = [
                row(strike, 25062, 1000, 100, 1000, 90)
                for strike in (25150, 24950, 25050, 25100, 25000)
            ]
            for item in data:
                item["expiry"] = "2026-08-20"
            return {"status": "success", "data": data}

    pairs = ChainClient(access_token="test").resolve_option_window_instruments(
        "20-Aug-2026",
        25062,
        wings=2,
    )

    assert [(pair.offset, pair.strike) for pair in pairs] == [
        (-2, 24950),
        (-1, 25000),
        (0, 25050),
        (1, 25100),
        (2, 25150),
    ]
    assert pairs[0].ce_instrument_key == "CE-24950"
    assert pairs[-1].pe_instrument_key == "PE-25150"


def test_option_window_requires_both_wings_and_both_contract_sides():
    class ShortChainClient(UpstoxOptionChainClient):
        def fetch_raw_chain(self, _expiry=None):
            return [
                row(strike, 25000, 1000, 100, 1000, 90)
                for strike in (24950, 25000, 25050)
            ]

    try:
        ShortChainClient(access_token="test").resolve_option_window_instruments(
            "current_week",
            25000,
            wings=2,
        )
    except UpstoxAPIError as exc:
        assert "complete ATM ±2" in str(exc)
    else:
        raise AssertionError("Expected an incomplete strike window to be rejected")


def test_option_instrument_candles_use_the_exact_contract_key():
    class OptionCandleClient(UpstoxOptionChainClient):
        def _get_v3(self, path):
            assert path == "/historical-candle/intraday/NSE_FO%7C12345/minutes/3"
            return {"status": "success", "data": {"candles": [
                ["2026-08-12T09:15:00+05:30", 100, 105, 98, 103, 500, 1000],
            ]}}

    candles = OptionCandleClient(access_token="test").fetch_instrument_intraday_candles(
        "NSE_FO|12345", 3,
    )

    assert candles[0].close == 103
    assert candles[0].open_interest == 1000


def test_historical_candles_use_v3_date_path_and_sort_oldest_first():
    class CandleClient(UpstoxOptionChainClient):
        def _get_v3(self, path):
            assert path == (
                "/historical-candle/NSE_INDEX%7CNifty%2050/minutes/15/"
                "2026-08-11/2026-08-02"
            )
            return {"status": "success", "data": {"candles": [
                ["2026-08-11T09:30:00+05:30", 25020, 25040, 25010, 25035, 0, 0],
                ["2026-08-03T09:15:00+05:30", 24900, 24925, 24890, 24920, 0, 0],
            ]}}

    candles = CandleClient(access_token="test").fetch_historical_candles(
        date(2026, 8, 2), date(2026, 8, 11), 15,
    )

    assert [item.timestamp for item in candles] == [
        datetime(2026, 8, 3, 9, 15), datetime(2026, 8, 11, 9, 30),
    ]


def test_historical_candles_reject_reversed_or_oversized_ranges():
    client = UpstoxOptionChainClient(access_token="test")
    for from_date, to_date in (
        (date(2026, 8, 12), date(2026, 8, 11)),
        (date(2026, 7, 1), date(2026, 8, 11)),
    ):
        try:
            client.fetch_historical_candles(from_date, to_date, 15)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected an invalid historical range to be rejected")
