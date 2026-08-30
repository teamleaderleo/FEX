// SPDX-License-Identifier: MIT

#include <FEXCore/Core/DiskCacheFile.h>
#include <FEXCore/Core/DiskCacheStorage.h>

#include <catch2/catch_test_macros.hpp>
#include <fmt/format.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <mutex>
#include <span>
#include <string>
#include <system_error>
#include <vector>

namespace {

using namespace FEXCore;

struct TempTree {
  std::filesystem::path Path;

  ~TempTree() {
    std::error_code Error;
    std::filesystem::remove_all(Path, Error);
  }
};

DiskCache::MesaFOZ::foz_payload_key MakeKey(uint64_t Hash) {
  DiskCache::MesaFOZ::foz_payload_key Key {};
  const auto Name = fmt::format("{:016x}", Hash);
  std::memcpy(Key.bytes, Name.data(), Name.size());
  return Key;
}

std::vector<uint64_t> IndexHashes(const DiskCache::Index& Index) {
  std::vector<uint64_t> Result;
  for (const auto& [Hash, Entry] : Index) {
    static_cast<void>(Entry);
    Result.push_back(Hash);
  }
  std::ranges::sort(Result);
  return Result;
}

void AppendDataOnlyRecord(const std::filesystem::path& CachePath, const DiskCache::MesaFOZ::foz_payload_key& Key,
                          std::span<const uint8_t> Blob) {
  std::ofstream CacheFile(CachePath, std::ios::binary | std::ios::app);
  REQUIRE(CacheFile.good());

  const DiskCache::MesaFOZ::foz_payload_header Header {
    .payload_size = static_cast<uint32_t>(Blob.size()),
    .format = 1,
    .crc = 0,
    .uncompressed_size = static_cast<uint32_t>(Blob.size()),
  };
  CacheFile.write(reinterpret_cast<const char*>(Key.bytes), sizeof(Key.bytes));
  CacheFile.write(reinterpret_cast<const char*>(&Header), sizeof(Header));
  CacheFile.write(reinterpret_cast<const char*>(Blob.data()), Blob.size());
  REQUIRE(CacheFile.good());
}

} // namespace

