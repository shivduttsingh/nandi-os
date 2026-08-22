"""Live composition for Shiv V2.

The UI was built against the adaptive strategy module. Before importing it, this
composition layer replaces the adaptive builder with the guarded wrapper so a
V2 policy can never bypass a V1 hard NO-TRADE or multi-timeframe conflict.
"""

from . import strategy as _strategy
from .safety import build_safe_v2_decision

_strategy.build_v2_decision = build_safe_v2_decision

from .ui import render_shiv_terminal  # noqa: E402

__all__ = ["render_shiv_terminal"]
