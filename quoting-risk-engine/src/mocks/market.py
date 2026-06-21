"""
market.py — Mock Market (stands in for the Infra/OMS section's normalised order book feed).

Simulates the underlying price as Geometric Brownian Motion (GBM) and exposes a
synthetic top-of-book for the underlying. Option books are derived around the
theo by the harness, so this file only needs to drive the underlying path.

GBM is the standard model behind Black-Scholes, so using it here keeps the
"true" data-generating process consistent with the pricer the mock Pricing section
uses — which means in a frictionless world our theos are *correct*, and any PnL
the engine makes comes purely from the spread it captures vs the inventory risk
it carries. That clean separation is what makes the later A/B tests trustworthy.
"""

from __future__ import annotations

import numpy as np


class MockMarket:
    """Generates a GBM underlying path with a fixed RNG seed for reproducibility."""

    def __init__(
        self,
        s0: float,
        mu: float,
        sigma: float,
        dt: float,
        n_steps: int,
        seed: int = 0,
        episodes: list[tuple[int, int, float]] | None = None,
    ):
        self.s0 = s0
        self.mu = mu          # annual drift
        self.sigma = sigma    # annual volatility (the "true" vol)
        self.dt = dt          # step size in years
        self.n_steps = n_steps
        self.seed = seed
        # toxic episodes: (start_step, end_step, drift_per_step_in_log_terms).
        # A burst of sustained one-sided drift -> one-sided informed flow that VPIN
        # can actually detect, and a real adverse move that picks off a naive MM.
        self.episodes = episodes or []
        self._path = self._simulate()

    def _simulate(self) -> np.ndarray:
        rng = np.random.default_rng(self.seed)
        # log-Euler exact discretisation of GBM
        shocks = rng.standard_normal(self.n_steps)
        drift = (self.mu - 0.5 * self.sigma**2) * self.dt
        log_returns = drift + self.sigma * np.sqrt(self.dt) * shocks

        # overlay deterministic drift bursts (toxic episodes)
        for start, end, ep_drift in self.episodes:
            lo, hi = max(start, 0), min(end, self.n_steps)
            log_returns[lo:hi] += ep_drift

        log_path = np.concatenate([[np.log(self.s0)], log_returns]).cumsum()
        return np.exp(log_path)

    def spot(self, step: int) -> float:
        return float(self._path[step])

    @property
    def path(self) -> np.ndarray:
        return self._path