TEST_CASE("DiskCacheIndexRecovery - a real writable database replaces its torn suffix") {
  const auto Unique = std::chrono::steady_clock::now().time_since_epoch().count();
  TempTree Temp {.Path = std::filesystem::temp_directory_path() / fmt::format("fex-index-recovery-{}", Unique)};
  REQUIRE(std::filesystem::create_directory(Temp.Path));

  const auto BasePath = Temp.Path / "RWCacheDB";
  const auto BasePathString = BasePath.string();
  const fextl::string FEXBasePath {BasePathString.c_str()};
  const auto KeyA = MakeKey(0xA);
  const auto KeyB = MakeKey(0xB);
  const auto KeyC = MakeKey(0xC);
  const std::array<uint8_t, 64> Blob {};

  {
    DiskCache::IndexedDB DB;
    DiskCache::Index Index;
    std::mutex IndexMutex;
    bool FoundMetadata = false;
    REQUIRE(DB.Open(FEXBasePath, false));
    DB.PopulateIndex(Index, FoundMetadata);
    REQUIRE_FALSE(FoundMetadata);
    REQUIRE(DB.StoreCacheBlob(KeyA, Blob, Index, IndexMutex));
    REQUIRE(IndexHashes(Index) == std::vector<uint64_t> {0xA});
  }

  const auto IndexPath = BasePathString + "_idx.foz";
  {
    std::ofstream IndexFile(IndexPath, std::ios::binary | std::ios::app);
    REQUIRE(IndexFile.good());
    IndexFile.write(reinterpret_cast<const char*>(KeyB.bytes), 12);
    REQUIRE(IndexFile.good());
  }

  const auto TornIndexSize = std::filesystem::file_size(IndexPath);
  {
    DiskCache::IndexedDB DB;
    DiskCache::Index Index;
    bool FoundMetadata = false;
    REQUIRE(DB.Open(FEXBasePath, true));
    DB.PopulateIndex(Index, FoundMetadata);
    REQUIRE(IndexHashes(Index) == std::vector<uint64_t> {0xA});
  }
  REQUIRE(std::filesystem::file_size(IndexPath) == TornIndexSize);

  {
    DiskCache::IndexedDB DB;
    DiskCache::Index Index;
    std::mutex IndexMutex;
    bool FoundMetadata = false;
    REQUIRE(DB.Open(FEXBasePath, false));
    DB.PopulateIndex(Index, FoundMetadata);
    REQUIRE(IndexHashes(Index) == std::vector<uint64_t> {0xA});
    REQUIRE(DB.StoreCacheBlob(KeyC, Blob, Index, IndexMutex));
    REQUIRE(IndexHashes(Index) == std::vector<uint64_t> {0xA, 0xC});
  }

  constexpr uintmax_t MagicSize = 16;
  constexpr uintmax_t RecordSize = sizeof(DiskCache::MesaFOZ::foz_payload_key) + sizeof(DiskCache::MesaFOZ::foz_payload_header) +
                                   sizeof(DiskCache::MesaFOZ::mesa_index_db_file_entry);
  REQUIRE(std::filesystem::file_size(IndexPath) == MagicSize + 2 * RecordSize);

  {
    DiskCache::IndexedDB DB;
    DiskCache::Index Index;
    bool FoundMetadata = false;
    REQUIRE(DB.Open(FEXBasePath, true));
    DB.PopulateIndex(Index, FoundMetadata);
    REQUIRE(IndexHashes(Index) == std::vector<uint64_t> {0xA, 0xC});
  }
}

TEST_CASE("DiskCacheIndexRecovery - a stale writer rechecks another handle's complete append") {
  const auto Unique = std::chrono::steady_clock::now().time_since_epoch().count();
  TempTree Temp {.Path = std::filesystem::temp_directory_path() / fmt::format("fex-index-coherence-{}", Unique)};
  REQUIRE(std::filesystem::create_directory(Temp.Path));

  const auto BasePathString = (Temp.Path / "RWCacheDB").string();
  const fextl::string FEXBasePath {BasePathString.c_str()};
  const auto KeyA = MakeKey(0xA);
  const auto KeyB = MakeKey(0xB);
  const auto KeyC = MakeKey(0xC);
  const std::array<uint8_t, 64> Blob {};
  DiskCache::Index FirstIndex;
  std::mutex FirstMutex;
  bool FoundMetadata = false;
  DiskCache::IndexedDB FirstDB;
  REQUIRE(FirstDB.Open(FEXBasePath, false));
  FirstDB.PopulateIndex(FirstIndex, FoundMetadata);
  REQUIRE(FirstDB.StoreCacheBlob(KeyA, Blob, FirstIndex, FirstMutex));

  {
    DiskCache::IndexedDB SecondDB;
    DiskCache::Index SecondIndex;
    std::mutex SecondMutex;
    FoundMetadata = false;
    REQUIRE(SecondDB.Open(FEXBasePath, false));
    SecondDB.PopulateIndex(SecondIndex, FoundMetadata);
    REQUIRE(SecondDB.StoreCacheBlob(KeyB, Blob, SecondIndex, SecondMutex));
  }

  REQUIRE(FirstDB.StoreCacheBlob(KeyC, Blob, FirstIndex, FirstMutex));

  {
    DiskCache::IndexedDB FinalDB;
    DiskCache::Index FinalIndex;
    FoundMetadata = false;
    REQUIRE(FinalDB.Open(FEXBasePath, true));
    FinalDB.PopulateIndex(FinalIndex, FoundMetadata);
    REQUIRE(IndexHashes(FinalIndex) == std::vector<uint64_t> {0xA, 0xB, 0xC});
  }
}

