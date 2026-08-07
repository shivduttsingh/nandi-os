from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from .engine import decide
from .lifecycle import TradeState, TradeStatus, advance_trade_state
from .models import Decision, DecisionAction, MarketContext, OptionChainSnapshot


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
    """Deterministic replay of the same V2 decision + confirmation + lifecycle flow.

    The replay never fetches network data. It consumes stored, strictly ordered
    NSE-style snapshots and the matching market contexts. BUY decisions must
    persist for the same number of fresh snapshots required by the live engine.
    """

    def __init__(
        self,
        trade_threshold: float = 75.0,
        prepare_threshold: float = 65.0,
        confirmation_snapshots: int = 3,
    ) -> None:
        self.trade_threshold = trade_threshold
        self.prepare_threshold = prepare_threshold
        self.confirmation_snapshots = max(1, int(confirmation_snapshots))

    def _confirm(
        self,
        raw: Decision,
        candidate_side: str,
        candidate_count: int,
    ) -> tuple[Decision, str, int]:
        if raw.action not in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}:
            return raw, "", 0
        if raw.side == candidate_side:
            candidate_count += 1
        else:
            candidate_side = raw.side
            candidate_count = 1
        if candidate_count >= self.confirmation_snapshots:
            return raw, candidate_side, candidate_count
        action = DecisionAction.PREPARE_CE if raw.side == "CE" else DecisionAction.PREPARE_PE
        confirmed = replace(
            raw,
            action=action,
            blockers=tuple(
                dict.fromkeys(
                    raw.blockers
                    + (f"Waiting for confirmation snapshot {candidate_count}/{self.confirmation_snapshots}",)
                )
            ),
        )
        return confirmed, candidate_side, candidate_count

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
        candidate_side = ""
        candidate_count = 0

        for snapshot, context in zip(snapshot_list, context_list):
            raw = decide(
                snapshot,
                context,
                trade_threshold=self.trade_threshold,
                prepare_threshold=self.prepare_threshold,
            )
            decision, candidate_side, candidate_count = self._confirm(raw, candidate_side, candidate_count)
            state = advance_trade_state(state, decision, snapshot.spot, context.observed_at)
            frames.append(ReplayFrame(snapshot, context, decision, state))

            was_active = previous_status in {
                TradeStatus.ACTIVE_CE,
                TradeStatus.ACTIVE_PE,
                TradeStatus.HOLD,
                TradeStatus.TRAIL,
                TradeStatus.PARTIAL,
            }
            if not was_active and state.status in {TradeStatus.ACTIVE_CE, TradeStatus.ACTIVE_PE}:
                entries += 1
                if state.side == "CE":
                    ce_entries += 1
                elif state.side == "PE":
                    pe_entries += 1
            if previous_status != TradeStatus.EXIT and state.status == TradeStatus.EXIT:
                exits += 1
            if decision.action == DecisionAction.NO_TRADE:
                no_trade += 1
            previous_status = state.status

        return ReplaySummary(tuple(frames), entries, exits, ce_entries, pe_entries, no_trade)
