// SPDX-License-Identifier: MIT

#include "FEXCore/Config/Config.h"
#include "FEXCore/Core/CodeCacheConfig.h"
#include "FEXCore/Core/HostFeatures.h"
#include "FEXCore/fextl/vector.h"
#include "FEXCore/Utils/TypeDefines.h"

#include <array>
#include <cstring>
#include <xxhash.h>

namespace FEXCore::CodeCacheConfig {

uint64_t ComputeId(std::string_view SerializedConfig, uint64_t HostFeaturesHash, bool Is64BitMode) {
  struct FEX_PACKED IdentityHeader {
    std::array<char, 8> Domain = {'F', 'X', 'C', 'C', 'I', 'D', '\0', '\0'};
    uint8_t Version = 1;
    uint8_t Is64BitMode;
    std::array<uint8_t, 6> Reserved {};
    uint64_t HostFeaturesHash;
  };
  static_assert(sizeof(IdentityHeader) == 24);

  const IdentityHeader Header {
    .Is64BitMode = Is64BitMode,
    .HostFeaturesHash = HostFeaturesHash,
  };
  fextl::vector<uint8_t> Bytes(sizeof(Header) + SerializedConfig.size());
  memcpy(Bytes.data(), &Header, sizeof(Header));
  memcpy(Bytes.data() + sizeof(Header), SerializedConfig.data(), SerializedConfig.size());
  return XXH3_64bits(Bytes.data(), Bytes.size());
}

uint64_t ComputeId(const HostFeatures& HostFeatures, bool Is64BitMode) {
  return ComputeId(Config::SerializeForCache(), HostFeatures.HashForCaching(), Is64BitMode);
}

} // namespace FEXCore::CodeCacheConfig
