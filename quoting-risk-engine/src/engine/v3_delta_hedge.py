"""
v3_delta_hedge.py — Dynamic Delta Hedging on top of Avellaneda-Stoikov.

WHY v2 ISN'T ENOUGH ON ITS OWN: the reservation-price skew is a *passive* tool. It
biases our quotes to lean against inventory, but it can only shed risk as fast as
counterparties choose to trade with us. If flow is one-sided or the underlying
moves hard, net delta can run away faster than the skew can lean it back. The skew
manages the SLOW drift; it can't flatten a sudden build-up.

THE FIX — an *active* threshold hedge (the blueprint's DDH):

    if |Δ_net| > θ:  trade the underlying to bring Δ_net back to 0

  θ (theta) is a NO-TRADE BAND. Inside it we do nothing and let the quotes work.
  Cross it and we cross the spread in the liquid underlying to flatten. This splits
  the labour cleanly:
     * quoting skew (v2)  -> cheap, continuous, handles normal drift
     * delta hedge (v3)   -> costs the spread, discrete, caps tail risk

THE TENSION WE'RE TUNING (and the whole reason θ exists):
  * Small θ  -> tight delta control, but you hedge constantly and BLEED the
               underlying spread (transaction costs dominate).
  * Large θ  -> almost never hedge (≈ v2), cheap, but you carry big delta tails.
  The right θ minimises {hedging cost + cost of carrying residual delta risk}.
  This is exactly the classic no-trade-band hedging problem (Whalley–Wilmott et al.),
  and sweeping θ traces the cost/risk frontier — the v3 deliverable.

WHY HEDGE THE UNDERLYING AND NOT QUOTE HARDER: hedging in the deep, liquid
underlying is immediate and certain; leaning quotes further only *hopes* for a fill
and gives away edge to everyone. Past the band, certainty is worth the spread.

We hedge to ZERO on breach (simplest threshold rule). Hedging only to the band edge
is a lower-churn variant worth trying later.
"""

from __future__ import annotations

from src.contracts import HedgeOrder, MarketState, PositionState, Quote, Side, TheoState
from src.engine.v2_avellaneda_stoikov import AvellanedaStoikovEngine


class DeltaHedgingEngine(AvellanedaStoikovEngine):
    name = "v3_delta_hedge"

    def __init__(
        self,
        quote_size: float,
        gamma: float,
        k: float,
        theta: float,
        min_half_spread: float = 0.0,
    ):
        super().__init__(quote_size=quote_size, gamma=gamma, k=k,
                         min_half_spread=min_half_spread)
        self.theta = theta  # no-trade band half-width, in underlying delta units

    def update(
        self,
        market: MarketState,
        theo: TheoState,
        position: PositionState,
    ) -> tuple[list[Quote], list[HedgeOrder]]:
        # reuse v2's A-S quoting + risk-mapping verbatim
        quotes, _ = super().update(market, theo, position)

        net_delta = self._net_delta(theo, position)
        hedges: list[HedgeOrder] = []
        if abs(net_delta) > self.theta:
            # flatten to zero: trade away exactly net_delta units of underlying
            if net_delta > 0:           # net LONG delta -> SELL underlying
                hedges.append(HedgeOrder(side=Side.SELL, qty=net_delta))
            else:                       # net SHORT delta -> BUY underlying
                hedges.append(HedgeOrder(side=Side.BUY, qty=-net_delta))

        return quotes, hedges
