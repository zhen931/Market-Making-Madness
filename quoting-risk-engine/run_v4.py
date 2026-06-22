"""
run_v4.py — Toxic-flow / adverse-selection protection.

This introduces a TOXIC world: on top of noise flow, bursts of *informed* flow
arrive during "episodes" (sustained directional moves) and pick off a naive MM.

Two things to show:
  1. Adverse selection is real: the same v2 engine that prints +PnL in the benign
     world prints a big LOSS once toxic episodes are added.
  2. The three defences are complementary — lean (v2 skew) / flatten (v3 hedge) /
     refuse (v4 toxicity protection). Only the full stack stays profitable.

    python run_v4.py
    python run_v4.py --plot
"""

from __future__ import annotations

import argparse

from src.contracts import OptionSpec, OptionType
from src.engine.v2_avellaneda_stoikov import AvellanedaStoikovEngine
from src.engine.v3_delta_hedge import DeltaHedgingEngine
from src.engine.v4_toxic_flow import ToxicFlowEngine
from src.harness.experiment import WorldConfig, compare, run_engine
from src.metrics.analytics import plot_summary, summarize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()

    specs = [
        OptionSpec("CALL_100", strike=100.0, expiry=30.0 / 365.0,
                   option_type=OptionType.CALL),
        OptionSpec("CALL_105", strike=105.0, expiry=30.0 / 365.0,
                   option_type=OptionType.CALL),
    ]
    # toxic episodes: sustained one-sided drift bursts (up ~steps 90-150, down 240-300)
    episodes = [(90, 150, 0.0040), (240, 300, -0.0040)]

    benign = WorldConfig(specs=specs, underlying_half_spread=0.02,
                         markout_horizons=[5, 15, 30])
    toxic = WorldConfig(specs=specs, underlying_half_spread=0.02,
                        toxic=True, informed_intensity=1e6, informed_horizon=15,
                        episodes=episodes, markout_horizons=[5, 15, 30])

    G, K, SIZE = 5.0e-4, 8.0, 1.0

    # ---- 1. adverse selection is real: same engine, benign vs toxic ------- #
    v2 = AvellanedaStoikovEngine(quote_size=SIZE, gamma=G, k=K)
    sb = summarize(run_engine(benign, v2))
    st = summarize(run_engine(toxic, v2))
    print("\n1) Same v2 engine, benign world vs toxic world")
    print(f"   benign:  PnL={sb.final_equity:8.2f}  Sharpe={sb.sharpe_like:6.3f}")
    print(f"   toxic:   PnL={st.final_equity:8.2f}  Sharpe={st.sharpe_like:6.3f}   <- adverse selection bites\n")

    # ---- 2. the three defences, all in the toxic world -------------------- #
    print("2) Defence stack in the TOXIC world  (lean / flatten / refuse)")
    engines = [
        AvellanedaStoikovEngine(quote_size=SIZE, gamma=G, k=K),
        DeltaHedgingEngine(quote_size=SIZE, gamma=G, k=K, theta=0.5),
        ToxicFlowEngine(quote_size=SIZE, gamma=G, k=K, theta=0.5),
    ]
    compare(toxic, engines)

    if args.plot:
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        for e in engines:
            res = run_engine(toxic, e)
            plot_summary(res, save_path=os.path.join(base, f"{e.name}_toxic.png"))


if __name__ == "__main__":
    main()
