// SPDX-License-Identifier: MIT

#include <FEXCore/Core/CodeCacheFile.h>
#include <FEXCore/Utils/MathUtils.h>

#include <Interface/Core/JIT/Relocations.h>

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstring>
#include <limits>
#include <utility>
#include <vector>

namespace {

constexpr std::array<uint8_t, 20> FEXVersion = {
  0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09,
  0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10, 0x11, 0x12, 0x13,
};
constexpr uint64_t ConfigId = 0x0123'4567'89ab'cdefULL;

template<typename T>
void Append(std::vector<std::byte>& Data, const T& Value) {
  const size_t Offset = Data.size();
  Data.resize(Offset + sizeof(Value));
  std::memcpy(Data.data() + Offset, &Value, sizeof(Value));
}

template<typename T>
void Write(std::vector<std::byte>& Data, size_t Offset, const T& Value) {
  REQUIRE(Offset <= Data.size());
  REQUIRE(sizeof(Value) <= Data.size() - Offset);
  std::memcpy(Data.data() + Offset, &Value, sizeof(Value));
}

struct Fixture {
  std::vector<std::byte> Data;
  size_t BlockOffset;
  size_t RelocationsOffset;
  size_t CodePagesOffset;
};

Fixture MakeFixture(std::vector<uint64_t> RelocationOffsets = {}, std::vector<uint64_t> BlockPages = {0x1000},
                    std::vector<std::pair<uint64_t, std::vector<uint64_t>>> CodePages = {{0x1000, {0x10}}}) {
  FEXCore::CodeCacheFile::Header Header;
  Header.FEXVersion = FEXVersion;
  Header.ConfigId = ConfigId;
  Header.NumBlocks = 1;
  Header.NumCodePages = CodePages.size();
  Header.CodeBufferSize = FEXCore::Utils::FEX_PAGE_SIZE;
  Header.NumRelocations = RelocationOffsets.size();

  Fixture Result;
  Result.Data.resize(sizeof(Header));
  Result.BlockOffset = Result.Data.size();
  Append(Result.Data, uint64_t {0x10});
  Append(Result.Data, uint64_t {0x20});
  Append(Result.Data, uint64_t {BlockPages.size()});
  for (uint64_t Page : BlockPages) {
    Append(Result.Data, Page);
  }

  Result.RelocationsOffset = Result.Data.size();
  for (uint64_t Offset : RelocationOffsets) {
    auto Relocation = FEXCore::CPU::Relocation::Default();
    Relocation.Header.Offset = Offset;
    Relocation.Header.Type = FEXCore::CPU::RelocationTypes::RELOC_NAMED_SYMBOL_LITERAL;
    Append(Result.Data, Relocation);
  }

  Result.Data.resize(FEXCore::AlignUp(Result.Data.size(), FEXCore::Utils::FEX_PAGE_SIZE));
  Result.Data.resize(Result.Data.size() + Header.CodeBufferSize);
  Result.CodePagesOffset = Result.Data.size();
  for (const auto& [Page, Entrypoints] : CodePages) {
    Append(Result.Data, Page);
    Append(Result.Data, uint64_t {Entrypoints.size()});
    for (uint64_t Entrypoint : Entrypoints) {
      Append(Result.Data, Entrypoint);
    }
  }
  Write(Result.Data, 0, Header);
  return Result;
}

FEXCore::CodeCacheFile::ValidationResult Validate(const std::vector<std::byte>& Data) {
  return FEXCore::CodeCacheFile::Validate(Data, FEXVersion, ConfigId);
}

void RequireError(const std::vector<std::byte>& Data, FEXCore::CodeCacheFile::ValidationError Error) {
  const auto Result = Validate(Data);
  REQUIRE_FALSE(Result.Parsed);
  REQUIRE(Result.Error == Error);
  REQUIRE(FEXCore::CodeCacheFile::ToString(Result.Error) != "unknown validation error");
}

} // namespace

TEST_CASE("CodeCacheFile - minimal format-3 layout is mapped exactly") {
  const auto Fixture = MakeFixture({0x20});
  const auto Result = Validate(Fixture.Data);
  REQUIRE(Result.Parsed);
  REQUIRE(Result.Error == FEXCore::CodeCacheFile::ValidationError::None);
  REQUIRE(Result.Parsed->BlockListOffset == Fixture.BlockOffset);
  REQUIRE(Result.Parsed->RelocationsOffset == Fixture.RelocationsOffset);
  REQUIRE(Result.Parsed->CodePagesOffset == Fixture.CodePagesOffset);
  REQUIRE(Result.Parsed->CodeBufferOffset % FEXCore::Utils::FEX_PAGE_SIZE == 0);
  REQUIRE(Result.Parsed->FileHeader.NumBlocks == 1);
  REQUIRE(Result.Parsed->FileHeader.NumRelocations == 1);
  REQUIRE(Result.Parsed->FileHeader.NumCodePages == 1);
}

