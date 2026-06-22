#pragma once

#include "order_book.hpp"

#include <functional>
#include <unordered_map>

// FillDispatcher (ORDER_BOOK_TIER1.md Â§11.4) â the layer that satisfies design
// decision #2. For each Trade from OrderBook::submit it produces TWO
// ExecutionReports (one for the maker, one for the taker), stamping the fields the
// book cannot know: the maker/taker liquidity flag, the fee from the fee model,
// and each participant's running position AFTER the fill. It then invokes each
// participant's registered std::function callback.
namespace clob {

class FillDispatcher {
public:
    using ExecCallback = std::function<void(const ExecutionReport&)>;
    // fee_model(liquidity, price, qty) -> fee for that side (maker/taker bps).
    using FeeModel     = std::function<double(Liquidity, Price, Quantity)>;

    explicit FillDispatcher(FeeModel fee_model = default_fee_model())
        : fee_model_(std::move(fee_model)) {}

    // Register (or replace) a participant's execution callback.
    void on_execution(ParticipantId p, ExecCallback cb) {
        on_exec_[p] = std::move(cb);
    }

    // Fan a single Trade out to both sides.
    void dispatch(const Trade& t) {
        emit(t.maker, t.maker_order_id, t.maker_side, t, Liquidity::Maker);
        emit(t.taker, t.taker_order_id, t.taker_side, t, Liquidity::Taker);
    }

    // Convenience: dispatch a whole batch returned by OrderBook::submit.
    void dispatch_all(const std::vector<Trade>& trades) {
        for (const Trade& t : trades) { dispatch(t); }
    }

    // This participant's net position in `instrument` (signed; long > 0).
    [[nodiscard]] Quantity position(ParticipantId p, InstrumentId instrument) const {
        auto pit = positions_.find(p);
        if (pit == positions_.end()) { return 0; }
        auto iit = pit->second.find(instrument);
        return iit == pit->second.end() ? 0 : iit->second;
    }

    // Default: flat, fee-free. Real venues plug in a bps schedule here.
    static FeeModel default_fee_model() {
        return [](Liquidity, Price, Quantity) { return 0.0; };  // TODO(tier2): maker/taker bps
    }

private:
    void emit(ParticipantId who, OrderId order_id, Side side, const Trade& t, Liquidity liq) {
        // Apply the fill to the running position first, then report it.
        Quantity& pos = positions_[who][t.instrument];
        pos += (side == Side::Buy) ? t.qty : -t.qty;

        auto cb = on_exec_.find(who);
        if (cb == on_exec_.end()) { return; }   // participant not listening; position still tracked

        cb->second(ExecutionReport{
            order_id,
            t.instrument,
            side,
            t.price,
            t.qty,
            liq,
            fee_model_(liq, t.price, t.qty),
            pos,
            t.ts,
        });
    }

    std::unordered_map<ParticipantId, ExecCallback> on_exec_;
    std::unordered_map<ParticipantId,
                       std::unordered_map<InstrumentId, Quantity>> positions_;
    FeeModel fee_model_;
};

}  // namespace clob
