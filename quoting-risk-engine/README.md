# Quoting & Risk Engine — Quant Trader section

A research sandbox for the **quoting & risk-management engine** of an options
market-making system. This is the quoting/risk section of a three-part project;
it is built to run **fully standalone** against mock implementations of the other
two sections, so it can be developed and benchmarked before they exist.

```
Pricing section   ──theo_state──┐
                                ├──► QuotingEngine ──quotes/hedges──► Infra/OMS section
Infra/OMS section ──market_state┤
                  ──position_state┘
```

## Layout
```
src/
  contracts.py          # the interface "seams" — agree these with teammates
  mocks/
    black_scholes.py    # analytical pricer + Greeks (truth behind mock Pricing section)
    pricing.py          # MockPricingEngine  -> TheoState   (stands in for Pricing section)
    market.py           # MockMarket (GBM underlying)       (stands in for Infra/OMS section)
    fills.py            # FillModel (exponential intensity)  (stands in for Infra/OMS section)
  engine/
    base.py             # QuotingEngine interface
    v0_symmetric.py     # baseline symmetric MM  (CONTROL)
    v1_inventory_skew.py# A-S reservation-price inventory skew
    v2_avellaneda_stoikov.py # A-S optimal spread + risk-mapped (delta) inventory
    v3_delta_hedge.py   # threshold (no-trade-band) dynamic delta hedging
    v4_toxic_flow.py    # VPIN-style toxic-flow detection + asymmetric defence
  harness/
    simulator.py        # the discrete-time event loop / backtester
    experiment.py       # reusable paired-A/B scaffolding (WorldConfig, compare)
  metrics/
    analytics.py        # PnL decomposition, inventory risk, markout curves
run_v0.py               # entry point
```

## Run
```bash
pip install -r requirements.txt
python run_v0.py            # summary table
python run_v0.py --plot     # + diagnostic plots
```

## Roadmap
- **v0** symmetric MM (control) — *done*
- **v1** inventory skew (reservation price) — *done* (`run_v1.py`; −66% inventory risk, Sharpe ×2.2)
- **v2** Avellaneda–Stoikov optimal spread + risk-mapped inventory — *done* (`run_v2.py`; lowest net-delta, best Sharpe)
- **v3** dynamic delta hedging (no-trade band) — *done* (`run_v3.py`; θ frontier, Sharpe 0.57→0.71)
- **v4** toxic-flow / adverse-selection protection (VPIN-style) — *done* (`run_v4.py`; toxic world PnL −134→−5→+35)

Full design notes & the *why* behind each piece live in the Obsidian vault:
`Documents/Personal/Quant/Options Market Maker Project`.
