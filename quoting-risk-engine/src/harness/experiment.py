"""
experiment.py — Reusable paired-A/B scaffolding.

Every version is judged against the previous one on the SAME world and the SAME
random draws, toggling only the engine. This module centralises that so each
`run_vN.py` is just "define the world, list the engines, compare".

The paired discipline (fix seeds, change one thing) is the only honest way to
attribute a PnL difference to a feature rather than to luck.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.contracts import OptionSpec
from src.engine.base import QuotingEngine
from src.harness.simulator import SimResult, Simulator
from src.metrics.analytics import Summary, markout_curve, summarize
from src.mocks.fills import FillModel, InformedFillModel
from src.mocks.market import MockMarket
from src.mocks.pricing import MockPricingEngine


@dataclass
class WorldConfig:
    specs: list[OptionSpec]
    s0: float = 100.0
    mu: float = 0.0
    sigma: float = 0.60          # true underlying vol
    iv: float = 0.60             # IV the mock pricer uses
    n_steps: int = 390
    dt: float = (1.0 / 252.0) / 390
    A: float = 50_000.0          # annualised fill intensity at theo
    k: float = 8.0               # fill-intensity decay per $ of spread
    underlying_half_spread: float = 0.0   # cost of hedging in the underlying ($)
    toxic: bool = False                   # if True, add informed (adverse) flow
    informed_intensity: float = 0.0       # strength of informed flow (toxic world)
    informed_horizon: int = 10            # look-ahead (steps) of the informed flow
    episodes: list = field(default_factory=list)  # toxic bursts: (start,end,drift)
    market_seed: int = 42
    fill_seed: int = 7
    markout_horizons: list[int] = field(default_factory=lambda: [1, 5, 10, 30])


def run_engine(world: WorldConfig, engine: QuotingEngine) -> SimResult:
    """Build a FRESH world (so RNG streams line up) and run one engine through it."""
    market = MockMarket(world.s0, world.mu, world.sigma, world.dt, world.n_steps,
                        seed=world.market_seed, episodes=world.episodes)
    pricing = MockPricingEngine(specs=world.specs, iv=world.iv)
    if world.toxic:
        fills = InformedFillModel(A=world.A, k=world.k, dt=world.dt,
                                  seed=world.fill_seed,
                                  informed_intensity=world.informed_intensity)
        horizon = world.informed_horizon
    else:
        fills = FillModel(A=world.A, k=world.k, dt=world.dt, seed=world.fill_seed)
        horizon = 0
    sim = Simulator(market, pricing, fills, engine,
                    underlying_half_spread=world.underlying_half_spread,
                    informed_horizon=horizon)
    return sim.run()


def compare(world: WorldConfig, engines: list[QuotingEngine]) -> list[SimResult]:
    """Run several engines paired on identical seeds and print a comparison."""
    results = [run_engine(world, e) for e in engines]
    summaries = [summarize(r) for r in results]

    _print_table(summaries)
    _print_markouts(world, results)
    return results


# --------------------------------------------------------------------------- #
def _print_table(summaries: list[Summary]) -> None:
    rows = [
        ("Total PnL (equity)", "final_equity", "{:.2f}"),
        ("Spread capture", "spread_capture", "{:.2f}"),
        ("Inventory/MtM PnL", "inventory_pnl", "{:.2f}"),
        ("Fills", "n_fills", "{:d}"),
        ("Max |inventory|", "max_abs_inventory", "{:.2f}"),
        ("Inventory RMS (risk)", "inventory_rms", "{:.2f}"),
        ("Max |net delta|", "max_abs_net_delta", "{:.2f}"),
        ("Hedge cost ($)", "hedge_cost", "{:.2f}"),
        ("Hedges", "n_hedges", "{:d}"),
        ("Per-step Sharpe-like", "sharpe_like", "{:.3f}"),
    ]
    label_w = max(len(r[0]) for r in rows) + 2
    header = " " * label_w + "".join(f"{s.engine_name:>22}" for s in summaries)
    print(header)
    print("-" * len(header))
    for label, attr, fmt in rows:
        cells = "".join(f"{fmt.format(getattr(s, attr)):>22}" for s in summaries)
        print(f"{label:<{label_w}}{cells}")
    print()


def _print_markouts(world: WorldConfig, results: list[SimResult]) -> None:
    curves = [markout_curve(r, world.markout_horizons) for r in results]
    label_w = 20
    header = " " * label_w + "".join(f"{r.engine_name:>22}" for r in results)
    print("Markout (avg $/fill) — positive & flat = no adverse selection")
    print(header)
    print("-" * len(header))
    for tau in world.markout_horizons:
        cells = "".join(f"{c[tau]:>+22.4f}" for c in curves)
        print(f"{'+' + str(tau) + ' steps':<{label_w}}{cells}")
    print()