TEST_CASE("DiskCacheIndexRecovery - the next writer reclaims a trailing unindexed data record") {
  const auto Unique = std::chrono::steady_clock::now().time_since_epoch().count();
  TempTree Temp {.Path = std::filesystem::temp_directory_path() / fmt::format("fex-data-tail-recovery-{}", Unique)};
  REQUIRE(std::filesystem::create_directory(Temp.Path));

  const auto BasePathString = (Temp.Path / "RWCacheDB").string();
  const fextl::string FEXBasePath {BasePathString.c_str()};
  const auto CachePath = std::filesystem::path {BasePathString + ".foz"};
  const auto KeyA = MakeKey(0xA);
  const auto OrphanKey = MakeKey(0xB);
  const auto KeyC = MakeKey(0xC);
  const std::array<uint8_t, 32> BlobA {0xA};
  const std::array<uint8_t, 47> OrphanBlob {0xB};
  const std::array<uint8_t, 64> BlobC {0xC};

  {
    DiskCache::IndexedDB DB;
    DiskCache::Index Index;
    std::mutex IndexMutex;
    bool FoundMetadata = false;
    REQUIRE(DB.Open(FEXBasePath, false));
    DB.PopulateIndex(Index, FoundMetadata);
    REQUIRE(DB.StoreCacheBlob(KeyA, BlobA, Index, IndexMutex));
  }

  constexpr uintmax_t DataRecordOverhead =
    sizeof(DiskCache::MesaFOZ::foz_payload_key) + sizeof(DiskCache::MesaFOZ::foz_payload_header);
  const auto ReferencedSize = std::filesystem::file_size(CachePath);
  AppendDataOnlyRecord(CachePath, OrphanKey, OrphanBlob);
  const auto OrphanedSize = std::filesystem::file_size(CachePath);
  REQUIRE(OrphanedSize == ReferencedSize + DataRecordOverhead + OrphanBlob.size());

  {
    DiskCache::IndexedDB ReadOnly;
    DiskCache::Index Index;
    bool FoundMetadata = false;
    REQUIRE(ReadOnly.Open(FEXBasePath, true));
    ReadOnly.PopulateIndex(Index, FoundMetadata);
    REQUIRE(IndexHashes(Index) == std::vector<uint64_t> {0xA});
  }
  REQUIRE(std::filesystem::file_size(CachePath) == OrphanedSize);

  {
    DiskCache::IndexedDB Writer;
    DiskCache::Index Index;
    std::mutex IndexMutex;
    bool FoundMetadata = false;
    REQUIRE(Writer.Open(FEXBasePath, false));
    Writer.PopulateIndex(Index, FoundMetadata);
    REQUIRE(IndexHashes(Index) == std::vector<uint64_t> {0xA});
    REQUIRE(Writer.StoreCacheBlob(KeyC, BlobC, Index, IndexMutex));
  }

  REQUIRE(std::filesystem::file_size(CachePath) == ReferencedSize + DataRecordOverhead + BlobC.size());

  DiskCache::IndexedDB Restarted;
  DiskCache::Index RestartedIndex;
  bool FoundMetadata = false;
  REQUIRE(Restarted.Open(FEXBasePath, true));
  Restarted.PopulateIndex(RestartedIndex, FoundMetadata);
  REQUIRE(IndexHashes(RestartedIndex) == std::vector<uint64_t> {0xA, 0xC});

  std::array<uint8_t, BlobA.size()> ReadbackA {};
  REQUIRE(Restarted.ReadCacheBlob(RestartedIndex.at(0xA).Offset, ReadbackA));
  REQUIRE(ReadbackA == BlobA);
  std::array<uint8_t, BlobC.size()> ReadbackC {};
  REQUIRE(Restarted.ReadCacheBlob(RestartedIndex.at(0xC).Offset, ReadbackC));
  REQUIRE(ReadbackC == BlobC);
}

