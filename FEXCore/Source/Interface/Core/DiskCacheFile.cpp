// SPDX-License-Identifier: MIT

#include <FEXCore/Core/DiskCacheFile.h>

#include <cstring>
#include <type_traits>

namespace FEXCore::DiskCacheFile {
namespace {

template<typename T>
T Read(std::span<const std::byte> Data, size_t Offset) {
  static_assert(std::is_trivially_copyable_v<T>);
  T Result;
  std::memcpy(&Result, Data.data() + Offset, sizeof(Result));
  return Result;
}

bool Consume(std::span<const std::byte> Data, size_t& Offset, size_t Size) {
  if (Offset > Data.size_bytes() || Size > Data.size_bytes() - Offset) {
    return false;
  }
  Offset += Size;
  return true;
}

bool ConsumeArray(std::span<const std::byte> Data, size_t& Offset, uint32_t Count, size_t ElementSize) {
  if (Offset > Data.size_bytes() || Count > (Data.size_bytes() - Offset) / ElementSize) {
    return false;
  }
  return Consume(Data, Offset, static_cast<size_t>(Count) * ElementSize);
}

ValidationResult Failure(ValidationError Error) {
  return {.Parsed = std::nullopt, .Error = Error};
}

} // namespace

ValidationResult Validate(std::span<const std::byte> Blob) {
  if (Blob.size_bytes() < sizeof(DiskCache::BlobFixedHeader)) {
    return Failure(ValidationError::TruncatedHeader);
  }

  const auto Header = Read<DiskCache::BlobFixedHeader>(Blob, 0);
  if (Header.HostSize % 16 != 0) {
    // The ARM64 JIT's cached-code loader requires 16-byte-sized code.
    return Failure(ValidationError::InvalidHostCodeSize);
  }

  size_t Offset = sizeof(Header);
  const size_t HostCodeOffset = Offset;
  if (!Consume(Blob, Offset, Header.HostSize)) {
    return Failure(ValidationError::TruncatedHostCode);
  }

  const size_t GuestPagesOffset = Offset;
  if (!ConsumeArray(Blob, Offset, Header.TouchedGuestPagesCount, sizeof(uint64_t))) {
    return Failure(ValidationError::TruncatedGuestPages);
  }

  const size_t EntryPointRIPsOffset = Offset;
  if (!ConsumeArray(Blob, Offset, Header.EntryPointCount, sizeof(uint64_t))) {
    return Failure(ValidationError::TruncatedEntryPointRIPs);
  }

  const size_t EntryPointHostOffsetsOffset = Offset;
  if (!ConsumeArray(Blob, Offset, Header.EntryPointCount, sizeof(uint32_t))) {
    return Failure(ValidationError::TruncatedEntryPointHostOffsets);
  }

  bool FoundPrimaryEntryPoint = false;
  for (uint32_t Index = 0; Index < Header.EntryPointCount; ++Index) {
    const uint64_t GuestOffset = Read<uint64_t>(Blob, EntryPointRIPsOffset + Index * sizeof(uint64_t));
    const uint32_t HostOffset = Read<uint32_t>(Blob, EntryPointHostOffsetsOffset + Index * sizeof(uint32_t));
    FoundPrimaryEntryPoint |= GuestOffset == 0;
    if (HostOffset >= Header.HostSize) {
      return Failure(ValidationError::HostEntryPointOutOfRange);
    }
  }
  if (!FoundPrimaryEntryPoint) {
    return Failure(ValidationError::MissingPrimaryEntryPoint);
  }

  const size_t SmallRelocationsOffset = Offset;
  if (!ConsumeArray(Blob, Offset, Header.SmallRelocCount, sizeof(DiskCache::BlobSmallRelocation))) {
    return Failure(ValidationError::TruncatedSmallRelocations);
  }

  const size_t ThunkRelocationsOffset = Offset;
  if (!ConsumeArray(Blob, Offset, Header.ThunkRelocCount, sizeof(DiskCache::BlobThunkRelocation))) {
    return Failure(ValidationError::TruncatedThunkRelocations);
  }

  return {
    .Parsed = Layout {
      .Header = Header,
      .HostCodeOffset = HostCodeOffset,
      .GuestPagesOffset = GuestPagesOffset,
      .EntryPointRIPsOffset = EntryPointRIPsOffset,
      .EntryPointHostOffsetsOffset = EntryPointHostOffsetsOffset,
      .SmallRelocationsOffset = SmallRelocationsOffset,
      .ThunkRelocationsOffset = ThunkRelocationsOffset,
      .RequiredSize = Offset,
    },
    .Error = ValidationError::None,
  };
}

std::string_view ToString(ValidationError Error) {
  switch (Error) {
  case ValidationError::None: return "none";
  case ValidationError::TruncatedHeader: return "truncated header";
  case ValidationError::InvalidHostCodeSize: return "invalid host-code size";
  case ValidationError::TruncatedHostCode: return "truncated host code";
  case ValidationError::TruncatedGuestPages: return "truncated guest-page list";
  case ValidationError::TruncatedEntryPointRIPs: return "truncated entrypoint RIP list";
  case ValidationError::TruncatedEntryPointHostOffsets: return "truncated entrypoint host-offset list";
  case ValidationError::HostEntryPointOutOfRange: return "entrypoint host offset out of range";
  case ValidationError::MissingPrimaryEntryPoint: return "missing primary entrypoint";
  case ValidationError::TruncatedSmallRelocations: return "truncated small-relocation list";
  case ValidationError::TruncatedThunkRelocations: return "truncated thunk-relocation list";
  }
  return "unknown validation error";
}

} // namespace FEXCore::DiskCacheFile
