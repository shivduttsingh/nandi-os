from .strategies import MarketSnapshot, evaluate

def sample(**overrides):
    data = dict(timestamp="2026-11-21T10:00:00+05:30", spot=25000, opening_range_high=25010, opening_range_low=24980, vwap=25000, volume_ratio=1.8, close=25020, previous_close=25005, retest_confirmed=True, premium_confirmed=True, opposite_structure_break=False)
    data.update(overrides)
    return MarketSnapshot(**data)

def test_a_breakout_ce():
    assert evaluate("A", sample())["signal"] == "CE"

def test_b_failed_breakout_pe():
    s = sample(close=25005, previous_close=25020, opposite_structure_break=True)
    assert evaluate("B", s)["signal"] == "PE"

def test_c_does_not_force_signal():
    assert evaluate("C", sample(volume_ratio=0.7))["signal"] == "NO TRADE"
