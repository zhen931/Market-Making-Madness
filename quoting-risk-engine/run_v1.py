"""
run_v1.py — Paired A/B: v0 (symmetric) vs v1 (inventory skew).

Same world, same seeds, only the engine changes. We're looking for v1 to cut
inventory RMS and inventory-PnL variance vs v0 WITHOUT giving back too much
spread capture.

    python run_v1.py
    python run_v1.py --plot        # save diagnostic plots for both engines
"""

from __future__ import annotations

import argparse

from src.contracts import OptionSpec, OptionType
from src.engine.v0_symmetric import SymmetricQuotingEngine
from src.engine.v1_inventory_skew import InventorySkewQuotingEngine
from src.harness.experiment import WorldConfig, compare, run_engine
from src.metrics.analytics import plot_summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true", help="save diagnostic plots")
    parser.add_argument("--gamma", type=float, default=5.0e-4,
                        help="risk aversion (5e-4 ~ Sharpe-optimal in the default world)")
    args = parser.parse_args()

    world = WorldConfig(
        specs=[OptionSpec("ATM_CALL", strike=100.0, expiry=30.0 / 365.0,
                          option_type=OptionType.CALL)],
    )

    HALF_SPREAD = 0.10
    SIZE = 1.0

    engines = [
        SymmetricQuotingEngine(half_spread=HALF_SPREAD, quote_size=SIZE),
        InventorySkewQuotingEngine(half_spread=HALF_SPREAD, quote_size=SIZE,
                                   gamma=args.gamma),
    ]

    print(f"\nPaired A/B  (gamma={args.gamma})\n")
    compare(world, engines)

    if args.plot:
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        for e in engines:
            res = run_engine(world, e)
            plot_summary(res, save_path=os.path.join(base, f"{e.name}_diagnostics.png"))


if __name__ == "__main__":
    main()
