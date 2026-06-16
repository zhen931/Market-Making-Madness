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
}