#pragma once

#include "order_book.hpp"

#include <map>

// InstrumentRegistry (ORDER_BOOK_TIER1.md Â§11.1): owns one OrderBook per
// InstrumentId. Submit/cancel events carry an InstrumentId; the DES kernel routes
// to `registry.book(id)`. The OrderPool is shared across every book (Â§8: the
// gateway/kernel allocates Orders from this pool, the books deallocate back into
// it), so the whole venue stays pool-conserved (Â§13).
namespace clob {

class InstrumentRegistry {
public:
    explicit InstrumentRegistry(OrderPool& pool) : pool_(pool) {}

    // Find-or-create the book for `id`. Reference is stable (std::map nodes do not
    // move), so callers may cache it for the life of the registry.
    OrderBook& book(InstrumentId id) {
        auto it = books_.find(id);
        if (it == books_.end()) {
            // OrderBook holds an OrderPool& (non-movable); construct it in place.
            it = books_.try_emplace(id, id, pool_).first;
        }
        return it->second;
    }

    [[nodiscard]] bool has(InstrumentId id) const { return books_.count(id) != 0; }
    [[nodiscard]] std::size_t size() const noexcept { return books_.size(); }

private:
    OrderPool&                      pool_;
    std::map<InstrumentId, OrderBook> books_;  // node-stable: OrderBook is non-movable
};

}  // namespace clob
