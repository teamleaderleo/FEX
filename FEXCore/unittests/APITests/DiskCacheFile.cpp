// SPDX-License-Identifier: MIT

#include <FEXCore/Core/DiskCacheFile.h>

#include <catch2/catch_test_macros.hpp>

#include <cstring>
#include <limits>
#include <vector>

namespace {

template<typename T>
void Write(std::vector<std::byte>& Data, size_t Offset, const T& Value) {
  REQUIRE(Offset <= Data.size());
  REQUIRE(sizeof(Value) <= Data.size() - Offset);
  std::memcpy(Data.data() + Offset, &Value, sizeof(Value));
}

struct Fixture {
  std::vector<std::byte> Data;
  size_t HostCodeOffset;
  size_t GuestPagesOffset;
  size_t EntryPointRIPsOffset;
  size_t EntryPointHostOffsetsOffset;
  size_t SmallRelocationsOffset;
  size_t ThunkRelocationsOffset;
  size_t RequiredSize;
};

Fixture MakeFixture(uint32_t GuestPages = 2, uint32_t EntryPoints = 2, uint32_t SmallRelocations = 1,
                    uint32_t ThunkRelocations = 1, size_t GuestCodeTail = 0) {
  FEXCore::DiskCache::BlobFixedHeader Header {
    .GuestSize = static_cast<uint32_t>(GuestCodeTail),
    .HostSize = 32,
    .EntryPointCount = EntryPoints,
    .SmallRelocCount = SmallRelocations,
    .ThunkRelocCount = ThunkRelocations,
    .TouchedGuestPagesCount = GuestPages,
    .GuestHash = {},
  };

  Fixture Result;
  Result.HostCodeOffset = sizeof(Header);
  Result.GuestPagesOffset = Result.HostCodeOffset + Header.HostSize;
  Result.EntryPointRIPsOffset = Result.GuestPagesOffset + GuestPages * sizeof(uint64_t);
  Result.EntryPointHostOffsetsOffset = Result.EntryPointRIPsOffset + EntryPoints * sizeof(uint64_t);
  Result.SmallRelocationsOffset = Result.EntryPointHostOffsetsOffset + EntryPoints * sizeof(uint32_t);
  Result.ThunkRelocationsOffset = Result.SmallRelocationsOffset + SmallRelocations * sizeof(FEXCore::DiskCache::BlobSmallRelocation);
  Result.RequiredSize = Result.ThunkRelocationsOffset + ThunkRelocations * sizeof(FEXCore::DiskCache::BlobThunkRelocation);
  Result.Data.resize(Result.RequiredSize + GuestCodeTail);
  Write(Result.Data, 0, Header);

  if (EntryPoints != 0) {
    Write(Result.Data, Result.EntryPointRIPsOffset, uint64_t {0});
    Write(Result.Data, Result.EntryPointHostOffsetsOffset, uint32_t {0});
    for (uint32_t Index = 1; Index < EntryPoints; ++Index) {
      Write(Result.Data, Result.EntryPointRIPsOffset + Index * sizeof(uint64_t), uint64_t {Index * 4});
      Write(Result.Data, Result.EntryPointHostOffsetsOffset + Index * sizeof(uint32_t), uint32_t {Index * 4});
    }
  }
  return Result;
}

FEXCore::DiskCacheFile::ValidationResult Validate(const std::vector<std::byte>& Data) {
  return FEXCore::DiskCacheFile::Validate(Data);
}

void RequireError(const std::vector<std::byte>& Data, FEXCore::DiskCacheFile::ValidationError Error) {
  const auto Result = Validate(Data);
  REQUIRE_FALSE(Result.Parsed);
  REQUIRE(Result.Error == Error);
  REQUIRE(FEXCore::DiskCacheFile::ToString(Result.Error) != "unknown validation error");
}

} // namespace

