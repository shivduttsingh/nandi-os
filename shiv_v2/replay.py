from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import product
from math import sqrt
from statistics import mean
from typing import Iterable


@dataclass(frozen=True)
class CalibrationSample:
    timestamp: datetime
    regime: str
    interval_minutes: int
    side: str
    setup_quality: float
    mtf_agreement: float
    strike_score: float
    persistence: int
    points: float
    session_bucket: str = "UNKNOWN"
    volatility_band: str = "UNKNOWN"
    pattern: str = "NONE"


@dataclass(frozen=True)
class ThresholdSet:
    minimum_quality: float
    minimum_mtf: float
    minimum_strike_score: float
    minimum_persistence: int


@dataclass(frozen=True)
class CalibrationResult:
    status: str
    sample_size: int
    selected_trades: int
    thresholds: ThresholdSet | None
    observed_win_rate: float | None
    average_points: float | None
    net_points: float | None
    objective: float | None
    reason: str


@dataclass(frozen=True)
class WalkForwardFold:
    fold: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_size: int
    test_size: int
    thresholds: ThresholdSet
    selected_test_trades: int
    wins: int
    observed_win_rate: float | None
    average_points: float | None
    net_points: float


@dataclass(frozen=True)
class WalkForwardResult:
    status: str
    sample_size: int
    folds: tuple[WalkForwardFold, ...]
    selected_trades: int
    wins: int
    observed_win_rate: float | None
    average_points: float | None
    net_points: float | None
    maximum_losing_streak: int
    reason: str


def _passes(sample: CalibrationSample, thresholds: ThresholdSet) -> bool:
    return (
        sample.setup_quality >= thresholds.minimum_quality
        and sample.mtf_agreement >= thresholds.minimum_mtf
        and sample.strike_score >= thresholds.minimum_strike_score
        and sample.persistence >= thresholds.minimum_persistence
    )


def _maximum_losing_streak(samples: Iterable[CalibrationSample]) -> int:
    longest = current = 0
    for sample in samples:
        if sample.points <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _candidate_thresholds() -> tuple[ThresholdSet, ...]:
    return tuple(
        ThresholdSet(float(quality), float(mtf), float(strike), int(persistence))
        for quality, mtf, strike, persistence in product(
            (60, 65, 70, 75, 80, 85),
            (60, 65, 70, 75, 80, 85),
            (45, 55, 65, 75),
            (1, 2, 3),
        )
    )


def calibrate_thresholds(
    samples: Iterable[CalibrationSample],
    *,
    minimum_total_sample: int = 20,
    minimum_selected_trades: int = 8,
) -> CalibrationResult:
    """Calibrate gates only from completed outcomes; no probability is invented."""
    ordered = tuple(sorted(samples, key=lambda item: item.timestamp))
    if len(ordered) < minimum_total_sample:
        return CalibrationResult(
            status="UNVALIDATED",
            sample_size=len(ordered),
            selected_trades=0,
            thresholds=None,
            observed_win_rate=None,
            average_points=None,
            net_points=None,
            objective=None,
            reason=f"Needs at least {minimum_total_sample} completed V2 paper/backtest outcomes before calibration.",
        )

    best: tuple[float, ThresholdSet, tuple[CalibrationSample, ...]] | None = None
    for thresholds in _candidate_thresholds():
        selected = tuple(sample for sample in ordered if _passes(sample, thresholds))
        if len(selected) < minimum_selected_trades:
            continue
        average_points = mean(sample.points for sample in selected)
        win_rate = sum(sample.points > 0 for sample in selected) / len(selected)
        loss_streak = _maximum_losing_streak(selected)
        # Reward positive expectancy and sufficient sample size while penalizing
        # long losing streaks. This objective is a ranking metric, not a win rate.
        objective = average_points * sqrt(len(selected)) + win_rate * 2.0 - loss_streak * 0.35
        if best is None or objective > best[0]:
            best = (objective, thresholds, selected)

    if best is None:
        return CalibrationResult(
            status="UNVALIDATED",
            sample_size=len(ordered),
            selected_trades=0,
            thresholds=None,
            observed_win_rate=None,
            average_points=None,
            net_points=None,
            objective=None,
            reason="No threshold set retained enough completed trades for a defensible calibration sample.",
        )

    objective, thresholds, selected = best
    wins = sum(sample.points > 0 for sample in selected)
    net = sum(sample.points for sample in selected)
    average_points = net / len(selected)
    return CalibrationResult(
        status="CALIBRATED — IN-SAMPLE ONLY",
        sample_size=len(ordered),
        selected_trades=len(selected),
        thresholds=thresholds,
        observed_win_rate=round(wins / len(selected) * 100.0, 1),
        average_points=round(average_points, 2),
        net_points=round(net, 2),
        objective=round(objective, 3),
        reason="Thresholds were selected from completed outcomes. Walk-forward validation is still required before treating them as robust.",
    )


