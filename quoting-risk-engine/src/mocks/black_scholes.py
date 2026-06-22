"""
black_scholes.py — Analytical option pricing + Greeks.

This is the maths the *Sell-Side Quant* (the Pricing section) owns for real, complete with
SABR/SVI surface fitting. For YOUR purposes it is a stand-in "source of truth":
a clean, deterministic pricer that lets you generate theos and Greeks so the
quoting engine has something to consume. When the Pricing section is ready you delete this
and read their TheoState instead — your engine never knows the difference.

Pure-Python/standard-library implementation (math.erf for the normal CDF) so the
project has no scipy dependency. Everything is per-1-contract.
"""

from __future__ import annotations

import math

from src.contracts import OptionType

_SQRT_2PI = math.sqrt(2.0 * math.pi)
# Below this time-to-expiry (≈ a few minutes in years) we treat the option as
# expiring and return intrinsic value to avoid divide-by-zero in d1/d2.
_MIN_T = 1e-8


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    vol_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def price_and_greeks(
    S: float,
    K: float,
    T: float,
    sigma: float,
    option_type: OptionType,
    r: float = 0.0,
) -> dict[str, float]:
    """Return {price, delta, gamma, vega, theta} for one option.

    Conventions:
      * vega is per 1.0 (=100%) change in vol.
      * theta is per YEAR (so a daily theta is theta/365).
    """
    is_call = option_type is OptionType.CALL

    # --- expiry / degenerate vol guard: fall back to intrinsic value -------- #
    if T <= _MIN_T or sigma <= 0.0:
        intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
        delta = (1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0)
        return {"price": intrinsic, "delta": delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0}

    d1, d2 = _d1_d2(S, K, T, r, sigma)
    pdf_d1 = _norm_pdf(d1)
    disc = math.exp(-r * T)
    sqrt_t = math.sqrt(T)

    if is_call:
        price = S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta = (-(S * pdf_d1 * sigma) / (2.0 * sqrt_t)
                 - r * K * disc * _norm_cdf(d2))
    else:
        price = K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta = (-(S * pdf_d1 * sigma) / (2.0 * sqrt_t)
                 + r * K * disc * _norm_cdf(-d2))

    # gamma and vega are identical for calls and puts (put-call parity)
    gamma = pdf_d1 / (S * sigma * sqrt_t)
    vega = S * pdf_d1 * sqrt_t

    return {"price": price, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta}
