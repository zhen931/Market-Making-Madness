#include "order_book.hpp"

// Non-template definitions for the Tier 1 matching engine. The template matcher
// (OrderBook::match_against) lives in the header; everything that does not need to
// be visible at every call site is defined here.

namespace clob {

// ---- OrderPool --------------------------------------------------------------
// deque arena + LIFO free list. `reserve` slots are pre-created and parked on the
// free list so a warmed-up pool has a stable baseline free_count() (Â§13).
OrderPool::OrderPool(std::size_t reserve) {
    free_.reserve(reserve);
    for (std::size_t i = 0; i < reserve; ++i) {
        arena_.emplace_back();
        free_.push_back(&arena_.back());
    }
}

Order* OrderPool::allocate() {
    if (!free_.empty()) {
        Order* o = free_.back();
        free_.pop_back();
        *o = Order{};               // clear stale ids / links / level back-pointer
        return o;
    }
    arena_.emplace_back();          // grow; deque keeps existing Order* stable
    return &arena_.back();
}

void OrderPool::deallocate(Order* o) {
    free_.push_back(o);             // recycle the slot (LIFO)
}

// ---- OrderBook: order entry -------------------------------------------------
std::vector<Trade> OrderBook::submit(Order* o) {
    // Market / FOK are out of scope in Tier 1 (Â§4/Â§9): reject by discarding the
    // order and returning no trades. The book takes ownership either way.
    if (o->type == OrdType::Market || o->type == OrdType::FOK) {
        pool_.deallocate(o);        // TODO(tier2): Market / FOK matching
        return {};
    }

    // A buy crosses the asks; a sell crosses the bids. Pick the opposing map at the
    // call site (the two maps are different types â see Â§9 GOTCHA).
    std::vector<Trade> trades = (o->side == Side::Buy)
        ? match_against(o, asks_)
        : match_against(o, bids_);

    if (o->qty_open > 0 && o->type == OrdType::Limit) {
        rest(o);                    // book retains ownership of the remainder
    } else {
        pool_.deallocate(o);        // IOC remainder discarded, or fully-filled aggressor
    }
    return trades;
}

void OrderBook::rest(Order* o) {
    PriceLevel& lvl = level_for_insert(o->side, o->price);
    lvl.push_back(o);               // sets o->level, total_qty += qty_open, ++order_count
    index_[o->id] = o;
}

// ---- OrderBook: cancel ------------------------------------------------------
bool OrderBook::cancel(OrderId id) {
    auto it = index_.find(id);
    if (it == index_.end()) { return false; }   // unknown or already filled

    Order*      o    = it->second;
    Side        side = o->side;
    PriceLevel* lvl  = o->level;                 // stable map-node ptr
    Price       px   = lvl->price;

    lvl->unlink(o);                              // total_qty -= o->qty_open, --order_count
    if (lvl->empty()) {                          // drop the now-empty level
        erase_level(side, px);
    }
    index_.erase(it);
    pool_.deallocate(o);
    return true;
}

// ---- OrderBook: level helpers ----------------------------------------------
PriceLevel& OrderBook::level_for_insert(Side side, Price price) {
    if (side == Side::Buy) {
        auto [it, inserted] = bids_.try_emplace(price);
        if (inserted) { it->second.price = price; }
        return it->second;
    }
    auto [it, inserted] = asks_.try_emplace(price);
    if (inserted) { it->second.price = price; }
    return it->second;
}

void OrderBook::erase_level(Side side, Price price) noexcept {
    if (side == Side::Buy) { bids_.erase(price); }
    else                   { asks_.erase(price); }
}

// ---- OrderBook: read-only market-data surface -------------------------------
Price OrderBook::best_bid() const noexcept { return bids_.begin()->first; }  // greatest, via greater<>
Price OrderBook::best_ask() const noexcept { return asks_.begin()->first; }  // least

const PriceLevel* OrderBook::best_bid_level() const noexcept {
    return bids_.empty() ? nullptr : &bids_.begin()->second;
}
const PriceLevel* OrderBook::best_ask_level() const noexcept {
    return asks_.empty() ? nullptr : &asks_.begin()->second;
}

Quantity OrderBook::qty_at(Side side, Price price) const noexcept {
    if (side == Side::Buy) {
        auto it = bids_.find(price);
        return it == bids_.end() ? 0 : it->second.total_qty;
    }
    auto it = asks_.find(price);
    return it == asks_.end() ? 0 : it->second.total_qty;
}

std::vector<std::pair<Price, Quantity>> OrderBook::depth(Side side, std::size_t n) const {
    std::vector<std::pair<Price, Quantity>> out;
    out.reserve(n);
    if (side == Side::Buy) {
        for (const auto& [px, lvl] : bids_) {
            if (out.size() >= n) { break; }
            out.emplace_back(px, lvl.total_qty);
        }
    } else {
        for (const auto& [px, lvl] : asks_) {
            if (out.size() >= n) { break; }
            out.emplace_back(px, lvl.total_qty);
        }
    }
    return out;
}

std::size_t OrderBook::level_count(Side side) const noexcept {
    return side == Side::Buy ? bids_.size() : asks_.size();
}

const Order* OrderBook::find(OrderId id) const {
    auto it = index_.find(id);
    return it == index_.end() ? nullptr : it->second;
}

}  // namespace clob
