"""
simulator.py — The simulation loop (our private "backtester").

This is the discrete-time event loop that ties the mocks and the engine together.
Each step it:

    1. asks the MockMarket for the underlying spot,
    2. asks the MockPricingEngine for theos + Greeks (mock Pricing section),
    3. builds a MarketState + PositionState (mock Infra/OMS section),
    4. calls engine.update(...) to get quotes + hedges (YOUR Quoting/Risk section),
    5. runs the FillModel against those quotes and applies the fills,
    6. applies any hedge orders,
    7. records a full snapshot of state for analysis.

It is intentionally single-threaded and bar/step driven — clarity over speed.
The REAL Infra/OMS section is async and latency-critical; none of that belongs here,
because none of it changes the *strategy* decisions we are trying to study.

Reproducibility: the market path and the fill draws each take an explicit seed.
Hold them fixed and toggle ONE engine parameter to get a clean paired A/B — the
only honest way to tell whether a feature actually helped or just got lucky.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.contracts import (
    BookTop,
    HedgeOrder,
    MarketState,
    PositionState,
    Side,
)
from src.engine.base import QuotingEngine
from src.mocks.fills import Fill, FillModel
from src.mocks.market import MockMarket
from src.mocks.pricing import MockPricingEngine


@dataclass
class StepRecord:
    """One row of the simulation history."""
    step: int
    t: float
    spot: float
    cash: float
    inventory: dict[str, float]
    underlying_position: float
    net_delta: float
    equity: float                 # mark-to-market total PnL
    spread_capture: float         # cumulative edge captured vs theo at fill
    hedge_cost: float = 0.0       # cumulative $ paid crossing the underlying spread
    n_hedges: int = 0             # cumulative count of hedge trades
    fills: list[Fill] = field(default_factory=list)
    quotes: dict[str, tuple[float, float]] = field(default_factory=dict)  # sym -> (bid, ask)
    theos: dict[str, float] = field(default_factory=dict)


@dataclass
class SimResult:
    engine_name: str
    history: list[StepRecord]

    @property
    def final_equity(self) -> float:
        return self.history[-1].equity

    @property
    def final_capture(self) -> float:
        return self.history[-1].spread_capture


class Simulator:
    def __init__(
        self,
        market: MockMarket,
        pricing: MockPricingEngine,
        fill_model: FillModel,
        engine: QuotingEngine,
        market_half_spread: float = 0.05,
        underlying_half_spread: float = 0.0,
        informed_horizon: int = 0,
    ):
        self.market = market
        self.pricing = pricing
        self.fill_model = fill_model
        self.engine = engine
        # look-ahead (in steps) the informed flow uses; 0 = no informed flow.
        self.informed_horizon = informed_horizon
        # synthetic exchange book width around theo, for realism / future use
        self.market_half_spread = market_half_spread
        # cost of hedging: a hedge crosses this half-spread on the underlying.
        # 0.0 keeps v0-v2 (which never hedge) and the no-cost case unchanged.
        self.underlying_half_spread = underlying_half_spread

        # mutable portfolio state
        self.cash = 0.0
        self.inventory: dict[str, float] = {}
        self.underlying_position = 0.0
        self.cum_capture = 0.0
        self.cum_hedge_cost = 0.0
        self.n_hedges = 0

    # --------------------------------------------------------------------- #
    def _net_delta(self, theo_state) -> float:
        """Aggregate portfolio delta in underlying units (options + hedge)."""
        d = self.underlying_position  # the underlying itself has delta 1
        for sym, qty in self.inventory.items():
            d += qty * theo_state.quotes[sym].delta
        return d

    def _equity(self, theo_state, spot: float) -> float:
        """Mark-to-market total PnL: cash + option MtM + underlying MtM."""
        eq = self.cash + self.underlying_position * spot
        for sym, qty in self.inventory.items():
            eq += qty * theo_state.quotes[sym].fair_value
        return eq

    def _apply_fill(self, fill: Fill) -> None:
        sign = 1.0 if fill.side is Side.BUY else -1.0
        self.inventory[fill.symbol] = self.inventory.get(fill.symbol, 0.0) + sign * fill.size
        self.cash -= sign * fill.price * fill.size
        # Edge captured = how far the fill was on the GOOD side of theo for us.
        # Buy below theo or sell above theo => positive capture.
        edge = (fill.theo_at_fill - fill.price) if fill.side is Side.BUY else (fill.price - fill.theo_at_fill)
        self.cum_capture += edge * fill.size

    def _apply_hedge(self, hedge: HedgeOrder, spot: float) -> None:
        # Hedge crosses the underlying spread: buy pays spot+c, sell gets spot-c.
        # (the Infra/OMS section would model fuller slippage; this captures the core cost.)
        sign = 1.0 if hedge.side is Side.BUY else -1.0
        fill_price = spot + sign * self.underlying_half_spread
        self.underlying_position += sign * hedge.qty
        self.cash -= sign * hedge.qty * fill_price
        self.cum_hedge_cost += hedge.qty * self.underlying_half_spread
        self.n_hedges += 1

    # --------------------------------------------------------------------- #
    def run(self) -> SimResult:
        history: list[StepRecord] = []

        for step in range(self.market.n_steps + 1):
            t = step * self.market.dt
            spot = self.market.spot(step)
            theo_state = self.pricing.theo(t, spot)

            # future theos (from the precomputed path) drive the informed flow
            future_theo_state = None
            if self.informed_horizon > 0:
                fstep = min(step + self.informed_horizon, self.market.n_steps)
                future_theo_state = self.pricing.theo(fstep * self.market.dt,
                                                      self.market.spot(fstep))

            # ---- build mock Infra/OMS-section views ---------------------- #
            books = {
                sym: BookTop(
                    symbol=sym,
                    best_bid=tq.fair_value - self.market_half_spread,
                    best_ask=tq.fair_value + self.market_half_spread,
                    bid_size=10.0,
                    ask_size=10.0,
                )
                for sym, tq in theo_state.quotes.items()
            }
            market_state = MarketState(t=t, underlying_mid=spot, books=books)
            position_state = PositionState(
                inventory=dict(self.inventory),
                underlying_position=self.underlying_position,
                cash=self.cash,
                net_delta=self._net_delta(theo_state),
            )

            # ---- YOUR engine decides ------------------------------------- #
            quotes, hedges = self.engine.update(market_state, theo_state, position_state)

            # ---- match fills against the engine's quotes ----------------- #
            step_fills: list[Fill] = []
            quote_record: dict[str, tuple[float, float]] = {}
            for q in quotes:
                quote_record[q.symbol] = (q.bid_price, q.ask_price)
                theo = theo_state.quotes[q.symbol].fair_value
                future_theo = (future_theo_state.quotes[q.symbol].fair_value
                               if future_theo_state else None)
                for f in self.fill_model.simulate_fills(q, theo, future_theo):
                    self._apply_fill(f)
                    step_fills.append(f)

            # ---- apply hedges -------------------------------------------- #
            for h in hedges:
                self._apply_hedge(h, spot)

            # ---- record snapshot ----------------------------------------- #
            history.append(
                StepRecord(
                    step=step,
                    t=t,
                    spot=spot,
                    cash=self.cash,
                    inventory=dict(self.inventory),
                    underlying_position=self.underlying_position,
                    net_delta=self._net_delta(theo_state),
                    equity=self._equity(theo_state, spot),
                    spread_capture=self.cum_capture,
                    hedge_cost=self.cum_hedge_cost,
                    n_hedges=self.n_hedges,
                    fills=step_fills,
                    quotes=quote_record,
                    theos={s: tq.fair_value for s, tq in theo_state.quotes.items()},
                )
            )

        return SimResult(engine_name=self.engine.name, history=history)
