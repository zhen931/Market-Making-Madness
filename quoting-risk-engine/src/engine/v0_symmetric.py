"""
v0_symmetric.py — Baseline symmetric market maker.

The simplest possible quoting rule and our experimental CONTROL:

    bid = theo − h
    ask = theo + h

with a fixed half-spread `h` and fixed size. It ignores inventory entirely, so
it will happily accumulate a huge one-sided position if flow is lopsided or the
underlying trends — and watching it do exactly that is the whole point. v0 gives
us the PnL/inventory baseline that every later, smarter version must beat on a
seed-matched run.

WHY START HERE rather than jumping to Avellaneda-Stoikov:
  * It validates the plumbing (harness, fills, PnL accounting) with logic simple
    enough that any bug is obviously in the infra, not the strategy.
  * It makes the *problem* visceral: run it and you'll see inventory random-walk
    away and PnL get dominated by mark-to-market swings, not spread capture.
    Every feature from v1 on is a response to a pathology you can see in v0.
"""

from __future__ import annotations

from src.contracts import HedgeOrder, MarketState, PositionState, Quote, TheoState
from src.engine.base import QuotingEngine


class SymmetricQuotingEngine(QuotingEngine):
    name = "v0_symmetric"

    def __init__(self, half_spread: float, quote_size: float):
        self.half_spread = half_spread
        self.quote_size = quote_size

    def update(
        self,
        market: MarketState,
        theo: TheoState,
        position: PositionState,
    ) -> tuple[list[Quote], list[HedgeOrder]]:
        quotes: list[Quote] = []
        for symbol, tq in theo.quotes.items():
            quotes.append(
                Quote(
                    symbol=symbol,
                    bid_price=tq.fair_value - self.half_spread,
                    bid_size=self.quote_size,
                    ask_price=tq.fair_value + self.half_spread,
                    ask_size=self.quote_size,
                )
            )
        # v0 does no hedging — net delta is left to run, on purpose.
        return quotes, []
