#pragma once

#include "order_book.hpp"

// Participant seam (ORDER_BOOK_TIER1.md Â§11.5). A bot never calls OrderBook
// directly â it CONNECTS to the gateway, receives market data + execution
// reports through these virtual callbacks, and ACTS by emitting order requests to
// the gateway. This mirrors the Python quoting-risk-engine contracts.py seams:
//   MarketSnapshot  <-> BookTop / MarketState   (market-data in)
//   OrderRequest    <-> Quote / HedgeOrder       (orders out)
//   ExecutionReport <-> fill callbacks           (fills in)
namespace clob {

// Top-of-book pushed to participants. Maps 1:1 to the Python `BookTop`.
struct MarketSnapshot {
    InstrumentId instrument = 0;
    bool         has_bid    = false;
    Price        best_bid   = 0;
    Quantity     bid_qty    = 0;
    bool         has_ask    = false;
    Price        best_ask   = 0;
    Quantity     ask_qty    = 0;
    Timestamp    ts         = 0;
};

// An order intent a bot hands to the gateway (which stamps id/ts and submits).
// Maps to the Python `Quote` / `HedgeOrder` outputs -> SubmitOrder events.
struct OrderRequest {
    InstrumentId instrument = 0;
    Side         side       = Side::Buy;
    OrdType      type       = OrdType::Limit;
    Price        price      = 0;
    Quantity     qty        = 0;
};

class Participant {
public:
    virtual ~Participant() = default;
    [[nodiscard]] virtual ParticipantId id() const = 0;

    // Pushed by the gateway whenever the visible market changes.
    virtual void on_market_data(const MarketSnapshot&) = 0;
    // Pushed by the FillDispatcher when this participant is filled.
    virtual void on_execution(const ExecutionReport&) = 0;
    // Pushed by the gateway in response to a cancel request.
    virtual void on_cancel_ack(OrderId, bool ok) = 0;
};

}  // namespace clob
