"""
v1_inventory_skew.py — Inventory skew via the Avellaneda-Stoikov reservation price.

THE PROBLEM v0 LEFT US (see the v0 results): a symmetric quote has no mechanism to
pull inventory back to zero, so the position random-walks with order flow and we
carry unwanted directional risk.

THE FIX: stop quoting around the fair value `theo`. Quote around a *shifted* centre
— the **reservation price** — that leans against our current inventory.

    r = theo − q · γ · σ² · τ

  q  = current inventory (signed; + long)
  γ  = risk aversion (our tunable knob; bigger ⇒ skew harder, hold less inventory)
  σ² = variance RATE of the thing we're holding (here: the option's $-price vol)
  τ  = time remaining to expiry, in years

  bid = r − h ,   ask = r + h      (SAME half-spread h as v0 — only the centre moves)

INTUITION: if we're long (q>0), r < theo, so BOTH quotes drop. Our ask is now
cheaper (more likely lifted → we sell) and our bid is further from theo (less likely
hit → we buy less). Net effect: inventory is pulled back toward zero. Short is the
mirror. This is exactly the "lower both bid and ask skews when net long" behaviour
the project blueprint calls for.

WHY THIS IS THE *RESERVATION PRICE* AND NOT JUST A FUDGE FACTOR: r is the price at
which a risk-averse maker is *indifferent* to holding its current inventory vs not.
Quoting symmetrically around r (rather than the mid) is the Avellaneda-Stoikov
result for where to centre your quotes. v1 implements the CENTRE; v2 will add the
optimal WIDTH and generalise q to a Greek-weighted ("risk-mapped") inventory.

THE σ WE USE (a v1 approximation): the inventory risk of an option is, to first
order, the risk of its delta exposure: dV ≈ Δ·dS, and dS has $-vol σ_S·S. So the
option's annualised $-vol ≈ |Δ|·S·σ_S, and we use its square as the variance rate.
This deliberately ignores gamma/vega — generalising to full Greek risk is precisely
the v2 deliverable.

NICE EMERGENT FEATURE: as τ → 0 (near expiry) the skew vanishes — there's little
time left for an adverse move, so holding inventory is less risky. A-S gives us that
for free.
"""

from __future__ import annotations

from src.contracts import HedgeOrder, MarketState, PositionState, Quote, TheoState
from src.engine.base import QuotingEngine


class InventorySkewQuotingEngine(QuotingEngine):
    name = "v1_inventory_skew"

    def __init__(self, half_spread: float, quote_size: float, gamma: float):
        self.half_spread = half_spread
        self.quote_size = quote_size
        self.gamma = gamma

    def _reservation_price(self, tq, inventory: float) -> float:
        # option's annualised $-volatility via the delta approximation
        sigma_dollar = abs(tq.delta) * tq.underlying_spot * tq.iv
        variance_rate = sigma_dollar * sigma_dollar
        skew = inventory * self.gamma * variance_rate * tq.time_to_expiry
        return tq.fair_value - skew

    def update(
        self,
        market: MarketState,
        theo: TheoState,
        position: PositionState,
    ) -> tuple[list[Quote], list[HedgeOrder]]:
        quotes: list[Quote] = []
        for symbol, tq in theo.quotes.items():
            r = self._reservation_price(tq, position.inv(symbol))
            quotes.append(
                Quote(
                    symbol=symbol,
                    bid_price=r - self.half_spread,
                    bid_size=self.quote_size,
                    ask_price=r + self.half_spread,
                    ask_size=self.quote_size,
                )
            )
        # still no explicit hedging in v1 — the skew alone manages inventory.
        return quotes, []
