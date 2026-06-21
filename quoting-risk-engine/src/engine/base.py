"""
base.py — The QuotingEngine interface.

Every version (v0 symmetric, v1 inventory skew, v2 Avellaneda-Stoikov, ...) is a
subclass that implements ONE method:

    update(market, theo, position) -> (quotes, hedge_orders)

Keeping the signature fixed across versions is what lets the harness run a clean,
seed-matched A/B between any two engines: swap the engine, change nothing else.
This is the same discipline as a paired experiment — hold everything constant,
toggle one thing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.contracts import HedgeOrder, MarketState, PositionState, Quote, TheoState


class QuotingEngine(ABC):
    name: str = "base"

    @abstractmethod
    def update(
        self,
        market: MarketState,
        theo: TheoState,
        position: PositionState,
    ) -> tuple[list[Quote], list[HedgeOrder]]:
        """Produce the desired resting quotes and any hedge orders for this tick."""
        ...
