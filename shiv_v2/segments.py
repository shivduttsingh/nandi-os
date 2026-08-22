from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .replay import CalibrationResult, CalibrationSample, calibrate_thresholds


@dataclass(frozen=True)
class SegmentCalibration:
    regime: str
    interval_minutes: int
    result: CalibrationResult


def calibrate_by_regime_and_timeframe(
    samples: Iterable[CalibrationSample],
    *,
    minimum_total_sample: int = 20,
    minimum_selected_trades: int = 8,
) -> tuple[SegmentCalibration, ...]:
    """Calibrate each regime/timeframe independently once it has enough outcomes."""
    groups: dict[tuple[str, int], list[CalibrationSample]] = {}
    for sample in samples:
        groups.setdefault((sample.regime, sample.interval_minutes), []).append(sample)
    results = []
    for (regime, interval), group in sorted(groups.items()):
        results.append(SegmentCalibration(
            regime=regime,
            interval_minutes=interval,
            result=calibrate_thresholds(
                group,
                minimum_total_sample=minimum_total_sample,
                minimum_selected_trades=minimum_selected_trades,
            ),
        ))
    return tuple(results)
