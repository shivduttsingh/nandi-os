import ast
from pathlib import Path


def function_calls(name: str) -> set[str]:
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    return {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_upstox_nifty_chart_does_not_call_full_nse_market_refresh():
    calls = function_calls("nifty_upstox_chart_fragment")

    assert "refresh_upstox_nifty_candles" in calls
    assert "refresh_market_data" not in calls


def test_paper_algo_refreshes_read_only_chart_inputs_independently():
    calls = function_calls("paper_algo_fragment")

    assert "refresh_upstox_nifty_candles" in calls
    assert "refresh_atm_option_candles" in calls
    assert "refresh_strike_window_candles" in calls
    assert "refresh_market_data" not in calls
