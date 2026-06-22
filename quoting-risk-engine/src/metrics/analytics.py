"""
analytics.py — Turning a SimResult into the numbers that matter.

A market maker is NOT judged on total PnL alone. The whole skill is making money
from spread capture while keeping inventory risk small. So we decompose and
measure exactly that:

  * spread_capture   — cumulative edge earned vs theo at the moment of each fill.
                       This is the "good" PnL: it's what you're paid for providing
                       liquidity.
  * inventory_pnl    — equity change NOT explained by capture. This is the mark-
                       to-market noise from carrying a position while theo moves.
                       Ideally ~0 on average; its VARIANCE is your risk.
  * inventory series — how big a position you carried. A good MM hugs zero.
  * markout(τ)       — average PnL of a fill measured τ steps later. This is the
                       cleanest adverse-selection metric: if you're being picked
                       off (toxic flow), markouts go NEGATIVE. v4 lives or dies
                       on this curve.

These functions are pure (no plotting) so they're easy to unit-test and to call
inside an A/B comparison. `plot_summary` is optional and only imports matplotlib
if you actually call it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.contracts import Side
from src.harness.simulator import SimResult


@dataclass
class Summary:
    engine_name: str
    final_equity: float
    spread_capture: float
    inventory_pnl: float          # final_equity - spread_capture
    n_fills: int
    max_abs_inventory: float
    inventory_rms: float          # root-mean-square inventory (risk proxy)
    max_abs_net_delta: float
    sharpe_like: float            # mean/std of per-step equity change (unitless)
    hedge_cost: float = 0.0       # cumulative $ drag from hedging
    n_hedges: int = 0

    def pretty(self) -> str:
        return (
            f"== {self.engine_name} ============================\n"
            f"  Final equity (total PnL):   {self.final_equity:10.2f}\n"
            f"  Spread capture (the edge):  {self.spread_capture:10.2f}\n"
            f"  Inventory/MtM PnL:          {self.inventory_pnl:10.2f}\n"
            f"  Fills:                      {self.n_fills:10d}\n"
            f"  Max |inventory|:            {self.max_abs_inventory:10.2f}\n"
            f"  Inventory RMS (risk):       {self.inventory_rms:10.2f}\n"
            f"  Max |net delta|:            {self.max_abs_net_delta:10.2f}\n"
            f"  Per-step Sharpe-like:       {self.sharpe_like:10.3f}\n"
        )


def _total_inventory(rec) -> float:
    return sum(rec.inventory.values())


def summarize(result: SimResult) -> Summary:
    h = result.history
    equity = np.array([r.equity for r in h])
    inv = np.array([_total_inventory(r) for r in h])
    net_delta = np.array([r.net_delta for r in h])
    n_fills = sum(len(r.fills) for r in h)

    deq = np.diff(equity)
    sharpe = float(deq.mean() / deq.std()) if deq.std() > 0 else 0.0

    return Summary(
        engine_name=result.engine_name,
        final_equity=float(equity[-1]),
        spread_capture=float(h[-1].spread_capture),
        inventory_pnl=float(equity[-1] - h[-1].spread_capture),
        n_fills=n_fills,
        max_abs_inventory=float(np.abs(inv).max()),
        inventory_rms=float(np.sqrt((inv**2).mean())),
        max_abs_net_delta=float(np.abs(net_delta).max()),
        sharpe_like=sharpe,
        hedge_cost=float(h[-1].hedge_cost),
        n_hedges=int(h[-1].n_hedges),
    )


def markout_curve(result: SimResult, horizons: list[int]) -> dict[int, float]:
    """Average per-fill markout at each horizon τ (in steps).

    For a BUY at price p, markout(τ) = theo[t+τ] − p   (we profit if theo rises).
    For a SELL at price p, markout(τ) = p − theo[t+τ]  (we profit if theo falls).
    Positive = healthy; negative = adverse selection / toxic flow.
    """
    h = result.history
    # theo path per symbol
    symbols = set()
    for r in h:
        symbols.update(r.theos.keys())
    theo_path = {s: np.array([r.theos.get(s, np.nan) for r in h]) for s in symbols}

    out: dict[int, list[float]] = {tau: [] for tau in horizons}
    n = len(h)
    for i, r in enumerate(h):
        for f in r.fills:
            for tau in horizons:
                j = i + tau
                if j >= n:
                    continue
                future_theo = theo_path[f.symbol][j]
                mk = (future_theo - f.price) if f.side is Side.BUY else (f.price - future_theo)
                out[tau].append(mk * f.size)

    return {tau: (float(np.mean(v)) if v else 0.0) for tau, v in out.items()}


def plot_summary(result: SimResult, save_path: str | None = None) -> None:
    """Optional 3-panel diagnostic plot (spot, inventory, PnL decomposition)."""
    import matplotlib.pyplot as plt  # local import keeps it an optional dep

    h = result.history
    t = [r.t for r in h]
    spot = [r.spot for r in h]
    inv = [_total_inventory(r) for r in h]
    equity = [r.equity for r in h]
    capture = [r.spread_capture for r in h]
    inv_pnl = [e - c for e, c in zip(equity, capture)]

    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    axes[0].plot(t, spot, color="black", lw=1)
    axes[0].set_ylabel("underlying spot")
    axes[0].set_title(f"{result.engine_name} — simulation diagnostics")

    axes[1].plot(t, inv, color="tab:blue", lw=1)
    axes[1].axhline(0, color="grey", ls="--", lw=0.7)
    axes[1].set_ylabel("net inventory\n(contracts)")

    axes[2].plot(t, equity, label="total equity (PnL)", color="tab:green")
    axes[2].plot(t, capture, label="spread capture", color="tab:orange")
    axes[2].plot(t, inv_pnl, label="inventory / MtM PnL", color="tab:red", lw=0.8)
    axes[2].axhline(0, color="grey", ls="--", lw=0.7)
    axes[2].set_ylabel("PnL")
    axes[2].set_xlabel("time (years)")
    axes[2].legend(loc="best", fontsize=8)

    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=120)
        print(f"saved plot -> {save_path}")
    else:
        plt.show()
