"""
v4_toxic_flow.py — Toxic-flow / adverse-selection protection (VPIN-style).

THE PROBLEM EVERYTHING SO FAR IGNORED: v0–v3 assume our counterparties are noise
traders. In reality some flow is *informed* — filled by someone with short-term
alpha, so theo moves against us right after the trade. That's **adverse selection**,
and it's how market makers bleed. It shows up as a NEGATIVE markout curve (see
[[05 - Measuring a Market Maker]]); none of our previous metrics-on-the-benign-world
revealed it because there was no informed flow to reveal.

DETECTION — a VPIN-style toxicity gauge. VPIN (Volume-Synchronised Probability of
Informed Trading, Easley–López de Prado–O'Hara) says: informed flow is *one-sided*
(they all trade the same direction over a horizon), so a sustained **order-flow
imbalance** signals toxicity. We compute a rolling

    toxicity = |ΣBuyVol − ΣSellVol| / (ΣBuyVol + ΣSellVol)   ∈ [0, 1]

over the last `window` steps. Balanced noise flow ⇒ toxicity ≈ 0; a one-sided
informed run ⇒ toxicity → 1.

  HOW WE SEE THE FLOW: the engine only gets `PositionState`, but our own inventory
  changes ARE the trade tape. If our inventory fell, a taker BOUGHT from us (lifted
  our ask); if it rose, a taker SOLD to us (hit our bid). We diff inventory between
  ticks to reconstruct signed taker volume. (One-tick lag: we react to *realised*
  toxicity, which is the honest, causal thing to do.)

REACTION — widen, then pull:
  * toxicity > `vpin_threshold`  ⇒ widen the A-S half-spread by
        (1 + widen_beta·(toxicity − threshold)).
    Because informed arrivals scale with our mispricing (see InformedFillModel),
    a wider quote directly starves the toxic flow while still capturing noise.
  * toxicity > `pull_threshold`  ⇒ pull quotes entirely (size → 0): when flow is
    overwhelmingly one-sided, the right move is to stop quoting and not feed it.

WHY THIS IS THE RIGHT TOOL AND NOT JUST MORE SKEW: inventory skew (v2) leans
against a position you ALREADY hold. Toxicity protection acts on the *character of
the flow itself*, BEFORE the position is built — it stops the bleed at the source.
Skew, hedge, and toxicity-defence are three different jobs: lean / flatten / refuse.

LIMITATION (honest): we widen symmetrically, though toxic flow is one-sided —
asymmetric widening (only the attacked side) is a natural refinement. And VPIN is a
lagging, noisy estimator; too low a threshold and we widen on noise, giving up edge.
That threshold trade-off is the v4 experiment.

v4 subclasses v3, so it stacks the full programme: A-S quoting (v2) + delta hedge
(v3) + toxicity defence (v4).
"""

from __future__ import annotations

from collections import deque

from src.contracts import MarketState, PositionState, TheoState
from src.engine.v3_delta_hedge import DeltaHedgingEngine


class ToxicFlowEngine(DeltaHedgingEngine):
    name = "v4_toxic_protect"

    def __init__(
        self,
        quote_size: float,
        gamma: float,
        k: float,
        theta: float,
        vpin_window: int = 40,
        vpin_threshold: float = 0.5,
        widen_beta: float = 8.0,
        pull_threshold: float = 0.7,
        min_volume: float = 8.0,
        min_half_spread: float = 0.0,
    ):
        super().__init__(quote_size=quote_size, gamma=gamma, k=k, theta=theta,
                         min_half_spread=min_half_spread)
        self.vpin_window = vpin_window
        self.vpin_threshold = vpin_threshold
        self.widen_beta = widen_beta
        self.pull_threshold = pull_threshold
        # Don't trust the imbalance ratio until this much volume has accrued in
        # the window — the essence of *volume*-synchronised VPIN. Without it, a
        # window holding 1-2 noise fills reads toxicity≈1.0 (false positive).
        self.min_volume = min_volume

        self._last_inv: dict[str, float] = {}
        self._buy_hist: deque[float] = deque(maxlen=vpin_window)
        self._sell_hist: deque[float] = deque(maxlen=vpin_window)
        # SIGNED toxicity in [-1, 1]:
        #   > 0  takers are net BUYING (lifting our asks) -> the ASK side is toxic
        #   < 0  takers are net SELLING (hitting our bids) -> the BID side is toxic
        self._signed_tox: float = 0.0

    # --- toxicity estimation ----------------------------------------------- #
    def _update_toxicity(self, position: PositionState) -> None:
        buy_vol = 0.0   # taker BOUGHT from us (our inventory fell) -> hit our ask
        sell_vol = 0.0  # taker SOLD to us  (our inventory rose) -> hit our bid
        for sym, qty in position.inventory.items():
            d = qty - self._last_inv.get(sym, 0.0)
            if d > 0:
                sell_vol += d
            elif d < 0:
                buy_vol += -d

        self._buy_hist.append(buy_vol)
        self._sell_hist.append(sell_vol)
        B, S = sum(self._buy_hist), sum(self._sell_hist)
        total = B + S
        # min-volume guard: too little flow to judge -> assume benign
        self._signed_tox = (B - S) / total if total >= self.min_volume else 0.0
        self._last_inv = dict(position.inventory)

    def _side_toxicity(self, side: str) -> float:
        """How toxic THIS side is, in [0,1]. Only the attacked side is positive."""
        if side == "ask":
            return max(self._signed_tox, 0.0)    # toxic when takers are buying
        return max(-self._signed_tox, 0.0)        # bid toxic when takers are selling

    # --- quoting hooks (consumed by v2's update), now ASYMMETRIC ----------- #
    def _spread_scale(self, symbol: str, side: str) -> float:
        excess = max(self._side_toxicity(side) - self.vpin_threshold, 0.0)
        return 1.0 + self.widen_beta * excess

    def _size_scale(self, symbol: str, side: str) -> float:
        # pull ONLY the attacked side; keep the other live so we can shed risk
        return 0.0 if self._side_toxicity(side) > self.pull_threshold else 1.0

    # --- main entry point -------------------------------------------------- #
    def update(self, market: MarketState, theo: TheoState, position: PositionState):
        # refresh toxicity from realised flow BEFORE we (re)quote
        self._update_toxicity(position)
        # then defer to the full v3 stack (A-S quoting via hooks + delta hedge)
        return super().update(market, theo, position)
