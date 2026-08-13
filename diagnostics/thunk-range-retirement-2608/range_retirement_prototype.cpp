// Diagnostic-only prototype for FEX-2608 thunk bridge retirement.
//
// This is intentionally not wired into the FEX build. It models the narrow
// source experiment proposed for the Vulkan guest-thunk unload failure:
//   * native PFN -> guest thunk target registrations are long-lived bridges;
//   * retiring a guest address range tombstones bridges targeting that range;
//   * a later registration of the same native PFN can replace the tombstone;
//   * unrelated bridges remain live;
//   * conflicting live registrations are not silently rebound.
//
// This prototype does NOT claim to solve host->guest callback lifetime or the
// check-to-unmap race. Those are deliberately separate scope decisions.

#include <cassert>
#include <cstdint>
#include <iostream>
#include <limits>
#include <optional>
#include <unordered_map>
#include <vector>

namespace {

enum class DispatchKind {
  Missing,
  Live,
  Retired,
};

struct DispatchResult {
  DispatchKind Kind {DispatchKind::Missing};
  uintptr_t GuestTarget {};
};

struct LinkResult {
  enum class Kind {
    Inserted,
    Idempotent,
    Revived,
    LiveConflict,
  } Kind;
  uintptr_t ExistingTarget {};
};

struct BridgeEntry {
  enum class State {
    Live,
    Retired,
  } State {State::Live};

  uintptr_t GuestTarget {};
  uint64_t Generation {1};
};

class BridgeRegistry final {
public:
  LinkResult Link(uintptr_t NativePFN, uintptr_t GuestTarget) {
    assert(NativePFN != 0);
    assert(GuestTarget != 0);

    auto [It, Inserted] = Entries.emplace(NativePFN, BridgeEntry {
      .State = BridgeEntry::State::Live,
      .GuestTarget = GuestTarget,
      .Generation = 1,
    });

    if (Inserted) {
      return {LinkResult::Kind::Inserted, 0};
    }

    auto& Existing = It->second;
    if (Existing.State == BridgeEntry::State::Retired) {
      Existing.State = BridgeEntry::State::Live;
      Existing.GuestTarget = GuestTarget;
      ++Existing.Generation;
      return {LinkResult::Kind::Revived, 0};
    }

    if (Existing.GuestTarget == GuestTarget) {
      return {LinkResult::Kind::Idempotent, Existing.GuestTarget};
    }

    // Narrow diagnostic semantics: keep the currently-live registration.
    // A production design needs richer alias/owner identity before it can
    // safely choose between two live guest invokers for one native PFN.
    return {LinkResult::Kind::LiveConflict, Existing.GuestTarget};
  }

  std::vector<uintptr_t> RetireGuestRange(uintptr_t Start, uintptr_t Length) {
    std::vector<uintptr_t> NativePFNsToInvalidate;
    if (Length == 0) {
      return NativePFNsToInvalidate;
    }

    const auto Max = std::numeric_limits<uintptr_t>::max();
    const uintptr_t End = Length > Max - Start ? Max : Start + Length;

    for (auto& [NativePFN, Entry] : Entries) {
      if (Entry.State != BridgeEntry::State::Live) {
        continue;
      }

      if (Entry.GuestTarget >= Start && Entry.GuestTarget < End) {
        Entry.State = BridgeEntry::State::Retired;
        Entry.GuestTarget = 0;
        ++Entry.Generation;
        NativePFNsToInvalidate.push_back(NativePFN);
      }
    }

    return NativePFNsToInvalidate;
  }

  DispatchResult Dispatch(uintptr_t NativePFN) const {
    const auto It = Entries.find(NativePFN);
    if (It == Entries.end()) {
      return {DispatchKind::Missing, 0};
    }

    if (It->second.State == BridgeEntry::State::Retired) {
      return {DispatchKind::Retired, 0};
    }

    return {DispatchKind::Live, It->second.GuestTarget};
  }

