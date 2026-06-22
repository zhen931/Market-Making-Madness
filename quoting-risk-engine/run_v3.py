"""
run_v3.py — Dynamic delta hedging: v2 (quote-only) vs v3 (quote + hedge), and a
theta (no-trade band) sweep.

Now the world has a HEDGING COST: every hedge crosses `underlying_half_spread` on
the underlying. That's what creates the trade-off v3 exists to navigate.

What to look for:
  * Headline A/B: v3 should cut 'Max |net delta|' hard vs v2, at the price of some
    hedge cost — net win on Sharpe if θ is sensible.
  * theta sweep: small θ -> tiny delta, big hedge cost (churn); large θ -> ≈ v2.
    Sharpe peaks at the θ that balances hedge cost against residual delta risk.

    python run_v3.py
    python run_v3.py --plot
"""

from __future__ import annotations

import argparse

from src.contracts import OptionSpec, OptionType
from src.engine.v2_avellaneda_stoikov import AvellanedaStoikovEngine
from src.engine.v3_delta_hedge import DeltaHedgingEngine
from src.harness.experiment import WorldConfig, compare, run_engine
from src.metrics.analytics import plot_summary, summarize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--gamma", type=float, default=5.0e-4)
    parser.add_argument("--theta", type=float, default=0.5,
                        help="no-trade band (0.5 ~ best total PnL in the default world)")
    args = parser.parse_args()

    world = WorldConfig(
        specs=[
            OptionSpec("CALL_100", strike=100.0, expiry=30.0 / 365.0,
                       option_type=OptionType.CALL),
            OptionSpec("CALL_105", strike=105.0, expiry=30.0 / 365.0,
                       option_type=OptionType.CALL),
        ],
        underlying_half_spread=0.02,   # 2 cents on a ~$100 underlying (~2 bps)
    )

    SIZE = 1.0
    K = world.k
    G = args.gamma

    # ---- headline A/B: v2 vs v3 ------------------------------------------- #
    engines = [
        AvellanedaStoikovEngine(quote_size=SIZE, gamma=G, k=K),
        DeltaHedgingEngine(quote_size=SIZE, gamma=G, k=K, theta=args.theta),
    ]
    print(f"\nv2 vs v3  (gamma={G}, theta={args.theta}, "
          f"hedge cost/unit={world.underlying_half_spread})\n")
    compare(world, engines)

    # ---- theta frontier ---------------------------------------------------- #
    print("theta sweep (v3) — the hedge-cost vs residual-delta-risk frontier")
    print(f"{'theta':>8}{'maxNetDel':>11}{'hedgeCost':>11}{'nHedges':>9}"
          f"{'capture':>10}{'totalPnL':>10}{'sharpe':>9}")
    for theta in [0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 1e9]:
        eng = DeltaHedgingEngine(quote_size=SIZE, gamma=G, k=K, theta=theta)
        s = summarize(run_engine(world, eng))
        label = "inf(=v2)" if theta > 1e8 else f"{theta:.1f}"
        print(f"{label:>8}{s.max_abs_net_delta:>11.2f}{s.hedge_cost:>11.2f}"
              f"{s.n_hedges:>9d}{s.spread_capture:>10.2f}"
              f"{s.final_equity:>10.2f}{s.sharpe_like:>9.3f}")
    print()

    if args.plot:
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        for e in engines:
            res = run_engine(world, e)
            plot_summary(res, save_path=os.path.join(base, f"{e.name}_diagnostics.png"))


if __name__ == "__main__":
    main()
