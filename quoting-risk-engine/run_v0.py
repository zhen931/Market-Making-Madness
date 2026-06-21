"""
run_v0.py — Entry point for the v0 baseline market maker.

Run from the project root:

    python run_v0.py            # prints a summary table
    python run_v0.py --plot     # also shows/saves the diagnostic plot

Everything is parameterised at the top of main() so you can see exactly what
world the engine is trading in. Change ONE thing at a time and keep the seeds
fixed when you start comparing versions.
"""

from __future__ import annotations

import argparse

from src.contracts import OptionSpec, OptionType
from src.engine.v0_symmetric import SymmetricQuotingEngine
from src.harness.simulator import Simulator
from src.metrics.analytics import markout_curve, plot_summary, summarize
from src.mocks.fills import FillModel
from src.mocks.market import MockMarket
from src.mocks.pricing import MockPricingEngine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true", help="show diagnostic plot")
    parser.add_argument("--save", default=None, help="save plot to this path")
    args = parser.parse_args()

    # --- world parameters --------------------------------------------------- #
    S0 = 100.0          # underlying start price
    MU = 0.0            # annual drift (0 = no edge from direction)
    SIGMA = 0.60        # annual vol of the underlying (crypto-ish, 60%)
    IV = 0.60           # the IV our (mock) pricer uses == true vol => fair theos

    # one trading day, one step per minute
    YEAR = 1.0
    TRADING_DAY = 1.0 / 252.0
    N_STEPS = 390
    DT = TRADING_DAY / N_STEPS

    # a single ATM call expiring in 30 calendar days
    specs = [OptionSpec("ATM_CALL", strike=100.0, expiry=30.0 / 365.0,
                        option_type=OptionType.CALL)]

    # Fill model: λ(δ) = A·exp(−k·δ).  A is the ANNUALISED arrival intensity at
    # theo (δ=0). It looks large because a year holds ~98k trading minutes:
    # A=50000/yr ≈ 130 fills/day if we quoted right at theo. k is per $ of spread.
    A = 50_000.0
    K = 8.0             # flow sensitivity to our spread

    # --- build the world ---------------------------------------------------- #
    market = MockMarket(s0=S0, mu=MU, sigma=SIGMA, dt=DT, n_steps=N_STEPS, seed=42)
    pricing = MockPricingEngine(specs=specs, iv=IV)
    fills = FillModel(A=A, k=K, dt=DT, seed=7)

    engine = SymmetricQuotingEngine(half_spread=0.10, quote_size=1.0)

    sim = Simulator(market, pricing, fills, engine)
    result = sim.run()

    # --- report ------------------------------------------------------------- #
    summary = summarize(result)
    print(summary.pretty())

    horizons = [1, 5, 10, 30]
    mk = markout_curve(result, horizons)
    print("  Markout (avg per fill, $):")
    for tau in horizons:
        print(f"    +{tau:>3} steps: {mk[tau]:+.4f}")
    print()

    if args.plot or args.save:
        plot_summary(result, save_path=args.save)


if __name__ == "__main__":
    main()
