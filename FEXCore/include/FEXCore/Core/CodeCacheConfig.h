// SPDX-License-Identifier: MIT
#pragma once

#include <FEXCore/Utils/CompilerDefs.h>

#include <cstdint>
#include <string_view>

namespace FEXCore {

struct HostFeatures;

namespace CodeCacheConfig {
  /**
   * Computes the versioned identity of all inputs that may change generated
   * host code for a whole-file code cache.
   *
   * SerializedConfig is Config::SerializeForCache(). HostFeaturesHash is the
   * effective HostFeatures::HashForCaching() value after overrides.
   */
  FEX_DEFAULT_VISIBILITY uint64_t ComputeId(std::string_view SerializedConfig, uint64_t HostFeaturesHash, bool Is64BitMode);
  FEX_DEFAULT_VISIBILITY uint64_t ComputeId(const HostFeatures& HostFeatures, bool Is64BitMode);
} // namespace CodeCacheConfig

} // namespace FEXCore
