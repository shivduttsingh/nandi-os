from datetime import datetime, timedelta

from shiv_v2.a4_history import A4ResearchStore


def test_a4_store_keeps_its_own_trade_sample(tmp_path):
    store = A4ResearchStore(str(tmp_path / "shiv.sqlite"))
    opened = datetime(2026, 8, 24, 10, 0)
    signature = "tf=5|regime=TRENDING UP|side=CE|session=MORNING|vol=NORMAL|offset=0"
    store.record_trade(
        opened_at=opened,
        closed_at=opened + timedelta(minutes=15),
        signature=signature,
        interval_minutes=5,
        side="CE",
        strike=25000,
        strike_offset=0,
        entry_price=100,
        exit_price=109,
        exit_reason="TARGET",
        setup_quality=95,
        mtf_agreement=94,
        strike_score=93,
        persistence=4,
        regime="TRENDING UP",
        session_bucket="MORNING",
        volatility_band="NORMAL",
    )
    stats = store.similarity_stats(signature, 5, "CE")
    assert stats.sample_size == 1
    assert stats.wins == 1
    assert stats.win_rate == 100.0
    assert "UNVALIDATED" in stats.status
    samples = store.calibration_samples()
    assert len(samples) == 1
    assert samples[0].points == 9.0
