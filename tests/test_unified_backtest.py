from datetime import datetime, timedelta

from nandi_oi.models import OptionLeg, OptionSnapshot
from nandi_oi.unified_backtest import OI_STRATEGY_NAMES, UnifiedBacktester, run_one_oi_strategy


def snapshot(at: datetime, spot: float, premium: float) -> OptionSnapshot:
    legs = []
    for strike in range(24750, 25300, 50):
        legs.extend((
            OptionLeg(strike, "CE", 1000, -100, premium, 5, 1000, premium - 1, premium + 1,
                      premium, premium * 1.35, premium * 0.95),
            OptionLeg(strike, "PE", 1000, 100, premium, -5, 1000, premium - 1, premium + 1,
                      premium, premium * 1.05, premium * 0.95),
        ))
    return OptionSnapshot(at, spot, 10, spot - 1, spot - 20, tuple(legs), "2026-07-23")


def test_unified_backtest_runs_oi_and_every_saved_rsi_variant():
    start = datetime(2026, 7, 20, 9, 15)
    weekly = [snapshot(start + timedelta(minutes=5 * index), 25000 + index * 10, 100 + index * 10)
              for index in range(20)]
    monthly = [snapshot(start + timedelta(minutes=5 * index), 25000 - index * 10, 100 + index * 2)
               for index in range(20)]
    result = UnifiedBacktester(rsi_timeframes=(1, 5)).run(
        weekly, monthly,
        {
            "RSI 5 — 30/70": {"length": 5, "lower": 30, "upper": 70},
            "RSI 6 — 25/75": {"length": 6, "lower": 25, "upper": 75},
        },
        one_minute_closes={start + timedelta(minutes=index): 100 + index for index in range(30)},
    )
    assert [(run.strategy, run.contract) for run in result.runs] == [
        ("OI flow", "Nearest weekly evidence check"),
        ("NIFTY price structure", "Nearest weekly evidence check"),
        ("OI-wall movement", "Nearest weekly evidence check"),
        ("Option premium and liquidity", "Nearest weekly evidence check"),
        ("Three-snapshot OI persistence", "Nearest weekly evidence check"),
        ("Nandi OI V1", "Nearest weekly"),
        ("RSI 5 — 30/70", "Nearest weekly"),
        ("RSI 5 — 30/70", "Nearest monthly"),
        ("RSI 6 — 25/75", "Nearest weekly"),
        ("RSI 6 — 25/75", "Nearest monthly"),
    ]
    assert len(result.rsi_touch_rows()) == 4
    assert {row["Strategy"] for row in result.summary_rows()} == {
        "OI flow", "NIFTY price structure", "OI-wall movement",
        "Option premium and liquidity", "Three-snapshot OI persistence",
        "Nandi OI V1", "RSI 5 — 30/70", "RSI 6 — 25/75",
    }


def test_unified_backtest_rejects_empty_strategy_selection():
    start = datetime(2026, 7, 20, 9, 15)
    records = [snapshot(start + timedelta(minutes=5 * index), 25000 + index, 100) for index in range(5)]
    try:
        UnifiedBacktester().run(records, records, {})
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:
        raise AssertionError("Expected an empty strategy selection to be rejected")


def test_unified_backtest_keeps_daily_chart_evidence_and_strategy_background():
    first_day = datetime(2026, 7, 20, 9, 15)
    second_day = datetime(2026, 7, 21, 9, 15)
    weekly = [
        snapshot(first_day + timedelta(minutes=5 * index), 25000 + index * 5, 100 + index)
        for index in range(16)
    ] + [
        snapshot(second_day + timedelta(minutes=5 * index), 25100 - index * 5, 116 + index)
        for index in range(16)
    ]
    monthly = list(weekly)
    result = UnifiedBacktester().run(
        weekly, monthly, {"RSI 5 — 30/70": {"length": 5, "lower": 30, "upper": 70}},
    )

    assert result.available_dates() == (first_day.date(), second_day.date())
    rows = result.daily_strategy_rows(first_day.date())
    assert len(rows) == len(result.runs)
    assert all(row["Historical snapshots"] == 16 for row in rows)
    assert {row["Strategy"] for row in rows} >= {"OI flow", "Nandi OI V1", "RSI 5 — 30/70"}

    chart_rows = result.daily_chart_rows(first_day.date(), rsi_length=5, rsi_lower=30, rsi_upper=70)
    assert len(chart_rows) == 16
    assert {
        "NIFTY spot", "Nearby CE ΔOI", "CE OI wall", "Nandi bullish score", "RSI(5)",
        "EMA 9", "Bollinger upper", "MACD line", "ROC 10 %",
    } <= chart_rows[0].keys()
    assert result.strategy_run("Nandi OI V1 — Nearest weekly").background.entry_rule.startswith("Score must be at least 80")
    assert result.strategy_run("RSI 5 — 30/70 — Nearest weekly").background.rsi_length == 5

    selected_timestamp = chart_rows[-1]["Timestamp"]
    option_chain = result.option_chain_rows(selected_timestamp)
    assert len(option_chain) == 22
    assert {"Open interest", "Change in OI", "OI/premium activity", "Spread %"} <= option_chain[0].keys()
    calculation = result.calculation_rows(chart_rows[-1])
    assert [row["Calculation"] for row in calculation] == [
        "OI flow", "NIFTY price structure", "OI-wall movement", "ATM option premium",
        "Three-snapshot persistence", "Liquidity quality",
    ]
    assert result.approval_rows(chart_rows[-1])[-1]["Approval gate"] == "Final paper action"
    provenance = result.data_provenance_rows(selected_timestamp, result.runs[0])
    assert any(row["Item"] == "Option data source" for row in provenance)


def test_every_oi_strategy_can_run_in_its_own_auditable_result():
    start = datetime(2026, 7, 20, 9, 15)
    records = [snapshot(start + timedelta(minutes=5 * index), 25000 + index * 10, 100 + index)
               for index in range(16)]
    for strategy in OI_STRATEGY_NAMES:
        result = run_one_oi_strategy(strategy, records)
        assert len(result.runs) == 1
        run = result.runs[0]
        assert run.strategy == strategy
        chart_rows = result.daily_chart_rows(start.date(), run=run)
        assert chart_rows and "Strategy action" in chart_rows[-1]
        assert result.strategy_calculation_rows(run, chart_rows[-1])
        assert result.strategy_approval_rows(run, chart_rows[-1])