def walk_forward_replay(
    samples: Iterable[CalibrationSample],
    *,
    train_size: int = 40,
    test_size: int = 10,
    minimum_selected_trades: int = 6,
) -> WalkForwardResult:
    """Chronological train-then-test replay; no test outcome is used to choose its thresholds."""
    ordered = tuple(sorted(samples, key=lambda item: item.timestamp))
    minimum_required = train_size + test_size
    if len(ordered) < minimum_required:
        return WalkForwardResult(
            status="UNVALIDATED",
            sample_size=len(ordered),
            folds=tuple(),
            selected_trades=0,
            wins=0,
            observed_win_rate=None,
            average_points=None,
            net_points=None,
            maximum_losing_streak=0,
            reason=f"Needs at least {minimum_required} chronological completed outcomes for the first {train_size}/{test_size} walk-forward fold.",
        )

    folds: list[WalkForwardFold] = []
    selected_all: list[CalibrationSample] = []
    fold_number = 1
    cursor = train_size
    while cursor + test_size <= len(ordered):
        train = ordered[cursor - train_size:cursor]
        test = ordered[cursor:cursor + test_size]
        calibration = calibrate_thresholds(
            train,
            minimum_total_sample=min(train_size, 20),
            minimum_selected_trades=minimum_selected_trades,
        )
        if calibration.thresholds is None:
            cursor += test_size
            fold_number += 1
            continue
        selected = tuple(sample for sample in test if _passes(sample, calibration.thresholds))
        selected_all.extend(selected)
        wins = sum(sample.points > 0 for sample in selected)
        net = sum(sample.points for sample in selected)
        folds.append(WalkForwardFold(
            fold=fold_number,
            train_start=train[0].timestamp,
            train_end=train[-1].timestamp,
            test_start=test[0].timestamp,
            test_end=test[-1].timestamp,
            train_size=len(train),
            test_size=len(test),
            thresholds=calibration.thresholds,
            selected_test_trades=len(selected),
            wins=wins,
            observed_win_rate=round(wins / len(selected) * 100.0, 1) if selected else None,
            average_points=round(net / len(selected), 2) if selected else None,
            net_points=round(net, 2),
        ))
        cursor += test_size
        fold_number += 1

    if not folds:
        return WalkForwardResult(
            status="UNVALIDATED",
            sample_size=len(ordered),
            folds=tuple(),
            selected_trades=0,
            wins=0,
            observed_win_rate=None,
            average_points=None,
            net_points=None,
            maximum_losing_streak=0,
            reason="Training windows did not contain enough retained trades to calibrate a walk-forward test.",
        )

    wins = sum(sample.points > 0 for sample in selected_all)
    net = sum(sample.points for sample in selected_all)
    selected_count = len(selected_all)
    status = "WALK-FORWARD OBSERVED" if selected_count >= minimum_selected_trades else "UNVALIDATED"
    return WalkForwardResult(
        status=status,
        sample_size=len(ordered),
        folds=tuple(folds),
        selected_trades=selected_count,
        wins=wins,
        observed_win_rate=round(wins / selected_count * 100.0, 1) if selected_count else None,
        average_points=round(net / selected_count, 2) if selected_count else None,
        net_points=round(net, 2) if selected_count else None,
        maximum_losing_streak=_maximum_losing_streak(selected_all),
        reason=(
            "Each test fold used thresholds chosen only from the immediately preceding training window. Results are historical observations, not future probability."
            if selected_count
            else "The walk-forward folds ran, but none of the test outcomes passed the trained thresholds."
        ),
    )
