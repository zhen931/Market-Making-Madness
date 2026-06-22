// Tier 1 unit / smoke tests for the order-book matching engine.
// Lightweight assert harness (no external framework). Built with ASan/UBSan.
//
// Covers ORDER_BOOK_TIER1.md Â§12 build steps and Â§13 acceptance invariants:
//   * PriceLevel total_qty / order_count invariant after every mutation
//   * resting / best-bid-ask / depth surface
//   * Limit matching: full cross, partial fills, multi-level sweep, FIFO priority
//   * IOC residual discarded; Market / FOK rejected
//   * cancel (head / middle / last-on-level / unknown id)
//   * pool conservation (no leaks / double-frees)
//   * determinism (identical scripts -> identical trade tape)
//   * end-to-end maker/taker dispatch (liquidity flag, running position)

#include "order_book.hpp"
#include "instrument_registry.hpp"
#include "fill_dispatcher.hpp"

#include <iostream>
#include <optional>
#include <vector>

using namespace clob;

// ---- tiny test harness ------------------------------------------------------
static int g_failures = 0;
static int g_checks   = 0;

#define CHECK(cond)                                                            \
    do {                                                                       \
        ++g_checks;                                                            \
        if (!(cond)) {                                                         \
            ++g_failures;                                                      \
            std::cerr << "FAIL " << __FILE__ << ':' << __LINE__                \
                      << "  " #cond "\n";                                      \
        }                                                                      \
    } while (0)

#define CHECK_EQ(a, b)                                                         \
    do {                                                                       \
        ++g_checks;                                                            \
        auto _a = (a);                                                         \
        auto _b = (b);                                                         \
        if (!(_a == _b)) {                                                     \
            ++g_failures;                                                      \
            std::cerr << "FAIL " << __FILE__ << ':' << __LINE__                \
                      << "  " #a " == " #b "  (" << +_a << " vs " << +_b       \
                      << ")\n";                                                \
        }                                                                      \
    } while (0)

// Live (allocated-but-not-freed) Order count. Conservation == this returns to 0
// once every order has been filled or cancelled, independent of pool growth.
static std::size_t live(const OrderPool& p) { return p.capacity() - p.free_count(); }

// Submit when we don't care about the (nodiscard) trade result â e.g. resting a
// maker that doesn't cross. Keeps intent explicit and the build warning-free.
static void place(OrderBook& book, Order* o) { (void)book.submit(o); }

// Stands in for the gateway/kernel (Â§8): allocate from the pool + stamp identity,
// monotonic id, and an ever-increasing accept timestamp (drives time priority).
struct Entry {
    OrderPool& pool;
    OrderId    next_id = 1;
    Timestamp  clock   = 0;

    Order* make(InstrumentId inst, ParticipantId owner, Side side, OrdType type,
                Price px, Quantity qty) {
        Order* o      = pool.allocate();
        o->id         = next_id++;
        o->instrument = inst;
        o->owner      = owner;
        o->side       = side;
        o->type       = type;
        o->price      = px;
        o->qty_open   = qty;
        o->qty_orig   = qty;
        o->ts_accept  = ++clock;
        return o;
    }
};

static bool trade_eq(const Trade& a, const Trade& b) {
    return a.instrument == b.instrument && a.price == b.price && a.qty == b.qty &&
           a.maker_order_id == b.maker_order_id && a.maker == b.maker &&
           a.maker_side == b.maker_side && a.taker_order_id == b.taker_order_id &&
           a.taker == b.taker && a.taker_side == b.taker_side && a.ts == b.ts;
}

// PriceLevel invariant: total_qty == Î£ qty_open and order_count == node count.
static void check_level_invariant(const PriceLevel& lvl) {
    Quantity    sum = 0;
    std::size_t n   = 0;
    for (const Order* o = lvl.head; o; o = o->next) {
        sum += o->qty_open;
        ++n;
    }
    CHECK_EQ(lvl.total_qty, sum);
    CHECK_EQ(lvl.order_count, n);
    if (n == 0) {
        CHECK(lvl.head == nullptr && lvl.tail == nullptr);
    }
}

