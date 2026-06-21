"""
fills.py — Fill model (stands in for the Infra/OMS section's exchange matching + our fills).

This is the single most important modelling choice in the whole sandbox, because
it decides *when our resting quotes get hit*. We deliberately use the same form
that the Avellaneda–Stoikov framework assumes, so the intuition we build now
carries directly into v2.

THE MODEL — intensity decays exponentially with distance from fair value:

    λ(δ) = A · exp(−k · δ)

  δ  = how far our quote sits AWAY from theo (a worse price for the taker), in $.
  A  = base arrival intensity: how often someone would trade against a quote
       sitting exactly at theo (δ = 0).
  k  = how sharply flow dries up as we widen. Big k ⇒ takers are price-sensitive
       and a small step out kills our fill rate.

Per discrete step we convert intensity to a hit probability  p = 1 − exp(−λ·dt),
then draw a uniform. Quoting tighter (small δ) ⇒ more fills but less edge per
fill; quoting wider ⇒ fewer fills but more edge. THAT trade-off is the entire
market-making problem, and A-S is just the closed-form optimum of it.

Note the asymmetry we are modelling: a BID below theo is hit by sellers; an ASK
above theo is lifted by buyers. δ is always "how unattractive is my price to the
counterparty", so it is (theo − bid) on the bid side and (ask − theo) on the ask
side. Quoting THROUGH theo (bid above theo) gives δ < 0 and a >A intensity —
i.e. you are paying up and will get run over, which is exactly right.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.contracts import Quote, Side


@dataclass
class Fill:
    symbol: str
    side: Side          # the side OF OUR QUOTE that traded (BUY = our bid was hit)
    price: float
    size: float
    theo_at_fill: float


class FillModel:
    def __init__(self, A: float, k: float, dt: float, seed: int = 1):
        self.A = A
        self.k = k
        self.dt = dt
        self.rng = np.random.default_rng(seed)

    def _hit_prob(self, delta: float) -> float:
        intensity = self.A * np.exp(-self.k * delta)
        return 1.0 - np.exp(-intensity * self.dt)

    def simulate_fills(self, quote: Quote, theo: float,
                       future_theo: float | None = None) -> list[Fill]:
        """Return the fills generated against one two-sided quote this step.

        `future_theo` is ignored by the uninformed model; it exists so the
        Simulator can call every fill model with one signature. The informed
        subclass uses it.
        """
        fills: list[Fill] = []

        # --- bid side: distance below theo, hit by sellers ------------------ #
        delta_bid = theo - quote.bid_price
        if quote.bid_size > 0 and self.rng.random() < self._hit_prob(delta_bid):
            fills.append(Fill(quote.symbol, Side.BUY, quote.bid_price,
                              quote.bid_size, theo))

        # --- ask side: distance above theo, lifted by buyers ---------------- #
        delta_ask = quote.ask_price - theo
        if quote.ask_size > 0 and self.rng.random() < self._hit_prob(delta_ask):
            fills.append(Fill(quote.symbol, Side.SELL, quote.ask_price,
                              quote.ask_size, theo))

        return fills


class InformedFillModel(FillModel):
    """Fill model WITH adverse selection: a fraction of flow is *informed*.

    An informed taker knows where theo is heading (we feed it the theo `H` steps
    ahead from the precomputed path) and only trades when our quote is mispriced
    relative to that future:

      * lifts our ASK when  future_theo > ask_price  (we sell cheap into a rise),
      * hits our BID  when  bid_price > future_theo   (we buy rich into a fall).

    So **every informed fill is adverse by construction** — its markout is
    negative. The informed arrival intensity scales with the taker's edge
    `I·max(edge,0)`: the more mispriced we are, the more informed flow we attract.

    Crucially, this gives v4 something to *do*: if we WIDEN our ask, `edge` shrinks
    and informed buying falls off. Widening quotes mechanically reduces adverse
    selection — that's the lever the toxicity detector pulls.

    Implementation note (paired-RNG hygiene): we draw a fixed FOUR uniforms per
    symbol per step (2 uninformed + 2 informed) REGARDLESS of prices/sizes, so the
    random stream stays aligned across engines no matter how differently they
    quote. This is what keeps the v2/v3/v4 comparison a true paired test.
    """

    def __init__(self, A: float, k: float, dt: float, seed: int = 1,
                 informed_intensity: float = 0.0):
        super().__init__(A, k, dt, seed)
        self.I = informed_intensity

    def _informed_prob(self, edge: float) -> float:
        if edge <= 0.0:
            return 0.0
        return 1.0 - np.exp(-self.I * edge * self.dt)

    def simulate_fills(self, quote: Quote, theo: float,
                       future_theo: float | None = None) -> list[Fill]:
        fills: list[Fill] = []

        # draw all four uniforms up front -> constant RNG consumption per step
        u_bid = self.rng.random()
        u_ask = self.rng.random()
        u_inf_bid = self.rng.random()
        u_inf_ask = self.rng.random()

        # --- uninformed (noise) flow, as in the base model ------------------ #
        if quote.bid_size > 0 and u_bid < self._hit_prob(theo - quote.bid_price):
            fills.append(Fill(quote.symbol, Side.BUY, quote.bid_price,
                              quote.bid_size, theo))
        if quote.ask_size > 0 and u_ask < self._hit_prob(quote.ask_price - theo):
            fills.append(Fill(quote.symbol, Side.SELL, quote.ask_price,
                              quote.ask_size, theo))

        # --- informed (toxic) flow, conditioned on the future theo ---------- #
        if future_theo is not None and self.I > 0.0:
            # informed buyers lift our ask when we're selling too cheap
            if quote.ask_size > 0 and u_inf_ask < self._informed_prob(future_theo - quote.ask_price):
                fills.append(Fill(quote.symbol, Side.SELL, quote.ask_price,
                                  quote.ask_size, theo))
            # informed sellers hit our bid when we're buying too rich
            if quote.bid_size > 0 and u_inf_bid < self._informed_prob(quote.bid_price - future_theo):
                fills.append(Fill(quote.symbol, Side.BUY, quote.bid_price,
                                  quote.bid_size, theo))

        return fills
