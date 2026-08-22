from datetime import datetime, timedelta

from shiv_v1.history import ShivResearchStore


def test_similarity_stats_stay_unvalidated_until_real_sample(tmp_path):
    store = ShivResearchStore(str(tmp_path / "shiv.sqlite"))
    now = datetime(2026, 8, 22, 10, 0)
    for index, points in enumerate((8.0, -3.5, 10.0)):
        store.record_trade(
            opened_at=now + timedelta(minutes=index * 10),
            closed_at=now + timedelta(minutes=index * 10 + 5),
            signature="same-setup",
            interval_minutes=5,
            side="CE",
            strike=25000,
            entry_price=100,
            exit_price=100 + points,
            exit_reason="test",
            setup_quality=80,
        )
    stats = store.similarity_stats("same-setup", 5, "CE", validation_sample=20)
    assert stats.sample_size == 3
    assert stats.wins == 2
    assert stats.losses == 1
    assert stats.win_rate == 66.7
    assert stats.status == "UNVALIDATED (3/20)"


def test_similarity_stats_do_not_mix_other_setup_signatures(tmp_path):
    store = ShivResearchStore(str(tmp_path / "shiv.sqlite"))
    now = datetime(2026, 8, 22, 10, 0)
    store.record_trade(
        opened_at=now,
        closed_at=now + timedelta(minutes=5),
        signature="A",
        interval_minutes=5,
        side="CE",
        strike=25000,
        entry_price=100,
        exit_price=108,
        exit_reason="target",
        setup_quality=82,
    )
    store.record_trade(
        opened_at=now,
        closed_at=now + timedelta(minutes=5),
        signature="B",
        interval_minutes=5,
        side="CE",
        strike=25000,
        entry_price=100,
        exit_price=96.5,
        exit_reason="stop",
        setup_quality=70,
    )
    stats = store.similarity_stats("A", 5, "CE", validation_sample=1)
    assert stats.sample_size == 1
    assert stats.win_rate == 100.0
