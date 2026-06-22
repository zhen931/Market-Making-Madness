"""
run_v2.py — Paired A/B across v0, v1, v2 on a TWO-OPTION book.

The world now has two calls (strikes 100 and 105). This is what makes v2's
risk-mapping matter: v1 manages each leg's inventory independently, while v2
aggregates both onto net portfolio delta and quotes the chain as one book.

What to look for:
  * v2 should hold the LOWEST 'Max |net delta|' — that's the joint-risk control.
  * v2 sets its OWN spread width (A-S optimal), so its capture/fill profile will
    differ from the fixed-0.10 engines; judge it on risk-adjusted terms (Sharpe)
    and net-delta control, not raw PnL alone.

    python run_v2.py
    python run_v2.py --plot
"""

from __future__ import annotations

import argparse

from src.contracts import OptionSpec, OptionType
from src.engine.v0_symmetric import SymmetricQuotingEngine
from src.engine.v1_inventory_skew import InventorySkewQuotingEngine
from src.engine.v2_avellaneda_stoikov import AvellanedaStoikovEngine
from src.harness.experiment import WorldConfig, compare, run_engine
from src.metrics.analytics import plot_summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--gamma", type=float, default=5.0e-4)
    args = parser.parse_args()

    world = WorldConfig(
        specs=[
            OptionSpec("CALL_100", strike=100.0, expiry=30.0 / 365.0,
                       option_type=OptionType.CALL),
            OptionSpec("CALL_105", strike=105.0, expiry=30.0 / 365.0,
                       option_type=OptionType.CALL),
        ],
    )

    HALF_SPREAD = 0.10
    SIZE = 1.0
    K = world.k  # v2 uses the true arrival decay from the fill model

    engines = [
        SymmetricQuotingEngine(half_spread=HALF_SPREAD, quote_size=SIZE),
        InventorySkewQuotingEngine(half_spread=HALF_SPREAD, quote_size=SIZE,
                                   gamma=args.gamma),
        AvellanedaStoikovEngine(quote_size=SIZE, gamma=args.gamma, k=K),
    ]

    print(f"\nPaired A/B on a 2-call book  (gamma={args.gamma}, k={K})\n")
    compare(world, engines)

    if args.plot:
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        for e in engines:
            res = run_engine(world, e)
            plot_summary(res, save_path=os.path.join(base, f"{e.name}_diagnostics.png"))


if __name__ == "__main__":
    main()
