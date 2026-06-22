#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <unordered_map>
#include <utility>
#include <vector>

// Central limit order book
namespace clob {
using OrderId = std::uint64_t;
using InstrumentId = std::uint32_t;
using ParticipantId = std::uint32_t;
using Price = std::int64_t; // smallest amount a price is allowed to move
using Quantity = std::int64_t; // signed; eases partial-fill arithmetic
using Timestamp = std::int64_t; // simulated nanoseconds since session start

enum class Side : std::uint8_t { Buy, Sell };

// OrdType is set at order entry and consumed by the matching layer.
// A resting order is a Limit that did not (fully) cross, so
// no structural primitive in this file reads this field - it is carried here so
// the shared Order record does not have to be reopened later. 
enum class OrdType : std::uint8_t { Limit, Market, IOC, FOK };

struct PriceLevel;  
// Order: an intrusive FIFO node
struct Order {
    OrderId id = 0;
    InstrumentId instrument = 0;
    ParticipantId owner = 0;
    Side side = Side::Buy;
    OrdType type = OrdType::Limit; 
    Price price = 0; // ticks (ignored for Market)
    Quantity qty_open = 0; // live remaining quantity
    Quantity qty_orig = 0; // original submitted quantity
    Timestamp ts_accept = 0; // for time priority / tie-break

    // Intrusive FIFO links + level back-pointer. Owned and maintained by
    // PriceLevel / OrderBook; callers must not touch these directly.
    Order* prev  = nullptr;
    Order* next  = nullptr;
    PriceLevel* level = nullptr;
};

// FIFO queue of orders resting at one price
struct PriceLevel {
    Price price = 0;
    Quantity total_qty = 0;       
    std::size_t order_count = 0;
    Order* head = nullptr; // oldest - front of FIFO (time priority)
    Order* tail = nullptr; // newest - back of FIFO
 
    [[nodiscard]] bool empty() const noexcept { return head == nullptr; }
 
    // Append to tail (newest). O(1)
    void push_back(Order* o) noexcept {
        o->next = nullptr; // nothing behind new order
        o->prev = tail; // last order is in front of new order

        if (tail) {
            tail->next = o; // tail points to new order
        }
        else {
            head = o; // if queue empty then new order is head
        }

        tail = o;
        o->level = this; // o joined PriceLevel queue
        total_qty += o->qty_open; // add o to running price total
        ++order_count;
    }

    // Remove an arbitrary node from the FIFO: relink neighbours, fix head/tail,
    // total_qty -= o->qty_open (its live remaining qty AT CALL TIME), --order_count.
    // O(1). The matcher zeroes o->qty_open for a fully-consumed maker before
    // calling unlink, so this subtracts 0 in that case; a cancel calls it with
    // the order's live qty. One rule keeps `total_qty == sum(qty_open)` either way.
    void unlink(Order* o) noexcept {
        if (o->prev) { o->prev->next = o->next; } else { head = o->next; }
        if (o->next) { o->next->prev = o->prev; } else { tail = o->prev; }
        o->prev = nullptr;
        o->next = nullptr;
        o->level = nullptr;
        total_qty -= o->qty_open;
        --order_count;
    }

    // Pop the oldest (head) node and return it; == unlink(head). nullptr if empty.
    [[nodiscard]] Order* pop_front() noexcept {
        if (!head) { return nullptr; }
        Order* o = head;
        unlink(o);
        return o;
    }
};

// OrderPool: deque arena + free list 
// Stable Order* (std::deque never relocates existing elements on growth);
// freed slots are recycled via a LIFO free list
class OrderPool {
public:
    explicit OrderPool(std::size_t reserve = 0);
 
    [[nodiscard]] Order* allocate(); // pop a free slot or grow the arena
    void deallocate(Order* o); // return a slot to the free list
 
    [[nodiscard]] std::size_t capacity() const noexcept { return arena_.size(); }
    [[nodiscard]] std::size_t free_count() const noexcept { return free_.size(); }
 
private:
    std::deque<Order> arena_; // owns Order storage; pointer-stable on growth
    std::vector<Order*> free_; // recycled slots
};

// ---- Matcher output & per-participant execution report ----------------------

// Trade: the matcher's primitive output. One per maker/taker fill. Carries BOTH
// identities so the FillDispatcher can fan out to each side without re-deriving.
struct Trade {
    InstrumentId  instrument = 0;
    Price         price = 0;        // the MAKER's resting price (ticks)
    Quantity      qty = 0;          // matched quantity (> 0)
    OrderId       maker_order_id = 0;
    ParticipantId maker = 0;
    Side          maker_side = Side::Buy;   // resting side
    OrderId       taker_order_id = 0;
    ParticipantId taker = 0;
    Side          taker_side = Side::Buy;   // == aggressor side
    Timestamp     ts = 0;
};

enum class Liquidity : std::uint8_t { Maker, Taker };

// ExecutionReport: enriched, per-participant. The book never fills in `fees` or
// `running_position` (it tracks neither); the FillDispatcher stamps those when it
// converts each Trade into one ExecutionReport per side.
struct ExecutionReport {
    OrderId      order_id = 0;          // THIS participant's order
    InstrumentId instrument = 0;
    Side         side = Side::Buy;      // THIS participant's side of the fill
    Price        price = 0;
    Quantity     qty = 0;
    Liquidity    liquidity = Liquidity::Maker;
    double       fees = 0.0;            // from the fee model (stamped at dispatch)
    Quantity     running_position = 0;  // this participant's net position AFTER the fill
    Timestamp    ts = 0;
};

// ---- OrderBook: one instrument's CLOB ---------------------------------------
// Tier 1 matching engine: price-time-priority CLOB for a single instrument.
// `submit` matches the aggressor against the resting book and returns the Trades
// it generated; `cancel` removes a resting order. The book takes OWNERSHIP of any
// Order* handed to submit (see ORDER_BOOK_TIER1.md Â§6): after the call the caller
// must never touch/free/reuse it. The book rests it (Limit remainder) or returns
// it to the pool (fully filled, or IOC remainder). The pool is OWNED EXTERNALLY
// (the gateway/kernel allocates + stamps the Order before submit, Â§8); the book
// only deallocates back into it, keeping the pool conserved (Â§13).
class OrderBook {
public:
    OrderBook(InstrumentId instrument, OrderPool& pool)
        : instrument_(instrument), pool_(pool) {}

