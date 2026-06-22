// connect_demo.cpp â proves a bot can actually CONNECT to the venue and trade.
//
// Wires the Tier 1 pieces into the flow from ORDER_BOOK_TIER1.md Â§2:
//
//   Participant (bot) --OrderRequest--> Gateway --(stamp id/ts, Â§8)--> OrderBook
//        ^                                  |                              |
//        |                                  |                        vector<Trade>
//   on_market_data / on_execution           v                              |
//        +----------- FillDispatcher <--- dispatch_all <--------------------+
//
// The DES kernel + 25 msg/s token bucket are out of Tier 1 scope; this Gateway is
// a minimal synchronous stand-in so the participant seam can be exercised for real.

#include "order_book.hpp"
#include "instrument_registry.hpp"
#include "fill_dispatcher.hpp"
#include "participant.hpp"

#include <iostream>
#include <vector>

using namespace clob;

static int g_fail = 0;
#define EXPECT(cond, msg)                                                      \
    do {                                                                       \
        if (cond) {                                                            \
            std::cout << "  [ok]   " << msg << "\n";                           \
        } else {                                                               \
            ++g_fail;                                                          \
            std::cout << "  [FAIL] " << msg << "\n";                           \
        }                                                                      \
    } while (0)

static const char* side_str(Side s) { return s == Side::Buy ? "BUY" : "SELL"; }
static const char* liq_str(Liquidity l) { return l == Liquidity::Maker ? "MAKER" : "TAKER"; }

// ---- Gateway: id/ts stamping (Â§8) + routing (Â§11.2) + market-data fan-out -----
// (Latency + the 25 msg/s token bucket are deferred to Tier 2.)
class Gateway {
public:
    Gateway(OrderPool& pool, InstrumentRegistry& reg, FillDispatcher& disp)
        : pool_(pool), reg_(reg), disp_(disp) {}

    void connect(Participant* p) {
        participants_.push_back(p);
        // Route this participant's fills back to it through the dispatcher.
        disp_.on_execution(p->id(), [p](const ExecutionReport& r) { p->on_execution(r); });
        std::cout << "Participant " << p->id() << " connected.\n";
    }

    OrderId submit(ParticipantId who, const OrderRequest& req) {
        Order* o      = pool_.allocate();          // gateway allocates from the shared pool
        OrderId id    = ++next_id_;
        o->id         = id;
        o->instrument = req.instrument;
        o->owner      = who;
        o->side       = req.side;
        o->type       = req.type;
        o->price      = req.price;
        o->qty_open   = req.qty;
        o->qty_orig   = req.qty;
        o->ts_accept  = ++clock_;                  // stamps time priority

        auto trades = reg_.book(req.instrument).submit(o);  // book takes ownership
        disp_.dispatch_all(trades);                          // fan fills to both sides
        broadcast(req.instrument);                           // refresh everyone's view
        return id;
    }

    bool cancel(ParticipantId who, InstrumentId inst, OrderId id) {
        bool ok = reg_.book(inst).cancel(id);
        for (auto* p : participants_) {
            if (p->id() == who) { p->on_cancel_ack(id, ok); }
        }
        broadcast(inst);
        return ok;
    }

private:
    void broadcast(InstrumentId inst) {
        OrderBook& b = reg_.book(inst);
        MarketSnapshot s;
        s.instrument = inst;
        s.ts         = clock_;
        if (b.has_bids()) { s.has_bid = true; s.best_bid = b.best_bid(); s.bid_qty = b.qty_at(Side::Buy, b.best_bid()); }
        if (b.has_asks()) { s.has_ask = true; s.best_ask = b.best_ask(); s.ask_qty = b.qty_at(Side::Sell, b.best_ask()); }
        for (auto* p : participants_) { p->on_market_data(s); }
    }

    OrderPool&                pool_;
    InstrumentRegistry&       reg_;
    FillDispatcher&           disp_;
    std::vector<Participant*> participants_;
    OrderId                   next_id_ = 0;
    Timestamp                 clock_   = 0;
};

// ---- A market-making bot: rests a two-sided quote and tracks its fills --------
class MakerBot : public Participant {
public:
    MakerBot(ParticipantId id, InstrumentId inst, Gateway& gw) : id_(id), inst_(inst), gw_(gw) {}

    ParticipantId id() const override { return id_; }

    // ACT: post a two-sided quote around a mid (bots emit to the gateway, Â§11.5).
    void quote(Price bid_px, Quantity bid_qty, Price ask_px, Quantity ask_qty) {
        bid_id_ = gw_.submit(id_, {inst_, Side::Buy,  OrdType::Limit, bid_px, bid_qty});
        ask_id_ = gw_.submit(id_, {inst_, Side::Sell, OrdType::Limit, ask_px, ask_qty});
        std::cout << "MakerBot " << id_ << " quoted  bid " << bid_qty << "@" << bid_px
                  << "  /  ask " << ask_qty << "@" << ask_px << "\n";
    }

