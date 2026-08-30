// SPDX-License-Identifier: MIT
#pragma once
#include "FEXCore/Core/CodeCache.h"
#include "FEXCore/Core/Context.h"
#include "FEXCore/Core/DiskCacheFile.h"
#include "FEXCore/Core/DiskCacheStorage.h"
#include "Interface/Core/JIT/Relocations.h"
#include "Interface/Core/Frontend.h"
#include "Interface/Core/CPUBackend.h"
#include "FEXCore/Config/Config.h"
#include "FEXCore/Utils/File.h"
#include "FEXCore/Utils/WorkQueueThread.h"
#include "FEXCore/fextl/memory.h"
#include <FEXCore/fextl/string.h>
#include <FEXCore/fextl/unordered_set.h>
#include <FEXCore/fextl/robin_map.h>
#include <FEXCore/fextl/vector.h>
#include <stdint.h>
#include <mutex>
#include <optional>
#include <span>
#include <xxhash.h>

namespace FEXCore {

namespace Context {
  class ContextImpl;
}

namespace DiskCache {

  struct CodeHitData {
    fextl::vector<uint8_t> Blob;
    std::span<uint8_t> HostCode;
    std::span<uint64_t> GuestPages;
    std::span<uint64_t> EntryPointRIPs;
    std::span<const uint32_t> EntryPointHostOffsets;
    std::span<const BlobSmallRelocation> SmallRelocs;
    std::span<const BlobThunkRelocation> ThunkRelocs;

    // the spans above point to memory owned by the Blob vec, so it's important this can't be copied
    CodeHitData() = default;
    CodeHitData(CodeHitData&&) = default;
    CodeHitData& operator=(CodeHitData&&) = default;
    CodeHitData(const CodeHitData&) = delete;
    CodeHitData& operator=(const CodeHitData&) = delete;
  };

  class DiskCache {
  public:
    void Init(FEXCore::Context::ContextImpl* CTX);

    std::optional<CodeHitData> Lookup(Core::InternalThreadState* Thread, const ExecutableFileSectionInfo& Region, uint64_t GuestRIP);
    bool Store(Core::InternalThreadState* Thread, const ExecutableFileSectionInfo& Region, uint64_t GuestRIP,
               std::span<const uint8_t> GuestCode, const CPU::CPUBackend::CompiledCode& CompiledCode,
               std::span<const FEXCore::CPU::Relocation> Relocations, const Frontend::Decoder::DecodedBlockInformation* DecodedBlockInfo);

    bool IsWritingDiskCache() const {
      return (bool)RWCacheDB;
    }
    bool IsReadingDiskCache() const {
      return !ROCacheDBs.empty() || RWCacheDB != nullptr;
    }

  private:
    bool OpenCacheDB(const fextl::string& CacheDBName, bool ReadOnly);
    uint64_t MakeBlobKey(const uint64_t ModuleOffset);

    FEXCore::Context::ContextImpl* CTX;
    static const uint16_t FormatVersion = 3;
    XXH128_hash_t BucketHash;
    fextl::vector<fextl::unique_ptr<IndexedDB>> ROCacheDBs;
    fextl::unique_ptr<IndexedDB> RWCacheDB;
    Index Index;
    std::mutex IndexLock;
    bool FoundMetadata = false;
    struct CacheStoreWorkItem;

    // the Writer holds references to all this stuff above and needs to be last
    fextl::unique_ptr<WorkQueueThread> Writer;

    FEX_CONFIG_OPT(EnableDiskCache, DISKCACHE);
    FEX_CONFIG_OPT(MapDiskCacheFiles, DISKCACHEFILEMAPPING);
    FEX_CONFIG_OPT(RelocationFilter, DISKCACHERELOCATIONFILTER);
    FEX_CONFIG_OPT(BasePathOverride, DISKCACHEPATH);
    FEX_CONFIG_OPT(RODBNames, DISKCACHERODBNAMES);
  };

} // namespace DiskCache

} // namespace FEXCore
