// SPDX-License-Identifier: MIT
#pragma once

#include <FEXCore/Utils/CompilerDefs.h>

#include <xxhash.h>

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>

namespace FEXCore::DiskCache {

struct __attribute__((packed)) BlobFixedHeader {
  uint32_t GuestSize;
  uint32_t HostSize;
  uint32_t EntryPointCount;
  uint32_t SmallRelocCount;
  uint32_t ThunkRelocCount;
  uint32_t TouchedGuestPagesCount;
  XXH128_hash_t GuestHash;
};

// Packed struct for types 0, 2 and 3. Type 1 is bigger and separate below.
struct __attribute__((packed)) BlobSmallRelocation {
  uint32_t Offset;
  uint8_t Type;
  union {
    struct __attribute__((packed)) {
      uint32_t Symbol;
    } Named;
    struct __attribute__((packed)) {
      uint64_t GuestRIP;
    } RIPLiteral;
    struct __attribute__((packed)) {
      uint8_t RegisterIndex;
      uint64_t GuestRIP;
    } RIPMove;
  };
};

// Type 1, implicit.
struct __attribute__((packed)) BlobThunkRelocation {
  uint32_t Offset;
  uint8_t RegisterIndex;
  uint8_t SymbolHash[32]; // sha256sum in the real RelocNamedThunkMove
};

static_assert(sizeof(BlobFixedHeader) == 40, "Breaking change in disk-cache blob header layout");
static_assert(sizeof(BlobSmallRelocation) == 14, "Breaking change in disk-cache small-relocation layout");
static_assert(sizeof(BlobThunkRelocation) == 37, "Breaking change in disk-cache thunk-relocation layout");

} // namespace FEXCore::DiskCache

namespace FEXCore::DiskCacheFile {

struct Layout {
  DiskCache::BlobFixedHeader Header;
  size_t HostCodeOffset;
  size_t GuestPagesOffset;
  size_t EntryPointRIPsOffset;
  size_t EntryPointHostOffsetsOffset;
  size_t SmallRelocationsOffset;
  size_t ThunkRelocationsOffset;
  size_t RequiredSize;
};

enum class ValidationError {
  None,
  TruncatedHeader,
  InvalidHostCodeSize,
  TruncatedHostCode,
  TruncatedGuestPages,
  TruncatedEntryPointRIPs,
  TruncatedEntryPointHostOffsets,
  HostEntryPointOutOfRange,
  MissingPrimaryEntryPoint,
  TruncatedSmallRelocations,
  TruncatedThunkRelocations,
};

struct ValidationResult {
  std::optional<Layout> Parsed;
  ValidationError Error;
};

FEX_DEFAULT_VISIBILITY ValidationResult Validate(std::span<const std::byte> Blob);
FEX_DEFAULT_VISIBILITY std::string_view ToString(ValidationError Error);

} // namespace FEXCore::DiskCacheFile
