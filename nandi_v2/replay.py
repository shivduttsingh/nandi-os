from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .engine import decide
from .lifecycle import TradeState, TradeStatus, advance_trade_state
from .models import Decision, MarketContext, OptionChainSnapshot


@dataclass(frozen=True)
class ReplayFrame:
    snapshot: OptionChainSnapshot
    context: MarketContext
    decision: Decision
    trade_state: TradeState


@dataclass(frozen=True)
class ReplaySummary:
    frames: tuple[ReplayFrame, ...]
    entries: int
    exits: int
    ce_entries: int
    pe_entries: int
    no_trade_frames: int


class NandiReplay:
    """Deterministic replay harness for the unified V2 engine.

    It consumes already-time-ordered NSE-style snapshots and matching market
    contexts. The class does not fetch network data and therefore can be used by
    tests, historical adapters, and future replay UI code.
    """

    def __init__(self, trade_threshold: float = 75.0, prepare_threshold: float = 65.0) -> None:
        self.trade_threshold = trade_threshold
        self.prepare_threshold = prepare_threshold

    def run(
        self,
        snapshots: Sequence[OptionChainSnapshot] | Iterable[OptionChainSnapshot],
        contexts: Sequence[MarketContext] | Iterable[MarketContext],
    ) -> ReplaySummary:
        snapshot_list = list(snapshots)
        context_list = list(contexts)
        if len(snapshot_list) != len(context_list):
            raise ValueError("snapshots and contexts must have the same length")
        if any(b.timestamp <= a.timestamp for a, b in zip(snapshot_list, snapshot_list[1:])):
            raise ValueError("snapshots must be strictly increasing by timestamp")

        state = TradeState()
        frames: list[ReplayFrame] = []
        entries = exits = ce_entries = pe_entries = no_trade = 0
        previous_status = state.status

        for snapshot, context in zip(snapshot_list, context_list):
            decision = decide(
                snapshot,
                context,
                trade_threshold=self.trade_threshold,
                prepare_threshold=self.prepare_threshold,
            )
            state = advance_trade_state(state, decision, snapshot.spot, context.observed_at)
            frames.append(ReplayFrame(snapshot, context, decision, state))

            if previous_status not in {TradeStatus.ACTIVE_CE, TradeStatus.ACTIVE_PE, TradeStatus.HOLD, TradeStatus.TRAIL, TradeStatus.PARTIAL} and state.status in {TradeStatus.ACTIVE_CE, TradeStatus.ACTIVE_PE}:
                entries += 1
                if state.side == "CE":
                    ce_entries += 1
                elif state.side == "PE":
                    pe_entries += 1
            if previous_status != TradeStatus.EXIT and state.status == TradeStatus.EXIT:
                exits += 1
            if decision.action.value == "NO TRADE":
                no_trade += 1
            previous_status = state.status

        return ReplaySummary(tuple(frames), entries, exits, ce_entries, pe_entries, no_trade)