  uint64_t Generation(uintptr_t NativePFN) const {
    return Entries.at(NativePFN).Generation;
  }

private:
  std::unordered_map<uintptr_t, BridgeEntry> Entries;
};

void TestBasicRetirement() {
  BridgeRegistry R;
  constexpr uintptr_t H = 0x7000'1000;
  constexpr uintptr_t T = 0x7fff'f100'4200;

  assert(R.Link(H, T).Kind == LinkResult::Kind::Inserted);
  assert(R.Dispatch(H).Kind == DispatchKind::Live);

  const auto Invalidated = R.RetireGuestRange(0x7fff'f100'0000, 0x10000);
  assert(Invalidated.size() == 1);
  assert(Invalidated[0] == H);
  assert(R.Dispatch(H).Kind == DispatchKind::Retired);
}

void TestReloadSameNativePFN() {
  BridgeRegistry R;
  constexpr uintptr_t H = 0x7000'2000;
  constexpr uintptr_t OldT = 0x7fff'f200'1000;
  constexpr uintptr_t NewT = 0x7fff'e900'9000;

  assert(R.Link(H, OldT).Kind == LinkResult::Kind::Inserted);
  const auto G1 = R.Generation(H);
  R.RetireGuestRange(0x7fff'f200'0000, 0x20000);
  const auto G2 = R.Generation(H);
  assert(G2 > G1);
  assert(R.Dispatch(H).Kind == DispatchKind::Retired);

  assert(R.Link(H, NewT).Kind == LinkResult::Kind::Revived);
  const auto Result = R.Dispatch(H);
  assert(Result.Kind == DispatchKind::Live);
  assert(Result.GuestTarget == NewT);
  assert(R.Generation(H) > G2);
}

void TestDifferentGuestBase() {
  BridgeRegistry R;
  constexpr uintptr_t H = 0x7000'3000;
  constexpr uintptr_t FirstBase = 0x7fff'f300'0000;
  constexpr uintptr_t SecondBase = 0x7fff'e300'0000;

  assert(R.Link(H, FirstBase + 0x4b1f0).Kind == LinkResult::Kind::Inserted);
  R.RetireGuestRange(FirstBase, 0x60000);
  assert(R.Link(H, SecondBase + 0x4b1f0).Kind == LinkResult::Kind::Revived);
  assert(R.Dispatch(H).GuestTarget == SecondBase + 0x4b1f0);
}

void TestUnrelatedBridgeSurvives() {
  BridgeRegistry R;
  constexpr uintptr_t H1 = 0x7000'4000;
  constexpr uintptr_t H2 = 0x7000'5000;
  constexpr uintptr_t T1 = 0x7fff'f400'1000;
  constexpr uintptr_t T2 = 0x7fff'a000'1000;

  R.Link(H1, T1);
  R.Link(H2, T2);
  const auto Invalidated = R.RetireGuestRange(0x7fff'f400'0000, 0x20000);

  assert(Invalidated.size() == 1);
  assert(R.Dispatch(H1).Kind == DispatchKind::Retired);
  assert(R.Dispatch(H2).Kind == DispatchKind::Live);
  assert(R.Dispatch(H2).GuestTarget == T2);
}

void TestLiveAliasConflictIsExplicit() {
  BridgeRegistry R;
  constexpr uintptr_t H = 0x7000'6000;
  constexpr uintptr_t TA = 0x7fff'f600'1000;
  constexpr uintptr_t TB = 0x7fff'f700'1000;

  assert(R.Link(H, TA).Kind == LinkResult::Kind::Inserted);
  const auto Conflict = R.Link(H, TB);
  assert(Conflict.Kind == LinkResult::Kind::LiveConflict);
  assert(Conflict.ExistingTarget == TA);
  assert(R.Dispatch(H).GuestTarget == TA);
}

void TestZeroLengthDoesNothing() {
  BridgeRegistry R;
  constexpr uintptr_t H = 0x7000'7000;
  constexpr uintptr_t T = 0x7fff'f700'7000;
  R.Link(H, T);

  assert(R.RetireGuestRange(T, 0).empty());
  assert(R.Dispatch(H).Kind == DispatchKind::Live);
}

} // namespace

int main() {
  TestBasicRetirement();
  TestReloadSameNativePFN();
  TestDifferentGuestBase();
  TestUnrelatedBridgeSurvives();
  TestLiveAliasConflictIsExplicit();
  TestZeroLengthDoesNothing();

  std::cout << "range-retirement prototype: 6/6 PASS\n";
  return 0;
}