constexpr InstrumentId INST = 1;

// ---- Step 1: PriceLevel removal primitives ----------------------------------
static void test_price_level_primitives() {
    Order a{}, b{}, c{};
    a.id = 1; a.qty_open = 5;
    b.id = 2; b.qty_open = 7;
    c.id = 3; c.qty_open = 2;

    PriceLevel lvl;
    lvl.price = 100;
    lvl.push_back(&a);
    lvl.push_back(&b);
    lvl.push_back(&c);
    CHECK_EQ(lvl.order_count, 3u);
    CHECK_EQ(lvl.total_qty, 14);
    CHECK(lvl.head == &a && lvl.tail == &c);
    check_level_invariant(lvl);

    // unlink the middle node
    lvl.unlink(&b);
    CHECK_EQ(lvl.order_count, 2u);
    CHECK_EQ(lvl.total_qty, 7);
    CHECK(b.prev == nullptr && b.next == nullptr && b.level == nullptr);
    CHECK(a.next == &c && c.prev == &a);
    check_level_invariant(lvl);

    // pop the head, then the remaining node, then empty
    Order* popped = lvl.pop_front();
    CHECK(popped == &a);
    CHECK_EQ(lvl.order_count, 1u);
    CHECK_EQ(lvl.total_qty, 2);
    CHECK(lvl.head == &c && lvl.tail == &c);
    check_level_invariant(lvl);

    CHECK(lvl.pop_front() == &c);
    CHECK(lvl.empty());
    CHECK(lvl.pop_front() == nullptr);
    check_level_invariant(lvl);
}

// ---- Step 3: resting + read-only surface (no matching) ----------------------
static void test_resting_and_surface() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};
    const std::size_t base = live(pool);

    auto t1 = book.submit(e.make(INST, 1, Side::Buy,  OrdType::Limit, 100, 5));   // bid
    auto t2 = book.submit(e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 7));   // ask
    CHECK(t1.empty() && t2.empty());

    CHECK(book.has_bids() && book.has_asks());
    CHECK_EQ(book.best_bid(), 100);
    CHECK_EQ(book.best_ask(), 101);
    CHECK_EQ(book.qty_at(Side::Buy, 100), 5);
    CHECK_EQ(book.qty_at(Side::Sell, 101), 7);
    CHECK_EQ(book.qty_at(Side::Buy, 999), 0);      // empty level -> 0
    CHECK_EQ(book.volume_at(Side::Sell, 101), 7);  // alias agrees

    // add a second, deeper bid; depth must come back best-first
    place(book, e.make(INST, 1, Side::Buy, OrdType::Limit, 99, 3));
    auto d = book.depth(Side::Buy, 10);
    CHECK_EQ(d.size(), 2u);
    CHECK_EQ(d[0].first, 100);  CHECK_EQ(d[0].second, 5);
    CHECK_EQ(d[1].first, 99);   CHECK_EQ(d[1].second, 3);
    CHECK_EQ(book.depth(Side::Buy, 1).size(), 1u);  // n caps the result

    CHECK_EQ(book.level_count(Side::Buy), 2u);
    CHECK_EQ(book.level_count(Side::Sell), 1u);
    CHECK(book.find(1) != nullptr);
    CHECK(book.find(99999) == nullptr);

    // unwind -> pool conserved
    CHECK(book.cancel(1) && book.cancel(2) && book.cancel(3));
    CHECK_EQ(live(pool), base);
}

