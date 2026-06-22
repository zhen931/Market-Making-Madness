#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <unordered_map>
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

// ---- OrderBook: one instrument's CLOB ---------------------------------------
class OrderBook {
public:
    explicit OrderBook(InstrumentId instrument, std::size_t pool_reserve = 0);
 
    // --- Resting-order mutations ---------------------------------------------
 
    // Insert a non-marketable limit remainder as a resting order. The book
    // copies the business fields of `proto`, takes a pooled slot, wires the
    // intrusive links / level / index, and returns a stable pointer to the
    // resting Order.
    // Preconditions: the order does not cross the book (marketability is the
    // matching layer's job), proto.qty_open > 0, and proto.id is unique among
    // resting orders. proto's prev/next/level fields are ignored.
    Order* add_resting(const Order& proto);
 
    // Cancel a resting order by id. O(1) splice + index erase (+ level removal
    // if it became empty). Returns false if no such resting order exists.
    bool cancel(OrderId id);
 
    // Modify a resting order's price and/or open quantity. Priority rule:
    //   * pure quantity DECREASE at the same price -> keeps queue position;
    //   * quantity INCREASE, or any price change    -> loses time priority and
    //     is re-queued at the TAIL of the target price level.
    // `new_qty` is the desired new OPEN/remaining quantity. Returns a pointer to
    // the (same, possibly relocated) Order, or nullptr if not found / new_qty<=0.
    // Structural only: does NOT check whether new_price crosses the book - the
    // matching layer must re-evaluate marketability after a modify.
    Order* modify(OrderId id, Price new_price, Quantity new_qty);
 
    // --- Best bid/offer (O(1)) -----------------------------------------------
    [[nodiscard]] bool  has_bids() const noexcept { return !bids_.empty(); }
    [[nodiscard]] bool  has_asks() const noexcept { return !asks_.empty(); }
    [[nodiscard]] Price best_bid() const noexcept;  // precondition: has_bids()
    [[nodiscard]] Price best_ask() const noexcept;  // precondition: has_asks()
    [[nodiscard]] const PriceLevel* best_bid_level() const noexcept;  // null if empty
    [[nodiscard]] const PriceLevel* best_ask_level() const noexcept;  // null if empty
 
    // --- Depth / analytics ----------------------------------------------------
    [[nodiscard]] Quantity    volume_at(Side side, Price price) const;  // 0 if no level
    [[nodiscard]] std::size_t level_count(Side side) const noexcept;
 
    // --- Lookup ---------------------------------------------------------------
    [[nodiscard]] const Order* find(OrderId id) const;  // nullptr if absent
 
    [[nodiscard]] InstrumentId instrument() const noexcept { return instrument_; }
 
private:
    // Bids: highest price first. Asks: lowest price first. begin() == the touch.
    using BidMap = std::map<Price, PriceLevel, std::greater<Price>>;
    using AskMap = std::map<Price, PriceLevel, std::less<Price>>;
 
    PriceLevel& level_for_insert(Side side, Price price);  // get-or-create
    void        erase_level(Side side, Price price) noexcept;
 
    InstrumentId                        instrument_;
    BidMap                              bids_;
    AskMap                              asks_;
    std::unordered_map<OrderId, Order*> index_;  // id -> resting Order* (O(1))
    OrderPool                           pool_;
};

}