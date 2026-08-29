// SPDX-License-Identifier: MIT

#include "Interface/Core/LookupCache.h"

#include <catch2/catch_test_macros.hpp>

TEST_CASE("LookupCache - synthetic entries require exact invalidation") {
  constexpr uint64_t Entrypoint = 0x1234'5678'9000ULL;
  auto HostCode = reinterpret_cast<void*>(0x4000ULL);

  FEXCore::GuestToHostMap Map;
  {
    auto lk = Map.AcquireWriteLock();
    Map.AddBlockMapping(Entrypoint, {}, HostCode, lk);
  }

  // A CustomIR entry has no decoded guest pages, so range invalidation cannot
  // discover it through the ordinary CodePages reverse index.
  Map.InvalidateRange(Entrypoint, 1);
  {
    auto lk = Map.AcquireReadLock();
    REQUIRE(Map.FindBlock(Entrypoint, lk) != nullptr);
  }

  REQUIRE(Map.InvalidateExactEntry(Entrypoint));
  {
    auto lk = Map.AcquireReadLock();
    REQUIRE(Map.FindBlock(Entrypoint, lk) == nullptr);
  }
}