// ---- Step 4: Limit matching -------------------------------------------------
static void test_full_cross() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};
    const std::size_t base = live(pool);

    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 5));        // maker ask
    auto trades = book.submit(e.make(INST, 2, Side::Buy, OrdType::Limit, 101, 5)); // taker

    CHECK_EQ(trades.size(), 1u);
    CHECK_EQ(trades[0].price, 101);   // maker's resting price
    CHECK_EQ(trades[0].qty, 5);
    CHECK(trades[0].maker_side == Side::Sell);
    CHECK(trades[0].taker_side == Side::Buy);
    CHECK(!book.has_asks() && !book.has_bids());  // both consumed
    CHECK_EQ(live(pool), base);                    // maker freed, taker freed
}

static void test_partial_maker_remainder_rests() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};

    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 10));            // maker ask 10
    auto trades = book.submit(e.make(INST, 2, Side::Buy, OrdType::Limit, 101, 4)); // taker buys 4

    CHECK_EQ(trades.size(), 1u);
    CHECK_EQ(trades[0].qty, 4);
    CHECK(book.has_asks());
    CHECK_EQ(book.best_ask(), 101);
    CHECK_EQ(book.qty_at(Side::Sell, 101), 6);   // remainder rests
    CHECK(!book.has_bids());                     // taker fully filled, did not rest
    check_level_invariant(*book.best_ask_level());
}

static void test_taker_remainder_rests() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};

    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 3));              // maker ask 3
    auto trades = book.submit(e.make(INST, 2, Side::Buy, OrdType::Limit, 101, 10)); // taker buys 10

    CHECK_EQ(trades.size(), 1u);
    CHECK_EQ(trades[0].qty, 3);
    CHECK(!book.has_asks());                      // maker fully consumed
    CHECK(book.has_bids());
    CHECK_EQ(book.best_bid(), 101);
    CHECK_EQ(book.qty_at(Side::Buy, 101), 7);     // taker remainder rests as a bid
}

static void test_multi_level_sweep() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};

    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 2));
    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 102, 2));
    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 103, 2));

    // Buy 5 @ limit 102: sweeps 101 (2) + 102 (2); 103 is not marketable.
    auto trades = book.submit(e.make(INST, 2, Side::Buy, OrdType::Limit, 102, 5));
    CHECK_EQ(trades.size(), 2u);
    CHECK_EQ(trades[0].price, 101);  CHECK_EQ(trades[0].qty, 2);
    CHECK_EQ(trades[1].price, 102);  CHECK_EQ(trades[1].qty, 2);

    CHECK_EQ(book.qty_at(Side::Sell, 101), 0);  // gone
    CHECK_EQ(book.level_count(Side::Sell), 1u); // only 103 remains
    CHECK_EQ(book.best_ask(), 103);
    CHECK_EQ(book.qty_at(Side::Buy, 102), 1);   // 1 unfilled rests as a bid
}

static void test_fifo_priority_within_level() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};

    Order* first  = e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 2); // earlier ts
    OrderId first_id = first->id;
    place(book, first);
    Order* second = e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 2); // later ts
    OrderId second_id = second->id;
    place(book, second);

    // Buy 3: fills the earliest (first) fully, then 1 from second.
    auto trades = book.submit(e.make(INST, 2, Side::Buy, OrdType::Limit, 101, 3));
    CHECK_EQ(trades.size(), 2u);
    CHECK_EQ(trades[0].maker_order_id, first_id);   // FIFO: earliest first
    CHECK_EQ(trades[0].qty, 2);
    CHECK_EQ(trades[1].maker_order_id, second_id);
    CHECK_EQ(trades[1].qty, 1);
    CHECK_EQ(book.qty_at(Side::Sell, 101), 1);      // second's remainder

    // qty never exceeds either side's open
    for (const auto& t : trades) { CHECK(t.qty > 0 && t.qty <= 2); }
}

// ---- Step 5: IOC + reject Market / FOK --------------------------------------
static void test_ioc_residual_discarded() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};
    const std::size_t base = live(pool);

    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 3));         // maker ask 3
    auto trades = book.submit(e.make(INST, 2, Side::Buy, OrdType::IOC, 101, 10));

    CHECK_EQ(trades.size(), 1u);
    CHECK_EQ(trades[0].qty, 3);
    CHECK(!book.has_bids());              // IOC residual (7) never rests
    CHECK(!book.has_asks());              // maker consumed
    CHECK_EQ(live(pool), base);           // residual discarded, pool conserved
}