TEST_CASE("DiskCacheFile - format-3 block layout is mapped exactly") {
  const auto Fixture = MakeFixture(2, 2, 1, 1, 19);
  const auto Result = Validate(Fixture.Data);
  REQUIRE(Result.Parsed);
  REQUIRE(Result.Error == FEXCore::DiskCacheFile::ValidationError::None);
  REQUIRE(Result.Parsed->HostCodeOffset == Fixture.HostCodeOffset);
  REQUIRE(Result.Parsed->GuestPagesOffset == Fixture.GuestPagesOffset);
  REQUIRE(Result.Parsed->EntryPointRIPsOffset == Fixture.EntryPointRIPsOffset);
  REQUIRE(Result.Parsed->EntryPointHostOffsetsOffset == Fixture.EntryPointHostOffsetsOffset);
  REQUIRE(Result.Parsed->SmallRelocationsOffset == Fixture.SmallRelocationsOffset);
  REQUIRE(Result.Parsed->ThunkRelocationsOffset == Fixture.ThunkRelocationsOffset);
  REQUIRE(Result.Parsed->RequiredSize == Fixture.RequiredSize);
  REQUIRE(Result.Parsed->RequiredSize < Fixture.Data.size());
}

TEST_CASE("DiskCacheFile - every required-layout prefix is rejected") {
  const auto Fixture = MakeFixture();
  for (size_t Size = 0; Size < Fixture.RequiredSize; ++Size) {
    INFO("prefix size " << Size);
    const auto Result = FEXCore::DiskCacheFile::Validate(std::span {Fixture.Data}.first(Size));
    REQUIRE_FALSE(Result.Parsed);
  }
  REQUIRE(Validate(Fixture.Data).Parsed);
}

TEST_CASE("DiskCacheFile - disk-controlled count arithmetic cannot wrap") {
  auto Fixture = MakeFixture(0, 0, 0, 0);
  FEXCore::DiskCache::BlobFixedHeader Header;
  std::memcpy(&Header, Fixture.Data.data(), sizeof(Header));
  Fixture.Data.resize(sizeof(Header));

  Header.HostSize = 0;
  Header.EntryPointCount = 0x4000'0000;
  Write(Fixture.Data, 0, Header);
  RequireError(Fixture.Data, FEXCore::DiskCacheFile::ValidationError::TruncatedEntryPointRIPs);

  Header.EntryPointCount = 0;
  Header.TouchedGuestPagesCount = std::numeric_limits<uint32_t>::max();
  Write(Fixture.Data, 0, Header);
  RequireError(Fixture.Data, FEXCore::DiskCacheFile::ValidationError::TruncatedGuestPages);

  Header.TouchedGuestPagesCount = 0;
  Header.HostSize = 16;
  Header.EntryPointCount = 1;
  Header.SmallRelocCount = std::numeric_limits<uint32_t>::max();
  Fixture.Data.resize(sizeof(Header) + Header.HostSize + sizeof(uint64_t) + sizeof(uint32_t));
  Write(Fixture.Data, 0, Header);
  Write(Fixture.Data, sizeof(Header) + Header.HostSize, uint64_t {0});
  Write(Fixture.Data, sizeof(Header) + Header.HostSize + sizeof(uint64_t), uint32_t {0});
  RequireError(Fixture.Data, FEXCore::DiskCacheFile::ValidationError::TruncatedSmallRelocations);

  Header.SmallRelocCount = 0;
  Header.ThunkRelocCount = std::numeric_limits<uint32_t>::max();
  Write(Fixture.Data, 0, Header);
  RequireError(Fixture.Data, FEXCore::DiskCacheFile::ValidationError::TruncatedThunkRelocations);
}

TEST_CASE("DiskCacheFile - host code and primary entrypoint are bounded") {
  auto Fixture = MakeFixture();
  auto Header = FEXCore::DiskCache::BlobFixedHeader {};
  std::memcpy(&Header, Fixture.Data.data(), sizeof(Header));

  Header.HostSize = 31;
  Write(Fixture.Data, 0, Header);
  RequireError(Fixture.Data, FEXCore::DiskCacheFile::ValidationError::InvalidHostCodeSize);

  Fixture = MakeFixture();
  Write(Fixture.Data, Fixture.EntryPointHostOffsetsOffset, uint32_t {32});
  RequireError(Fixture.Data, FEXCore::DiskCacheFile::ValidationError::HostEntryPointOutOfRange);

  Fixture = MakeFixture();
  Write(Fixture.Data, Fixture.EntryPointRIPsOffset, uint64_t {4});
  RequireError(Fixture.Data, FEXCore::DiskCacheFile::ValidationError::MissingPrimaryEntryPoint);
}
