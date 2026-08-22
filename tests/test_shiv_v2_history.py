from datetime import datetime, timedelta

from shiv_v2.history import ShivV2ResearchStore


def test_v2_store_records_trade_and_calibration_sample(tmp_path):
    store = ShivV2ResearchStore(str(tmp_path / "shiv_v2.sqlite"))
    opened = datetime(2026, 8, 24, 10, 0)
    store.record_trade(
        opened_at=opened,
        closed_at=opened + timedelta(minutes=15),
        signature="tf=5|regime=TRENDING UP|session=MORNING",
        interval_minutes=5,
        side="CE",
        strike=25000,
        strike_offset=0,
        entry_price=100,
        exit_price=108,
        exit_reason="TEST",
        setup_quality=82,
        mtf_agreement=84,
        strike_score=76,
        persistence=3,
        regime="TRENDING UP",
        session_bucket="MORNING",
        volatility_band="NORMAL",
        pattern="W CONFIRMED",
    )
    records = store.recent_trades()
    assert len(records) == 1
    assert records[0].points == 8
    assert records[0].hold_minutes == 15
    calibration = store.calibration_samples()
    assert len(calibration) == 1
    assert calibration[0].strike_score == 76


def test_v2_observation_is_deduplicated_by_snapshot_key(tmp_path):
    store = ShivV2ResearchStore(str(tmp_path / "shiv_v2.sqlite"))
    kwargs = dict(
        observed_at=datetime(2026, 8, 24, 10, 0),
        snapshot_key="2026-08-24T10:00|tf=5",
        signature="test",
        interval_minutes=5,
        side="CE",
        regime="TRENDING UP",
        status="CONFIRM CE",
        setup_quality=80,
        required_quality=70,
        mtf_agreement=82,
        required_mtf=70,
        strike=25000,
        strike_offset=0,
        strike_score=75,
        persistence=2,
        session_bucket="MORNING",
        volatility_band="NORMAL",
        pattern="NONE",
        entry_status="WAIT — ENTRY TRIGGER",
    )
    store.record_observation(**kwargs)
    store.record_observation(**kwargs)
    assert store.observation_count() == 1
