# Tier 1 order-book matching engine â build + test.
# Repo standard: g++ 13, C++20, ASan/UBSan-clean.

CXX      ?= g++
CPPFLAGS ?= -I.
CXXFLAGS ?= -std=c++20 -Wall -Wextra -Wpedantic -g -O1 -fno-omit-frame-pointer
SAN      ?= -fsanitize=address,undefined

SRC := order_book.cpp tests/test_order_book.cpp
HDR := order_book.hpp instrument_registry.hpp fill_dispatcher.hpp participant.hpp
BIN := build/test_order_book

DEMO_SRC := order_book.cpp demo/connect_demo.cpp
DEMO_BIN := build/connect_demo

.PHONY: all test demo clean

all: test demo

$(BIN): $(SRC) $(HDR)
	@mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(SAN) $(SRC) -o $(BIN)

test: $(BIN)
	ASAN_OPTIONS=detect_leaks=1 UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1 ./$(BIN)

$(DEMO_BIN): $(DEMO_SRC) $(HDR)
	@mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(SAN) $(DEMO_SRC) -o $(DEMO_BIN)

demo: $(DEMO_BIN)
	ASAN_OPTIONS=detect_leaks=1 UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1 ./$(DEMO_BIN)

clean:
	rm -rf build
