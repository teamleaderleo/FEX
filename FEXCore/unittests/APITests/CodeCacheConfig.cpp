// SPDX-License-Identifier: MIT

#include <FEXCore/Config/Config.h>
#include <FEXCore/Core/CodeCache.h>

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