TEST_CASE("CodeCacheFile - every strict valid-file prefix is rejected") {
  const auto Fixture = MakeFixture({0x20});
  for (size_t Size = 0; Size < Fixture.Data.size(); ++Size) {
    INFO("prefix size " << Size);
    const auto Result = FEXCore::CodeCacheFile::Validate(std::span {Fixture.Data}.first(Size), FEXVersion, ConfigId);
    REQUIRE_FALSE(Result.Parsed);
  }
}

TEST_CASE("CodeCacheFile - header identity and fixed dimensions fail closed") {
  const auto Fixture = MakeFixture();
  auto Header = FEXCore::CodeCacheFile::Header {};
  std::memcpy(&Header, Fixture.Data.data(), sizeof(Header));

  auto Changed = Fixture.Data;
  Header.Magic[0] ^= 1;
  Write(Changed, 0, Header);
  RequireError(Changed, FEXCore::CodeCacheFile::ValidationError::InvalidMagic);

  Header.Magic = Header.ExpectedMagic;
  Header.FormatVersion = 2;
  Write(Changed, 0, Header);
  RequireError(Changed, FEXCore::CodeCacheFile::ValidationError::UnsupportedFormat);

  Header.FormatVersion = Header.ExpectedFormatVersion;
  Header.FEXVersion[0] ^= 1;
  Write(Changed, 0, Header);
  RequireError(Changed, FEXCore::CodeCacheFile::ValidationError::FEXVersionMismatch);

  Header.FEXVersion = FEXVersion;
  Header.ConfigId ^= 1;
  Write(Changed, 0, Header);
  RequireError(Changed, FEXCore::CodeCacheFile::ValidationError::ConfigMismatch);

  Header.ConfigId = ConfigId;
  Header.NumBlocks = 0;
  Write(Changed, 0, Header);
  RequireError(Changed, FEXCore::CodeCacheFile::ValidationError::EmptyBlockList);

  Header.NumBlocks = 1;
  Header.CodeBufferSize = 1;
  Write(Changed, 0, Header);
  RequireError(Changed, FEXCore::CodeCacheFile::ValidationError::InvalidCodeBufferSize);
}

TEST_CASE("CodeCacheFile - block and code-page indexes are structural") {
  auto Fixture = MakeFixture({}, {0x2000, 0x1000});
  RequireError(Fixture.Data, FEXCore::CodeCacheFile::ValidationError::UnsortedBlockCodePages);

  Fixture = MakeFixture();
  Write(Fixture.Data, Fixture.BlockOffset + sizeof(uint64_t), uint64_t {FEXCore::Utils::FEX_PAGE_SIZE});
  RequireError(Fixture.Data, FEXCore::CodeCacheFile::ValidationError::HostCodeOutOfRange);

  Fixture = MakeFixture({}, {0x1000}, {{0x2000, {0x20}}, {0x1000, {0x10}}});
  RequireError(Fixture.Data, FEXCore::CodeCacheFile::ValidationError::UnsortedCodePages);

  Fixture = MakeFixture();
  Fixture.Data.push_back(std::byte {1});
  RequireError(Fixture.Data, FEXCore::CodeCacheFile::ValidationError::TrailingData);

  Fixture = MakeFixture();
  Fixture.Data.resize(FEXCore::AlignUp(Fixture.Data.size(), FEXCore::Utils::FEX_PAGE_SIZE));
  REQUIRE(Validate(Fixture.Data).Parsed);
}

TEST_CASE("CodeCacheFile - relocation type order and write width are bounded") {
  auto Fixture = MakeFixture({0x20, 0x10});
  RequireError(Fixture.Data, FEXCore::CodeCacheFile::ValidationError::UnsortedRelocations);

  Fixture = MakeFixture({FEXCore::Utils::FEX_PAGE_SIZE - 4});
  RequireError(Fixture.Data, FEXCore::CodeCacheFile::ValidationError::RelocationOutOfRange);

  Fixture = MakeFixture({0x20});
  const auto InvalidType = static_cast<FEXCore::CPU::RelocationTypes>(std::numeric_limits<uint32_t>::max());
  Write(Fixture.Data, Fixture.RelocationsOffset + offsetof(FEXCore::CPU::RelocationHeader, Type), InvalidType);
  RequireError(Fixture.Data, FEXCore::CodeCacheFile::ValidationError::InvalidRelocationType);
}

TEST_CASE("CodeCacheFile - relocation storage must be naturally aligned") {
  const auto Fixture = MakeFixture({0x20});
  std::vector<std::byte> Misaligned(Fixture.Data.size() + 1);
  std::ranges::copy(Fixture.Data, Misaligned.begin() + 1);
  const auto Result = FEXCore::CodeCacheFile::Validate(std::span {Misaligned}.subspan(1), FEXVersion, ConfigId);
  REQUIRE_FALSE(Result.Parsed);
  REQUIRE(Result.Error == FEXCore::CodeCacheFile::ValidationError::MisalignedRelocations);
}
