from shiv_v2 import safety


def test_live_builder_excludes_mw_and_restores_detector(monkeypatch):
    original_detector = safety._strategy.detect_mw_pattern
    observed = []

    def fake_builder(**_kwargs):
        observed.append(safety._strategy.detect_mw_pattern(tuple()))
        return "sentinel"

    monkeypatch.setattr(safety, "_raw_build_v2_decision", fake_builder)
    result = safety._build_without_mw()

    assert result == "sentinel"
    assert observed[0].label == "EXCLUDED"
    assert observed[0].side == "NONE"
    assert observed[0].confidence == 0.0
    assert safety._strategy.detect_mw_pattern is original_detector


def test_excluded_mw_has_no_direction_or_confirmation():
    assessment = safety._excluded_mw_pattern(tuple())
    assert assessment.side == "NONE"
    assert not assessment.confirmed
    assert "contributes no points" in assessment.reason