static void test_ioc_unmarketable() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};

    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 3));
    const std::size_t before = live(pool);
    auto trades = book.submit(e.make(INST, 2, Side::Buy, OrdType::IOC, 100, 5)); // 100 < 101
    CHECK(trades.empty());
    CHECK(!book.has_bids());
    CHECK_EQ(live(pool), before);        // unmarketable IOC fully discarded
}

static void test_reject_market_and_fok() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};

    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 5));
    const std::size_t before = live(pool);

    auto m = book.submit(e.make(INST, 2, Side::Buy, OrdType::Market, 0, 5));
    CHECK(m.empty());
    auto f = book.submit(e.make(INST, 2, Side::Buy, OrdType::FOK, 101, 5));
    CHECK(f.empty());

    CHECK_EQ(book.qty_at(Side::Sell, 101), 5);  // resting maker untouched
    CHECK(!book.has_bids());
    CHECK_EQ(live(pool), before);               // both rejected orders freed
}

// ---- Step 6: cancel ---------------------------------------------------------
static void test_cancel() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};
    const std::size_t base = live(pool);

    // three bids at the same price: ids 1 (head), 2 (middle), 3 (tail)
    place(book, e.make(INST, 1, Side::Buy, OrdType::Limit, 100, 1));
    place(book, e.make(INST, 1, Side::Buy, OrdType::Limit, 100, 2));
    place(book, e.make(INST, 1, Side::Buy, OrdType::Limit, 100, 3));
    CHECK_EQ(book.qty_at(Side::Buy, 100), 6);

    CHECK(book.cancel(2));                       // middle
    CHECK_EQ(book.qty_at(Side::Buy, 100), 4);
    check_level_invariant(*book.best_bid_level());

    CHECK(book.cancel(1));                       // head
    CHECK_EQ(book.qty_at(Side::Buy, 100), 3);
    check_level_invariant(*book.best_bid_level());

    CHECK(!book.cancel(1));                       // already gone -> false
    CHECK(!book.cancel(424242));                  // unknown id -> false

    CHECK(book.cancel(3));                        // last on the level
    CHECK(!book.has_bids());                      // level erased
    CHECK_EQ(book.level_count(Side::Buy), 0u);
    CHECK_EQ(live(pool), base);                   // pool balanced
}

// ---- Â§13: conservation across a mixed sequence ------------------------------
static void test_conservation_mixed() {
    OrderPool pool(16);   // small reserve so the run exercises pool growth too
    OrderBook book(INST, pool);
    Entry e{pool};
    const std::size_t base = live(pool);

    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 105, 4));
    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 106, 6));
    place(book, e.make(INST, 2, Side::Buy,  OrdType::Limit, 104, 5));
    place(book, e.make(INST, 3, Side::Buy,  OrdType::Limit, 105, 2));   // crosses: fills 2 of the 105 ask
    place(book, e.make(INST, 4, Side::Sell, OrdType::IOC,   104, 3));   // crosses bids, residual discarded
    book.cancel(1);                                                     // cancel whatever 105 remainder exists
    book.cancel(2);

    // Drain the remaining book so live() can return to baseline.
    for (OrderId id = 1; id <= e.next_id; ++id) { book.cancel(id); }
    CHECK_EQ(live(pool), base);
    CHECK(!book.has_bids() && !book.has_asks());
}

