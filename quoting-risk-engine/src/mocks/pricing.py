"""
pricing.py — Mock Pricing Engine (stands in for the Pricing section).

Given the current underlying spot and sim time, it produces a TheoState: a fair
value + Greeks for every configured option, using Black-Scholes at a fixed IV.

Why a flat IV instead of a real surface? Because YOUR engine (the Quoting/Risk
section) only consumes `TheoState`. It does not care whether the fair value came
from a flat vol, a SABR fit, or the real Pricing section. By keeping this dumb we prove the engine
is correctly decoupled. Swap this class for a real adapter later — same output.
"""

from __future__ import annotations

from src.contracts import OptionSpec, TheoQuote, TheoState
from src.mocks.black_scholes import price_and_greeks


class MockPricingEngine:
    def __init__(self, specs: list[OptionSpec], iv: float, r: float = 0.0):
        self.specs = specs
        self.iv = iv
        self.r = r

    def theo(self, t: float, spot: float) -> TheoState:
        state = TheoState(t=t, underlying_spot=spot)
        for spec in self.specs:
            T = max(spec.expiry - t, 0.0)  # remaining time to expiry, in years
            g = price_and_greeks(
                S=spot, K=spec.strike, T=T, sigma=self.iv,
                option_type=spec.option_type, r=self.r,
            )
            state.quotes[spec.symbol] = TheoQuote(
                symbol=spec.symbol,
                fair_value=g["price"],
                iv=self.iv,
                delta=g["delta"],
                gamma=g["gamma"],
                vega=g["vega"],
                theta=g["theta"],
                underlying_spot=spot,
                time_to_expiry=T,
            )
        return state
