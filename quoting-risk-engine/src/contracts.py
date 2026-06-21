"""
contracts.py — The interface "seams" between the three project sections.

Your quoting/risk engine (the Quoting/Risk section) sits in the middle of the system:

    Pricing section   ──theo_state──┐
                                    ├──► QuotingEngine ──quotes/hedges──► Infra/OMS section
    Infra/OMS section ──market_state┤
                      ──position_state┘

As long as everyone agrees on the dataclasses in THIS file, you can build and
test your engine against MOCK implementations of the Infra/OMS and Pricing sections, then swap the
mocks for the real adapters later with zero changes to your engine code.

Sign / unit conventions (agree these with your teammates — see docs):
  * Prices are in quote currency (USD).
  * Inventory / position is in CONTRACTS, positive = long.
  * Delta is per 1 contract, in underlying units (so net delta is in underlying units).
  * Time is in YEARS (annualised), so Greeks like theta/vega are per-year / per-1.0-vol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# --------------------------------------------------------------------------- #
# Instrument definitions
# --------------------------------------------------------------------------- #
class OptionType(Enum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True)
class OptionSpec:
    """Static definition of a single option instrument."""
    symbol: str          # e.g. "BTC-30JUN-60000-C"
    strike: float
    expiry: float        # absolute expiry time on the sim clock, in YEARS
    option_type: OptionType


# --------------------------------------------------------------------------- #
# INPUT 1 — Theoretical pricing state (would come from the Pricing section)
# --------------------------------------------------------------------------- #
@dataclass
class TheoQuote:
    """Fair value + risk sensitivities for ONE option at the current instant."""
    symbol: str
    fair_value: float    # theoretical mid price ("theo")
    iv: float            # implied volatility used to produce fair_value
    delta: float         # ∂V/∂S            (per 1 contract)
    gamma: float         # ∂²V/∂S²
    vega: float          # ∂V/∂σ   (per 1.0 = 100% vol move)
    theta: float         # ∂V/∂t   (per year; typically negative)
    underlying_spot: float
    time_to_expiry: float = 0.0   # τ = expiry − t, in YEARS (needed for A-S skew)


@dataclass
class TheoState:
    """Snapshot of theos for every option we care about, at one timestamp."""
    t: float                                  # sim time in years
    underlying_spot: float
    quotes: dict[str, TheoQuote] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# INPUT 2 — Market state (would come from the Infra/OMS section's normalised order book)
# --------------------------------------------------------------------------- #
@dataclass
class BookTop:
    """Top-of-book for one instrument as seen on the exchange."""
    symbol: str
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float

    @property
    def mid(self) -> float:
        return 0.5 * (self.best_bid + self.best_ask)


@dataclass
class MarketState:
    """Everything the engine knows about the live market at one timestamp."""
    t: float
    underlying_mid: float
    books: dict[str, BookTop] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# INPUT 3 — Our own position / OMS state (would come from the Infra/OMS section)
# --------------------------------------------------------------------------- #
@dataclass
class PositionState:
    """Our current inventory and risk, as tracked by the OMS."""
    inventory: dict[str, float] = field(default_factory=dict)  # symbol -> contracts (signed)
    underlying_position: float = 0.0                           # hedge position in underlying units
    cash: float = 0.0
    net_delta: float = 0.0                                     # aggregate portfolio delta (underlying units)

    def inv(self, symbol: str) -> float:
        return self.inventory.get(symbol, 0.0)


# --------------------------------------------------------------------------- #
# OUTPUTS — what the engine emits each tick
# --------------------------------------------------------------------------- #
class Side(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Quote:
    """A two-sided quote the engine wants resting in the book for one option."""
    symbol: str
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float


@dataclass
class HedgeOrder:
    """A directional order in the underlying to flatten risk (used from v3)."""
    side: Side
    qty: float           # underlying units, always positive
    # None => market/aggressive hedge; a price => passive limit hedge
    limit_price: float | None = None
