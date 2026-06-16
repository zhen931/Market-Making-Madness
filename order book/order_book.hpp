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
}