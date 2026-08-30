// SPDX-License-Identifier: MIT

#include <FEXCore/Config/Config.h>
#include <FEXCore/Core/CodeCacheConfig.h>
#include <FEXCore/Core/HostFeatures.h>

#include <catch2/catch_test_macros.hpp>

namespace {
struct ConfigScope {
  ConfigScope() {
    FEXCore::Config::Initialize();
  }

  ~ConfigScope() {
    FEXCore::Config::Shutdown();
  }
};

uint64_t CurrentId(uint64_t HostFeaturesHash = 0x1234'5678'9abc'def0ULL, bool Is64BitMode = true) {
  return FEXCore::CodeCacheConfig::ComputeId(FEXCore::Config::SerializeForCache(), HostFeaturesHash, Is64BitMode);
}

bool RejectsWithoutMutation(std::string_view Snapshot) {
  const auto Before = FEXCore::Config::SerializeForCache();
  return !FEXCore::Config::ApplySerializedForCache(Snapshot) && FEXCore::Config::SerializeForCache() == Before;
}
} // namespace

TEST_CASE("CodeCacheConfig - identical inputs are stable and dimensions are separated") {
  const std::string_view SerializedConfig {"MaxInst\0" "1\0" "5000\0", 15};
  const auto Baseline = FEXCore::CodeCacheConfig::ComputeId(SerializedConfig, 0x1234, true);

  REQUIRE(Baseline == FEXCore::CodeCacheConfig::ComputeId(SerializedConfig, 0x1234, true));
  REQUIRE(Baseline != FEXCore::CodeCacheConfig::ComputeId(SerializedConfig, 0x1234, false));
  REQUIRE(Baseline != FEXCore::CodeCacheConfig::ComputeId(SerializedConfig, 0x1235, true));
  REQUIRE(Baseline != FEXCore::CodeCacheConfig::ComputeId(std::string_view {"different"}, 0x1234, true));
}

TEST_CASE("CodeCacheConfig - generated code configuration changes identity") {
  ConfigScope Scope;
  const auto Baseline = CurrentId();

  const std::pair<FEXCore::Config::ConfigOption, std::string_view> CodegenChanges[] = {
    {FEXCore::Config::CONFIG_MAXINST, "17"},
    {FEXCore::Config::CONFIG_MULTIBLOCK, "0"},
    {FEXCore::Config::CONFIG_TSOENABLED, "0"},
    {FEXCore::Config::CONFIG_X87REDUCEDPRECISION, "1"},
    {FEXCore::Config::CONFIG_EXTENDEDVOLATILEMETADATA, "module;0x10-0x20"},
  };

  for (const auto& [Option, Value] : CodegenChanges) {
    FEXCore::Config::Set(Option, Value);
    REQUIRE(CurrentId() != Baseline);
    FEXCore::Config::Erase(Option);
    REQUIRE(CurrentId() == Baseline);
  }
}

TEST_CASE("CodeCacheConfig - runtime-only configuration does not fragment identity") {
  ConfigScope Scope;
  const auto Baseline = CurrentId();

  FEXCore::Config::Set(FEXCore::Config::CONFIG_ENABLELAZYCODECACHINGWIP, "1");
  REQUIRE(CurrentId() == Baseline);
}

TEST_CASE("CodeCacheConfig - effective host features own host override differences") {
  ConfigScope Scope;
  const auto Serialized = FEXCore::Config::SerializeForCache();

  // HOSTFEATURES is a string-enum and is deliberately not duplicated in the
  // serialized scalar configuration. Its effective result is represented by
  // HostFeatures::HashForCaching().
  FEXCore::Config::Set(FEXCore::Config::CONFIG_HOSTFEATURES, "disablesve");
  REQUIRE(FEXCore::Config::SerializeForCache() == Serialized);
  REQUIRE(CurrentId(0x1) != CurrentId(0x2));
}

TEST_CASE("CodeCacheConfig - canonical snapshots round-trip atomically") {
  ConfigScope Scope;
  FEXCore::Config::Set(FEXCore::Config::CONFIG_MAXINST, "17");
  FEXCore::Config::Set(FEXCore::Config::CONFIG_EXTENDEDVOLATILEMETADATA, "module;0x10-0x20");
  const auto Snapshot = FEXCore::Config::SerializeForCache();
  REQUIRE(Snapshot.size() < FEXCore::Config::MAX_SERIALIZED_CACHE_CONFIG_SIZE);

  FEXCore::Config::Set(FEXCore::Config::CONFIG_MAXINST, "99");
  FEXCore::Config::Set(FEXCore::Config::CONFIG_EXTENDEDVOLATILEMETADATA, "different");
  REQUIRE(FEXCore::Config::ApplySerializedForCache(Snapshot));
  REQUIRE(FEXCore::Config::SerializeForCache() == Snapshot);
}

TEST_CASE("CodeCacheConfig - malformed snapshots are rejected without partial application") {
  ConfigScope Scope;
  const auto Snapshot = FEXCore::Config::SerializeForCache();

  auto Truncated = Snapshot;
  Truncated.pop_back();
  REQUIRE(RejectsWithoutMutation(Truncated));

  auto UnknownKey = Snapshot;
  UnknownKey.front() ^= 1;
  REQUIRE(RejectsWithoutMutation(UnknownKey));

  auto WrongOption = Snapshot;
  const auto FirstKeyEnd = WrongOption.find('\0');
  REQUIRE(FirstKeyEnd != fextl::string::npos);
  WrongOption.at(FirstKeyEnd + 1) = '1';
  REQUIRE(RejectsWithoutMutation(WrongOption));

  auto NoncanonicalBool = Snapshot;
  const auto FirstOptionEnd = NoncanonicalBool.find('\0', FirstKeyEnd + 1);
  REQUIRE(FirstOptionEnd != fextl::string::npos);
  NoncanonicalBool.replace(FirstOptionEnd + 1, 4, "TRUE");
  REQUIRE(RejectsWithoutMutation(NoncanonicalBool));

  const auto FirstTripleEnd = Snapshot.find('\0', FirstOptionEnd + 1);
  REQUIRE(FirstTripleEnd != fextl::string::npos);
  fextl::string DuplicateFirst {Snapshot.data(), FirstTripleEnd + 1};
  DuplicateFirst.append(Snapshot);
  REQUIRE(RejectsWithoutMutation(DuplicateFirst));

  const fextl::string MissingFirst {Snapshot.data() + FirstTripleEnd + 1, Snapshot.size() - FirstTripleEnd - 1};
  REQUIRE(RejectsWithoutMutation(MissingFirst));

  auto Extra = Snapshot;
  Extra.append("extra\0", 6);
  REQUIRE(RejectsWithoutMutation(Extra));

  fextl::string Oversized(FEXCore::Config::MAX_SERIALIZED_CACHE_CONFIG_SIZE + 1, 'x');
  REQUIRE(RejectsWithoutMutation(Oversized));
}

TEST_CASE("CodeCacheConfig - effective host feature state is reconstructible") {
  FEXCore::HostFeatures Features {};
  constexpr uint64_t Expected = 0x1234'5678'9abc'def0ULL;
  Features.ApplyCacheHash(Expected);
  REQUIRE(Features.HashForCaching() == Expected);
}
