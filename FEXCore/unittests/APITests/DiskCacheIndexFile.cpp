// SPDX-License-Identifier: MIT

#include <FEXCore/Core/DiskCacheIndexFile.h>

#include <catch2/catch_test_macros.hpp>
#include <fmt/format.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <span>
#include <vector>

namespace {

using namespace FEXCore;

constexpr uint64_t CacheFileSize = 4096;
constexpr size_t RecordSize = sizeof(DiskCache::MesaFOZ::foz_payload_key) + sizeof(DiskCache::MesaFOZ::foz_payload_header) +
                              sizeof(DiskCache::MesaFOZ::mesa_index_db_file_entry);

std::vector<uint8_t> MakeRecord(uint64_t Hash, uint64_t CacheOffset, uint32_t Size = 64) {
  DiskCache::MesaFOZ::foz_payload_key Key {};
  const auto Name = fmt::format("{:016x}", Hash);
  std::memcpy(Key.bytes, Name.data(), Name.size());

  const DiskCache::MesaFOZ::mesa_index_db_file_entry Entry {
    .hash = Hash,
    .size = Size,
    .last_access_time = 0,
    .cache_db_file_offset = CacheOffset,
  };
  const DiskCache::MesaFOZ::foz_payload_header Header {
    .payload_size = sizeof(Entry),
    .format = 1,
    .crc = 0,
    .uncompressed_size = sizeof(Entry),
  };

  std::vector<uint8_t> Result(RecordSize);
  size_t Offset = 0;
  std::memcpy(Result.data() + Offset, &Key, sizeof(Key));
  Offset += sizeof(Key);
  std::memcpy(Result.data() + Offset, &Header, sizeof(Header));
  Offset += sizeof(Header);
  std::memcpy(Result.data() + Offset, &Entry, sizeof(Entry));
  return Result;
}

void Append(std::vector<uint8_t>& Data, std::span<const uint8_t> Suffix) {
  Data.insert(Data.end(), Suffix.begin(), Suffix.end());
}

void Overwrite(std::vector<uint8_t>& Data, size_t Offset, std::span<const uint8_t> Record) {
  if (Data.size() < Offset + Record.size()) {
    Data.resize(Offset + Record.size());
  }
  std::copy(Record.begin(), Record.end(), Data.begin() + Offset);
}

std::vector<uint64_t> Hashes(const DiskCacheIndexFile::ParseResult& Result) {
  std::vector<uint64_t> Hashes;
  for (const auto& Record : Result.Records) {
    if (!Record.Metadata) {
      Hashes.push_back(Record.Hash);
    }
  }
  return Hashes;
}

} // namespace

TEST_CASE("DiskCacheIndexFile - complete records map exactly") {
  auto Data = MakeRecord(0xA, 128);
  Append(Data, MakeRecord(0xB, 256));
  auto Metadata = MakeRecord(~0ULL, 384);
  std::fill_n(Metadata.begin(), sizeof(DiskCache::MesaFOZ::foz_payload_key), 0xFF);
  Append(Data, Metadata);

  const auto Result = DiskCacheIndexFile::Parse(Data, CacheFileSize);
  REQUIRE(Result.State == DiskCacheIndexFile::ParseState::Complete);
  REQUIRE(Result.ValidSize == Data.size());
  REQUIRE(Hashes(Result) == std::vector<uint64_t> {0xA, 0xB});
  REQUIRE(Result.Records[1].Size == 64);
  REQUIRE(Result.Records[1].CacheFileOffset == 256);
  REQUIRE(Result.Records[2].Metadata);
}