// ---- Â§13: determinism -------------------------------------------------------
static std::vector<Trade> run_script() {
    OrderPool pool(64);
    OrderBook book(INST, pool);
    Entry e{pool};
    std::vector<Trade> tape;

    auto feed = [&](ParticipantId p, Side s, OrdType t, Price px, Quantity q) {
        auto tr = book.submit(e.make(INST, p, s, t, px, q));
        tape.insert(tape.end(), tr.begin(), tr.end());
    };

    feed(1, Side::Sell, OrdType::Limit, 101, 2);
    feed(2, Side::Sell, OrdType::Limit, 101, 3);   // same price, later time
    feed(3, Side::Sell, OrdType::Limit, 102, 5);
    feed(4, Side::Buy,  OrdType::Limit, 99, 4);
    feed(5, Side::Buy,  OrdType::Limit, 102, 6);    // sweeps 101x2, 101x3, 102x1
    return tape;
}

static void test_determinism() {
    auto a = run_script();
    auto b = run_script();
    CHECK_EQ(a.size(), b.size());
    CHECK_EQ(a.size(), 3u);
    bool identical = a.size() == b.size();
    for (std::size_t i = 0; identical && i < a.size(); ++i) {
        identical = trade_eq(a[i], b[i]);
    }
    CHECK(identical);
    // spot-check the expected tape
    CHECK_EQ(a[0].price, 101); CHECK_EQ(a[0].qty, 2);
    CHECK_EQ(a[1].price, 101); CHECK_EQ(a[1].qty, 3);
    CHECK_EQ(a[2].price, 102); CHECK_EQ(a[2].qty, 1);
}

// ---- Step 7: end-to-end maker/taker via registry + dispatcher ---------------
static void test_end_to_end_dispatch() {
    OrderPool pool(64);
    InstrumentRegistry registry(pool);
    FillDispatcher dispatcher;  // default flat, fee-free model
    Entry e{pool};

    std::optional<ExecutionReport> maker_rep, taker_rep;
    dispatcher.on_execution(1, [&](const ExecutionReport& r) { maker_rep = r; });
    dispatcher.on_execution(2, [&](const ExecutionReport& r) { taker_rep = r; });

    OrderBook& book = registry.book(INST);
    CHECK(registry.has(INST));

    place(book, e.make(INST, 1, Side::Sell, OrdType::Limit, 101, 5));            // maker p1 rests ask
    auto trades = book.submit(e.make(INST, 2, Side::Buy, OrdType::Limit, 101, 5)); // taker p2 crosses
    CHECK_EQ(trades.size(), 1u);
    dispatcher.dispatch_all(trades);

    CHECK(maker_rep.has_value() && taker_rep.has_value());

    // maker (p1) sold 5 @ 101 -> short 5, flagged Maker
    CHECK(maker_rep->liquidity == Liquidity::Maker);
    CHECK(maker_rep->side == Side::Sell);
    CHECK_EQ(maker_rep->qty, 5);
    CHECK_EQ(maker_rep->price, 101);
    CHECK_EQ(maker_rep->running_position, -5);
    CHECK_EQ(maker_rep->fees, 0.0);

    // taker (p2) bought 5 @ 101 -> long 5, flagged Taker
    CHECK(taker_rep->liquidity == Liquidity::Taker);
    CHECK(taker_rep->side == Side::Buy);
    CHECK_EQ(taker_rep->running_position, 5);

    CHECK_EQ(dispatcher.position(1, INST), -5);
    CHECK_EQ(dispatcher.position(2, INST), 5);
}

int main() {
    test_price_level_primitives();
    test_resting_and_surface();
    test_full_cross();
    test_partial_maker_remainder_rests();
    test_taker_remainder_rests();
    test_multi_level_sweep();
    test_fifo_priority_within_level();
    test_ioc_residual_discarded();
    test_ioc_unmarketable();
    test_reject_market_and_fok();
    test_cancel();
    test_conservation_mixed();
    test_determinism();
    test_end_to_end_dispatch();

    std::cout << (g_checks - g_failures) << '/' << g_checks << " checks passed\n";
    if (g_failures != 0) {
        std::cerr << g_failures << " FAILED\n";
        return 1;
    }
    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
