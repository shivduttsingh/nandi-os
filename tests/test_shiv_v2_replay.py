from datetime import datetime, timedelta

from shiv_v2.replay import CalibrationSample, calibrate_thresholds, walk_forward_replay


def samples(count=70):
    start = datetime(2026, 1, 1, 9, 30)
    output = []
    for index in range(count):
        good = index % 4 != 0
        output.append(CalibrationSample(
            timestamp=start + timedelta(days=index),
            regime="TRENDING UP" if index % 2 == 0 else "TRENDING DOWN",
            interval_minutes=5,
            side="CE" if index % 2 == 0 else "PE",
            setup_quality=82 if good else 62,
            mtf_agreement=82 if good else 60,
            strike_score=78 if good else 48,
            persistence=3 if good else 1,
            points=5.0 if good else -3.5,
            session_bucket="MORNING",
            volatility_band="NORMAL",
            pattern="NONE",
        ))
    return tuple(output)


def test_calibration_refuses_small_sample():
    result = calibrate_thresholds(samples(10))
    assert result.status == "UNVALIDATED"
    assert result.thresholds is None
    assert result.observed_win_rate is None


def test_calibration_uses_completed_outcomes_when_sample_is_large_enough():
    result = calibrate_thresholds(samples(40))
    assert result.thresholds is not None
    assert result.selected_trades >= 8
    assert result.observed_win_rate is not None
    assert result.average_points is not None
    assert "IN-SAMPLE" in result.status


def test_walk_forward_runs_chronological_train_then_test_folds():
    result = walk_forward_replay(samples(70), train_size=40, test_size=10)
    assert result.folds
    assert len(result.folds) == 3
    for fold in result.folds:
        assert fold.train_end < fold.test_start
    assert result.selected_trades > 0
    assert result.observed_win_rate is not None


def test_walk_forward_refuses_insufficient_history():
    result = walk_forward_replay(samples(30), train_size=40, test_size=10)
    assert result.status == "UNVALIDATED"
    assert not result.folds
