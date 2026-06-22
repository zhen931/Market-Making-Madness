# Tier 1 Order-Book Matching Engine — Build Summary

Implements **Tier 1** of [`ORDER_BOOK_TIER1.md`](../ORDER_BOOK_TIER1.md): the matching
engine, cancel, fill reporting, and the market-data surface needed to get bots
trading against a single instrument's book.

**Status: complete, builds clean (g++ 13 / C++20, `-Wall -Wextra -Wpedantic`),
ASan/UBSan-clean. 134/134 unit checks pass. A bot connects and trades end-to-end.**

---

## How to run

```bash
cd "order book"
make test    # unit + smoke tests (ASan/UBSan)
make demo    # bot-connectivity demo (ASan/UBSan)
make all     # both
make clean
```

### Test results

```
134/134 checks passed
ALL TESTS PASSED
```

### Bot connectivity demo

A market-making bot and a liquidity-taking bot **connect to the gateway** and
trade through the full path `Participant → Gateway → OrderBook → FillDispatcher →
Participant`:

```
Participant 1 connected.
Participant 2 connected.

-- Maker posts a two-sided quote --
MakerBot 1 quoted  bid 5@100  /  ask 5@101
Book top: bid 5@100  ask 5@101

-- Taker lifts 3 of the offer --
TakerBot 2 lifting 3@101 (IOC)
MakerBot 1 filled SELL 3@101  [MAKER]  pos=-3
TakerBot 2 filled BUY 3@101  [TAKER]  pos=3

-- Maker cancels its residual ask --
MakerBot 1 cancel-ack id=2  ok=true

-- Results --
  [ok]   maker is short 3 after being lifted
  [ok]   taker is long 3 after lifting
  [ok]   dispatcher position book agrees
  [ok]   maker's ask residual was cancelled
  [ok]   maker's bid is still resting
  [ok]   book shows bid-only after the trade

BOT CONNECTED AND TRADED OK
```

This exercises the real seam: registration, market-data fan-out, order emission to
the gateway, price-time matching, maker/taker execution reports, and running
positions. The DES kernel and 25 msg/s token-bucket gateway are **not** built (out
of Tier 1 scope) — the demo's `Gateway` is a minimal synchronous stand-in for the
id/timestamp stamping (§8) and routing (§11.2) so the participant seam is testable.

---

## Files

| File | What it is |
|------|------------|
| [`order_book.hpp`](order_book.hpp) | `PriceLevel::unlink`/`pop_front` (§7.1); `Trade`, `Liquidity`, `ExecutionReport` (§7.2–7.3); the `OrderBook` class + the templated `match_against` matcher (§9) |
| [`order_book.cpp`](order_book.cpp) | `OrderPool` arena/free-list; `OrderBook::submit`/`cancel`/`rest`/`level_for_insert`; read-only surface |
| [`instrument_registry.hpp`](instrument_registry.hpp) | One `OrderBook` per `InstrumentId`, sharing one pool (§11.1) |
| [`fill_dispatcher.hpp`](fill_dispatcher.hpp) | One `Trade` → two `ExecutionReport`s; stamps liquidity flag, fee, running position (§11.4) |
| [`participant.hpp`](participant.hpp) | `Participant` interface + `MarketSnapshot`/`OrderRequest` (§11.5) |
| [`tests/test_order_book.cpp`](tests/test_order_book.cpp) | 14 unit/smoke tests, 134 assertions |
| [`demo/connect_demo.cpp`](demo/connect_demo.cpp) | Two bots connecting + trading end-to-end |
| [`Makefile`](Makefile) | Build + run, sanitizers on |

---

## Acceptance invariants (§13) — all verified by tests

| Invariant | Where |
|-----------|-------|
| **Conservation** — pool returns to baseline, no leaks/double-frees | `test_full_cross`, `test_ioc_*`, `test_cancel`, `test_conservation_mixed` + ASan leak detector |
| **Level invariant** — `total_qty == Σ qty_open`, `order_count == node count` after every mutation | `check_level_invariant` called throughout |
| **Price-time priority** — earliest `ts_accept` fills first | `test_fifo_priority_within_level` |
| **Trade price == maker's resting price**; `qty` ≤ both sides' open | every matching test |
| **IOC leaves nothing resting**; Limit remainder rests + is cancellable | `test_ioc_residual_discarded`, `test_taker_remainder_rests` |
| **Determinism** — identical scripts → identical trade tape | `test_determinism` |

Also tested: multi-level sweep, partial maker remainder rests, Market/FOK rejected,
cancel of head/middle/last-on-level/unknown id, and the end-to-end maker/taker
dispatch with correct liquidity flags + positions.

---

## Design notes & divergence from the pre-existing header

The previous `OrderBook` in `order_book.hpp` was a **declaration-only shell** (no
`.cpp` defined it, no tests referenced it) whose design conflicted with the spec's
**locked** decisions (§4). Those are design-decision clashes, not name clashes, so
they were resolved in favour of the locked decisions:

- **Owned pool → external `OrderPool&`.** §8 requires the gateway/kernel to
  allocate + stamp the `Order` *before* `submit`, so the pool must be shared. The
  `InstrumentRegistry` now owns the single shared pool.
- **`add_resting(const Order&)` (copy-in) → `submit(Order*)`** which takes pointer
  ownership per the §6 lifecycle rule.
- **Removed `modify()`** — locked decision #5: amend = cancel-replace, "the book
  needs no `amend()` method."
- **Kept** the harmless pre-existing read-only accessors (`best_bid/ask_level`,
  `volume_at` as an alias of the spec's `qty_at`, `level_count`, `find`).

> If a teammate was building against `add_resting`/`modify`, flag it — but nothing
> implemented or tested them, so this is the clean path to the spec.

---

## Deferred (Tier 2) — `// TODO(tier2):` hooks left in place

- Market & FOK matching (currently rejected at `submit`)
- Self-trade prevention (the `owner` hook in `match_against`)
- In-place amend / priority-preserving size-down
- Maker/taker fee schedule (`FillDispatcher::default_fee_model` is flat 0)
- The DES kernel, the 25 msg/s token-bucket gateway, latency instrumentation
- The C++ ↔ Python quoting-engine adapter. The seams already line up 1:1:
  `MarketSnapshot ↔ BookTop`, `OrderRequest ↔ Quote/HedgeOrder`,
  `ExecutionReport ↔ fill callbacks`.
