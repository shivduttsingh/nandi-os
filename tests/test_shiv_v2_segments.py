from datetime import datetime, timedelta

from shiv_v2.replay import CalibrationSample
from shiv_v2.segments import calibrate_by_regime_and_timeframe


def test_segmented_calibration_keeps_regime_and_timeframe_separate():
    start = datetime(2026, 1, 1, 9, 30)
    samples = []
    for index in range(25):
        samples.append(CalibrationSample(
            timestamp=start + timedelta(days=index),
            regime="TRENDING UP",
            interval_minutes=5,
            side="CE",
            setup_quality=80,
            mtf_agreement=80,
            strike_score=75,
            persistence=2,
            points=4 if index % 5 else -3,
        ))
    for index in range(25):
        samples.append(CalibrationSample(
            timestamp=start + timedelta(days=40 + index),
            regime="REVERSAL UP",
            interval_minutes=3,
            side="CE",
            setup_quality=84,
            mtf_agreement=82,
            strike_score=78,
            persistence=3,
            points=3 if index % 4 else -2,
        ))
    results = calibrate_by_regime_and_timeframe(samples)
    assert len(results) == 2
    assert {(row.regime, row.interval_minutes) for row in results} == {
        ("TRENDING UP", 5),
        ("REVERSAL UP", 3),
    }
    assert all(row.result.sample_size == 25 for row in results)