    void on_market_data(const MarketSnapshot& s) override { last_ = s; }  // passive: cache top-of-book

    void on_execution(const ExecutionReport& r) override {
        position_ = r.running_position;
        std::cout << "MakerBot " << id_ << " filled " << side_str(r.side) << ' ' << r.qty
                  << "@" << r.price << "  [" << liq_str(r.liquidity)
                  << "]  pos=" << position_ << "\n";
    }

    void on_cancel_ack(OrderId id, bool ok) override {
        std::cout << "MakerBot " << id_ << " cancel-ack id=" << id << "  ok=" << (ok ? "true" : "false") << "\n";
    }

    Quantity position() const { return position_; }
    OrderId  bid_id() const { return bid_id_; }
    OrderId  ask_id() const { return ask_id_; }

private:
    ParticipantId  id_;
    InstrumentId   inst_;
    Gateway&       gw_;
    MarketSnapshot last_{};
    Quantity       position_ = 0;
    OrderId        bid_id_ = 0, ask_id_ = 0;
};

// ---- A liquidity-taking bot: lifts the offer it sees ------------------------
class TakerBot : public Participant {
public:
    TakerBot(ParticipantId id, InstrumentId inst, Gateway& gw) : id_(id), inst_(inst), gw_(gw) {}

    ParticipantId id() const override { return id_; }

    void on_market_data(const MarketSnapshot& s) override { last_ = s; }

    // ACT: cross the spread for `qty` at the best ask we last saw.
    void lift(Quantity qty) {
        if (!last_.has_ask) { std::cout << "TakerBot " << id_ << " sees no ask; nothing to lift.\n"; return; }
        std::cout << "TakerBot " << id_ << " lifting " << qty << "@" << last_.best_ask << " (IOC)\n";
        gw_.submit(id_, {inst_, Side::Buy, OrdType::IOC, last_.best_ask, qty});
    }

    void on_execution(const ExecutionReport& r) override {
        position_ = r.running_position;
        std::cout << "TakerBot " << id_ << " filled " << side_str(r.side) << ' ' << r.qty
                  << "@" << r.price << "  [" << liq_str(r.liquidity)
                  << "]  pos=" << position_ << "\n";
    }

    void on_cancel_ack(OrderId, bool) override {}

    Quantity position() const { return position_; }

private:
    ParticipantId  id_;
    InstrumentId   inst_;
    Gateway&       gw_;
    MarketSnapshot last_{};
    Quantity       position_ = 0;
};

int main() {
    constexpr InstrumentId INST = 42;

    OrderPool          pool(256);
    InstrumentRegistry registry(pool);
    FillDispatcher     dispatcher;
    Gateway            gateway(pool, registry, dispatcher);

    std::cout << "=== Bot connectivity demo (instrument " << INST << ") ===\n\n";

    MakerBot maker(1, INST, gateway);
    TakerBot taker(2, INST, gateway);
    gateway.connect(&maker);
    gateway.connect(&taker);

    std::cout << "\n-- Maker posts a two-sided quote --\n";
    maker.quote(/*bid*/ 100, 5, /*ask*/ 101, 5);

    OrderBook& book = registry.book(INST);
    std::cout << "Book top: bid " << book.qty_at(Side::Buy, 100) << "@" << book.best_bid()
              << "  ask " << book.qty_at(Side::Sell, 101) << "@" << book.best_ask() << "\n";

    std::cout << "\n-- Taker lifts 3 of the offer --\n";
    taker.lift(3);

    std::cout << "\n-- Maker cancels its residual ask --\n";
    gateway.cancel(maker.id(), INST, maker.ask_id());

    std::cout << "\n-- Results --\n";
    EXPECT(maker.position() == -3, "maker is short 3 after being lifted");
    EXPECT(taker.position() == 3,  "taker is long 3 after lifting");
    EXPECT(dispatcher.position(1, INST) == -3 && dispatcher.position(2, INST) == 3,
           "dispatcher position book agrees");
    EXPECT(book.qty_at(Side::Sell, 101) == 0, "maker's ask residual was cancelled");
    EXPECT(book.qty_at(Side::Buy, 100) == 5,  "maker's bid is still resting");
    EXPECT(book.has_bids() && !book.has_asks(), "book shows bid-only after the trade");

    std::cout << "\n" << (g_fail == 0 ? "BOT CONNECTED AND TRADED OK" : "DEMO FAILED") << "\n";
    return g_fail == 0 ? 0 : 1;
}