TEST_CASE("DiskCacheIndexFile - every strict record prefix preserves the prior boundary") {
  const auto A = MakeRecord(0xA, 128);
  const auto B = MakeRecord(0xB, 256);
  for (size_t Prefix = 1; Prefix < B.size(); ++Prefix) {
    auto Data = A;
    Append(Data, std::span {B}.first(Prefix));
    const auto Result = DiskCacheIndexFile::Parse(Data, CacheFileSize);
    REQUIRE(Result.State == DiskCacheIndexFile::ParseState::IncompleteSuffix);
    REQUIRE(Result.ValidSize == A.size());
    REQUIRE(Hashes(Result) == std::vector<uint64_t> {0xA});
  }
}

TEST_CASE("DiskCacheIndexFile - next append replaces a torn suffix") {
  const auto A = MakeRecord(0xA, 128);
  const auto B = MakeRecord(0xB, 256);
  const auto C = MakeRecord(0xC, 384);

  auto Data = A;
  Append(Data, std::span {B}.first(12));
  const auto Torn = DiskCacheIndexFile::Parse(Data, CacheFileSize);
  REQUIRE(Torn.ValidSize == A.size());

  Overwrite(Data, Torn.ValidSize, C);
  const auto Recovered = DiskCacheIndexFile::Parse(Data, CacheFileSize);
  REQUIRE(Recovered.State == DiskCacheIndexFile::ParseState::Complete);
  REQUIRE(Hashes(Recovered) == std::vector<uint64_t> {0xA, 0xC});
}

TEST_CASE("DiskCacheIndexFile - an already poisoned suffix is salvaged monotonically") {
  const auto A = MakeRecord(0xA, 128);
  const auto B = MakeRecord(0xB, 256);
  const auto C = MakeRecord(0xC, 384);
  const auto D = MakeRecord(0xD, 512);
  const auto E = MakeRecord(0xE, 640);
  const auto F = MakeRecord(0xF, 768);
  const auto G = MakeRecord(0x10, 896);

  auto Data = A;
  Append(Data, std::span {B}.first(12));
  Append(Data, C);
  Append(Data, D);
  const auto Poisoned = DiskCacheIndexFile::Parse(Data, CacheFileSize);
  REQUIRE(Hashes(Poisoned) == std::vector<uint64_t> {0xA});

  Overwrite(Data, Poisoned.ValidSize, E);
  const auto FirstRepair = DiskCacheIndexFile::Parse(Data, CacheFileSize);
  REQUIRE(Hashes(FirstRepair) == std::vector<uint64_t> {0xA, 0xE});
  REQUIRE(FirstRepair.State == DiskCacheIndexFile::ParseState::IncompleteSuffix);

  Overwrite(Data, FirstRepair.ValidSize, F);
  const auto SecondRepair = DiskCacheIndexFile::Parse(Data, CacheFileSize);
  REQUIRE(Hashes(SecondRepair) == std::vector<uint64_t> {0xA, 0xE, 0xF});
  REQUIRE(SecondRepair.State == DiskCacheIndexFile::ParseState::IncompleteSuffix);

  Overwrite(Data, SecondRepair.ValidSize, G);
  const auto ThirdRepair = DiskCacheIndexFile::Parse(Data, CacheFileSize);
  REQUIRE(Hashes(ThirdRepair) == std::vector<uint64_t> {0xA, 0xE, 0xF, 0x10});
  REQUIRE(ThirdRepair.State == DiskCacheIndexFile::ParseState::Complete);
}

TEST_CASE("DiskCacheIndexFile - semantic corruption skips one complete record") {
  auto InvalidKey = MakeRecord(0xA, 128);
  InvalidKey[0] = 'z';
  auto InvalidRange = MakeRecord(0xB, CacheFileSize - 16, 64);
  const auto Valid = MakeRecord(0xC, 384);
  Append(InvalidKey, InvalidRange);
  Append(InvalidKey, Valid);

  const auto Result = DiskCacheIndexFile::Parse(InvalidKey, CacheFileSize);
  REQUIRE(Result.State == DiskCacheIndexFile::ParseState::Complete);
  REQUIRE(Result.ValidSize == InvalidKey.size());
  REQUIRE(Hashes(Result) == std::vector<uint64_t> {0xC});
}
