// SPDX-License-Identifier: MIT
#pragma once

#include "FEXCore/Core/DiskCacheFileMapper.h"
#include "FEXCore/Core/DiskCacheIndexFile.h"
#include "FEXCore/Utils/File.h"
#include "FEXCore/fextl/memory.h"
#include "FEXCore/fextl/robin_map.h"
#include "FEXCore/fextl/string.h"
#include "FEXCore/fextl/vector.h"

#include <atomic>
#include <cstdint>
#include <mutex>
#include <optional>
#include <span>

namespace FEXCore::DiskCache {

class IndexedDB;

struct IndexEntry {
  IndexedDB* DB;
  uint64_t Offset;
  uint32_t Size;
};

using Index = fextl::robin_map<uint64_t, IndexEntry>;

class FOZFile {
public:
  bool Open(const fextl::string& CacheFileName, bool ReadOnly);
  bool Lock(uint32_t TimeoutMS) {
    if (!FD) {
      return false;
    }
    return FD->Lock(TimeoutMS);
  }
  bool Unlock() {
    if (!FD) {
      return false;
    }
    return FD->Unlock();
  }
  File::File::FileHandleType GetHandle() {
    return FD ? FD->GetHandle() : (File::File::FileHandleType)-1;
  }
  ssize_t Size();
  bool Truncate(uint64_t Size);
  bool ReadAll(fextl::vector<uint8_t>& Out);
  bool ReadBlob(uint64_t Offset, std::span<uint8_t> OutBlob);
  bool WriteBlob(const MesaFOZ::foz_payload_key& Key, std::span<const std::span<const uint8_t>> BlobChunks, uint64_t& OutBlobOffset,
                 std::optional<uint64_t> WriteOffset = std::nullopt);

private:
  static constexpr uint32_t OPEN_LOCK_TIMEOUT_MS = 100;

  fextl::string FileName;
  fextl::unique_ptr<File::File> FD;
  bool ReadOnly = false;
};

class IndexedDB {
public:
  bool Open(const fextl::string& CacheDBName, bool ReadOnly);
  void PopulateIndex(Index& CacheIndex, bool& FoundMetadata);
  bool ReadCacheBlob(uint64_t Offset, std::span<uint8_t> OutBlob);
  bool StoreCacheBlob(const MesaFOZ::foz_payload_key& Key, std::span<const uint8_t> Blob, Index& CacheIndex, std::mutex& IndexMutex);

private:
  // Stores run on the writer thread, so returning quickly is less important.
  static constexpr uint32_t STORE_LOCK_TIMEOUT_MS = 1000;
  static constexpr uint64_t BIG_MAPPING_SIZE = 1ULL << 33;

  FOZFile CacheFOZ;
  uint8_t* CacheFileMapping = nullptr;
  std::atomic<uint64_t> CacheFileSize;
  std::optional<uint64_t> CacheAppendOffset;
  FOZFile IndexFOZ;
  uint64_t ObservedIndexFileSize {};
  std::optional<uint64_t> IndexAppendOffset;
  bool ReadOnly = false;
};

} // namespace FEXCore::DiskCache