    // --- Matching mutations (event-driven; called by the DES kernel) ----------

    // Match `o` against the resting book (price-time priority), rest the unfilled
    // remainder IFF it is a Limit, and return every Trade generated this call.
    // Takes ownership of `o` (Â§6). Trade price is always the maker's resting price.
    // Market/FOK are rejected in Tier 1 (deallocated, empty result).
    [[nodiscard]] std::vector<Trade> submit(Order* o);

    // Cancel a resting order by id. O(1) splice + index erase (+ level removal if
    // it became empty), then the Order is returned to the pool. Returns false if
    // no such resting order exists (unknown id, or already fully filled).
    bool cancel(OrderId id);

    // --- Best bid/offer (O(1)) -----------------------------------------------
    [[nodiscard]] bool  has_bids() const noexcept { return !bids_.empty(); }
    [[nodiscard]] bool  has_asks() const noexcept { return !asks_.empty(); }
    [[nodiscard]] Price best_bid() const noexcept;  // precondition: has_bids()
    [[nodiscard]] Price best_ask() const noexcept;  // precondition: has_asks()
    [[nodiscard]] const PriceLevel* best_bid_level() const noexcept;  // null if empty
    [[nodiscard]] const PriceLevel* best_ask_level() const noexcept;  // null if empty

    // --- Depth / analytics ----------------------------------------------------
    // Resting open quantity at one (side, price); 0 if no such level.
    [[nodiscard]] Quantity    qty_at(Side side, Price price) const noexcept;
    [[nodiscard]] Quantity    volume_at(Side side, Price price) const noexcept {  // alias
        return qty_at(side, price);
    }
    // Up to `n` (price, total_qty) pairs, best-first.
    [[nodiscard]] std::vector<std::pair<Price, Quantity>> depth(Side side, std::size_t n) const;
    [[nodiscard]] std::size_t level_count(Side side) const noexcept;

    // --- Lookup ---------------------------------------------------------------
    [[nodiscard]] const Order* find(OrderId id) const;  // nullptr if absent

    [[nodiscard]] InstrumentId instrument() const noexcept { return instrument_; }

private:
    // Bids: highest price first. Asks: lowest price first. begin() == the touch.
    using BidMap = std::map<Price, PriceLevel, std::greater<Price>>;
    using AskMap = std::map<Price, PriceLevel, std::less<Price>>;

    // GOTCHA (Â§9): bids_ and asks_ are DIFFERENT types (different comparators), so
    // `(side==Buy) ? asks_ : bids_` does not compile. submit picks the opposing
    // map at the call site and passes it into this templated matcher. Defined in
    // the header because it is a template.
    template <class OppMap>
    std::vector<Trade> match_against(Order* o, OppMap& opp);

    PriceLevel& level_for_insert(Side side, Price price);  // get-or-create
    void        erase_level(Side side, Price price) noexcept;
    void        rest(Order* o);  // push_back to own-side level + index it

    InstrumentId                        instrument_;
    OrderPool&                          pool_;   // owned externally (Â§8)
    BidMap                              bids_;
    AskMap                              asks_;
    std::unordered_map<OrderId, Order*> index_;  // id -> resting Order* (O(1))
};

// match_against: price-time priority sweep of the opposing book. The aggressor `o`
// crosses to each marketable maker at the MAKER's price, FIFO within a level.
// Fully consumed makers are unlinked, de-indexed, and returned to the pool here.
template <class OppMap>
std::vector<Trade> OrderBook::match_against(Order* o, OppMap& opp) {
    std::vector<Trade> trades;
    while (o->qty_open > 0 && !opp.empty()) {
        auto it = opp.begin();              // best opposing level
        PriceLevel& lvl = it->second;
        const bool marketable = (o->side == Side::Buy)
            ? lvl.price <= o->price          // ask <= our bid
            : lvl.price >= o->price;         // bid >= our ask
        if (!marketable) { break; }

        while (o->qty_open > 0 && !lvl.empty()) {  // FIFO within the level
            Order*   maker = lvl.head;
            // TODO(tier2): self-trade prevention â if maker->owner == o->owner,
            // skip/cancel per policy instead of crossing. MVP does not consult it.
            Quantity fill  = std::min(o->qty_open, maker->qty_open);

            trades.push_back(Trade{
                instrument_, maker->price, fill,
                maker->id, maker->owner, maker->side,
                o->id,     o->owner,     o->side,
                o->ts_accept,
            });

            o->qty_open     -= fill;
            maker->qty_open -= fill;
            lvl.total_qty   -= fill;          // keep the invariant

            if (maker->qty_open == 0) {       // maker fully filled
                lvl.unlink(maker);            // total_qty -= 0 here (already 0)
                index_.erase(maker->id);
                pool_.deallocate(maker);
            }
        }
        if (lvl.empty()) { opp.erase(it); }   // drop the emptied level
    }
    return trades;
}

}