TEST_CASE("DiskCacheIndexRecovery - a minimal block blob retains nonzero guest identity") {
  const auto Unique = std::chrono::steady_clock::now().time_since_epoch().count();
  TempTree Temp {.Path = std::filesystem::temp_directory_path() / fmt::format("fex-minimal-block-blob-{}", Unique)};
  REQUIRE(std::filesystem::create_directory(Temp.Path));

  const auto BasePath = Temp.Path / "RWCacheDB";
  const auto BasePathString = BasePath.string();
  const fextl::string FEXBasePath {BasePathString.c_str()};
  const auto CachePath = BasePathString + ".foz";

  constexpr uint32_t GuestSize = 37;
  constexpr uint32_t HostSize = 16;
  constexpr size_t RequiredSize =
    sizeof(DiskCache::BlobFixedHeader) + HostSize + sizeof(uint64_t) + sizeof(uint64_t) + sizeof(uint32_t);
  std::array<uint8_t, RequiredSize> Blob {};
  const DiskCache::BlobFixedHeader Header {
    .GuestSize = GuestSize,
    .HostSize = HostSize,
    .EntryPointCount = 1,
    .SmallRelocCount = 0,
    .ThunkRelocCount = 0,
    .TouchedGuestPagesCount = 1,
    .GuestHash = {.low64 = 0x0123'4567'89ab'cdef, .high64 = 0xfedc'ba98'7654'3210},
  };
  std::memcpy(Blob.data(), &Header, sizeof(Header));
  const size_t EntryPointRIPOffset = sizeof(Header) + HostSize + sizeof(uint64_t);
  const uint64_t PrimaryEntryPoint = 0;
  const uint32_t PrimaryHostOffset = 0;
  std::memcpy(Blob.data() + EntryPointRIPOffset, &PrimaryEntryPoint, sizeof(PrimaryEntryPoint));
  std::memcpy(Blob.data() + EntryPointRIPOffset + sizeof(PrimaryEntryPoint), &PrimaryHostOffset, sizeof(PrimaryHostOffset));

  const auto Validation = DiskCacheFile::Validate(std::as_bytes(std::span {Blob}));
  REQUIRE(Validation.Parsed);
  REQUIRE(Validation.Parsed->Header.GuestSize == GuestSize);
  REQUIRE(Validation.Parsed->RequiredSize == Blob.size());

  constexpr uint64_t Hash = 0x1234'5678'9abc'def0;
  {
    DiskCache::Index Index;
    std::mutex IndexMutex;
    DiskCache::IndexedDB DB;
    REQUIRE(DB.Open(FEXBasePath, false));
    REQUIRE(DB.StoreCacheBlob(MakeKey(Hash), Blob, Index, IndexMutex));
    REQUIRE(Index.at(Hash).Size == Blob.size());
  }

  constexpr uintmax_t MagicSize = 16;
  constexpr uintmax_t DataRecordOverhead =
    sizeof(DiskCache::MesaFOZ::foz_payload_key) + sizeof(DiskCache::MesaFOZ::foz_payload_header);
  REQUIRE(std::filesystem::file_size(CachePath) == MagicSize + DataRecordOverhead + Blob.size());

  DiskCache::Index RestartedIndex;
  bool FoundMetadata = false;
  DiskCache::IndexedDB Restarted;
  REQUIRE(Restarted.Open(FEXBasePath, true));
  Restarted.PopulateIndex(RestartedIndex, FoundMetadata);
  REQUIRE_FALSE(FoundMetadata);
  REQUIRE(RestartedIndex.contains(Hash));
  REQUIRE(RestartedIndex.at(Hash).Size == Blob.size());
  std::array<uint8_t, RequiredSize> Readback {};
  REQUIRE(Restarted.ReadCacheBlob(RestartedIndex.at(Hash).Offset, Readback));
  REQUIRE(Readback == Blob);
}
