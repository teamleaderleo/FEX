// SPDX-License-Identifier: MIT

#include <FEXCore/Core/DiskCache.h>

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
