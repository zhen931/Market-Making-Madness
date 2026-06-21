"""
v2_avellaneda_stoikov.py — Full Avellaneda-Stoikov, adapted for options.

This is the headline deliverable. It adds the two things v1 was missing:

  (A) THE OPTIMAL SPREAD WIDTH. v0/v1 used a fixed half-spread `h` pulled from
      thin air. A-S tells us how wide to quote:

          δ_total = γ·σ²·τ  +  (2/γ)·ln(1 + γ/k)
                    └ inventory ┘   └ competition / flow elasticity ┘

      * 1st term: widen when the asset is risky (big σ) or there's lots of time
        left (big τ) — you'll be stuck with whatever you're filled on for longer.
      * 2nd term: governed by `k`, the order-arrival decay from the fill model
        λ=A·e^(−kδ). As γ→0 it tends to 2/k — the "monopolist" spread you'd quote
        with no inventory aversion, set purely by how fast flow dies as you widen.
      Quotes are placed symmetrically around the reservation price, so the CENTRE
      still skews with inventory (v1) and now the WIDTH is principled too.

  (B) RISK-MAPPED ("GREEK-WEIGHTED") INVENTORY — the options-specific adaptation.
      In equities A-S, `q` is "number of shares" and σ is the share's vol. For an
      options book that's wrong: holding 3× the 100-strike and 2× the 105-strike
      is NOT "5 contracts" of one risk. The first-order P&L risk of the whole book
      is its exposure to the UNDERLYING:

          dΠ ≈ Δ_net · dS ,   Δ_net = Σ_i q_i·Δ_i  (+ any underlying hedge)

      So we map every position onto a single common axis — net portfolio delta in
      underlying units — and run A-S on THAT, with σ = the underlying's $-vol.

      Concretely, quoting option i:
        * a fill changes Δ_net by Δ_i, so the reservation SKEW for option i is
              skew_i = Δ_i · γ · σ_$² · τ · Δ_net
          (proportional to how much that option moves portfolio risk AND to the
           sign/size of the risk we already carry).
        * the spread's inventory term uses option i's OWN $-variance (Δ_i·σ_$)²,
          so riskier (higher-delta) options are quoted wider.

      THE PAYOFF (and the interview soundbite): this makes the book quote as a
      PORTFOLIO. If we're net-long delta because we got filled on the 100 call,
      v2 will skew the 105 call to sell too — even with zero inventory in it —
      because selling it also reduces portfolio delta. A contract-count MM can't
      see that. Net-delta risk is controlled jointly across the whole chain.

  Sanity check / continuity: with a single option this reduces EXACTLY to v1's
  reservation price (since Δ_net = q·Δ and σ_$ = S·iv ⇒ skew = q·γ·(Δ·S·iv)²·τ),
  and with γ→0 it becomes a symmetric MM with half-spread 1/k. Good limiting cases.

  KNOWN LIMITATION (kept honest): we map to DELTA only. A delta-neutral but long-
  gamma/long-vega book looks "flat" to v2 even though it carries real vol risk.
  Extending the risk map to vega is the natural follow-on.

  NOTE ON k: here we pass the TRUE arrival decay `k` (the engine "knows" the
  market). Live, `k` is calibrated from order flow; mis-estimating it mis-sizes
  the spread, which is itself a worthwhile future experiment.
"""

from __future__ import annotations

import math

from src.contracts import HedgeOrder, MarketState, PositionState, Quote, TheoState
from src.engine.base import QuotingEngine


class AvellanedaStoikovEngine(QuotingEngine):
    name = "v2_avell_stoikov"

    def __init__(
        self,
        quote_size: float,
        gamma: float,
        k: float,
        min_half_spread: float = 0.0,
    ):
        self.quote_size = quote_size
        self.gamma = gamma
        self.k = k
        self.min_half_spread = min_half_spread

    def _net_delta(self, theo: TheoState, position: PositionState) -> float:
        """Risk-mapped inventory: aggregate portfolio delta in underlying units."""
        q = position.underlying_position  # underlying carries delta 1
        for sym, tq in theo.quotes.items():
            q += position.inv(sym) * tq.delta
        return q

    def _competition_term(self) -> float:
        # (2/γ)·ln(1 + γ/k) — independent of inventory; the half is taken below.
        return (2.0 / self.gamma) * math.log1p(self.gamma / self.k)

    # --- hooks for subclasses (v4) to modulate quoting per symbol/side ----- #
    # `side` is "bid" or "ask". Per-side so v4 can defend asymmetrically (widen
    # only the side toxic flow is attacking, keep the other live to shed risk).
    def _spread_scale(self, symbol: str, side: str) -> float:
        """Multiplier on that side's half-spread (default 1.0)."""
        return 1.0

    def _size_scale(self, symbol: str, side: str) -> float:
        """Multiplier on that side's quote size (default 1.0; v4 pulls ->0)."""
        return 1.0

    def update(
        self,
        market: MarketState,
        theo: TheoState,
        position: PositionState,
    ) -> tuple[list[Quote], list[HedgeOrder]]:
        net_delta = self._net_delta(theo, position)
        comp_term = self._competition_term()

        quotes: list[Quote] = []
        for symbol, tq in theo.quotes.items():
            tau = tq.time_to_expiry
            sigma_dollar = tq.underlying_spot * tq.iv          # underlying $-vol
            under_var = sigma_dollar * sigma_dollar            # underlying $-variance rate

            # (B) reservation price: skew driven by PORTFOLIO delta, weighted by
            #     this option's own delta (its contribution to portfolio risk).
            skew = tq.delta * self.gamma * under_var * tau * net_delta
            reservation = tq.fair_value - skew

            # (A) optimal spread: inventory term uses this option's OWN $-variance.
            option_var = (tq.delta * sigma_dollar) ** 2
            inventory_term = self.gamma * option_var * tau
            raw_half = 0.5 * (inventory_term + comp_term)

            bid_half = max(raw_half * self._spread_scale(symbol, "bid"),
                           self.min_half_spread)
            ask_half = max(raw_half * self._spread_scale(symbol, "ask"),
                           self.min_half_spread)

            quotes.append(
                Quote(
                    symbol=symbol,
                    bid_price=reservation - bid_half,
                    bid_size=self.quote_size * self._size_scale(symbol, "bid"),
                    ask_price=reservation + ask_half,
                    ask_size=self.quote_size * self._size_scale(symbol, "ask"),
                )
            )
        # delta hedging still deferred to v3; v2 manages risk purely via quoting.
        return quotes, []
