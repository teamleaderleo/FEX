// SPDX-License-Identifier: MIT

#include "FEXCore/Config/Config.h"
#include "FEXCore/fextl/string.h"
#include "FEXHeaderUtils/Filesystem.h"
#include "FEXCore/Core/DiskCache.h"
#include "FEXCore/Utils/LogManager.h"
#include "Interface/Context/Context.h"
#include "FEXCore/HLE/SyscallHandler.h"
#include "FEXCore/fextl/memory.h"
#include <cstdint>
#include <cstring>
#include <span>

namespace FEXCore {

namespace DiskCache {

  bool DiskCache::OpenCacheDB(const fextl::string& CacheDBName, bool ReadOnly) {
    fextl::unique_ptr<IndexedDB> CurDB;

    if (!ReadOnly && RWCacheDB) {
      // rw already opened, just support one
      return false;
    }

    CurDB = fextl::make_unique<IndexedDB>();
    if (!CurDB) {
      return false;
    }

    if (!CurDB->Open(CacheDBName, ReadOnly)) {
      CurDB.reset();
      return false;
    }

    CurDB->PopulateIndex(Index, FoundMetadata);

    if (ReadOnly) {
      ROCacheDBs.push_back(std::move(CurDB));
    } else {
      RWCacheDB = std::move(CurDB);
    }

    return true;
  }

  void DiskCache::Init(FEXCore::Context::ContextImpl* CTX) {
    this->CTX = CTX;

    if (!EnableDiskCache) {
      return;
    }

    fextl::string SerializedConfig = FEXCore::Config::SerializeForCache();

    struct __attribute__((packed)) {
      uint16_t FormatVersion;
      uint8_t Is64BitMode;
      uint64_t HostFeaturesHash;
    } BucketHeader = {FormatVersion, CTX->Config.Is64BitMode, CTX->HostFeatures.HashForCaching()};

    fextl::vector<uint8_t> BucketBytes;
    BucketBytes.resize(sizeof(BucketHeader) + SerializedConfig.size());
    memcpy(BucketBytes.data(), &BucketHeader, sizeof(BucketHeader));
    memcpy(BucketBytes.data() + sizeof(BucketHeader), SerializedConfig.data(), SerializedConfig.size());
    BucketHash = XXH3_128bits(BucketBytes.data(), BucketBytes.size());

    fextl::string BasePath = BasePathOverride();
    if (BasePath.empty()) {
      BasePath = FEXCore::Config::GetCacheDirectory() + "DiskCache/";
      BasePath += fextl::fmt::format("{:016x}{:016x}", BucketHash.high64, BucketHash.low64) + "/";
    }
    FHU::Filesystem::CreateDirectories(BasePath);

    if (!MapDiskCacheFiles) {
      SetFileMapper(nullptr);
    }

    fextl::string RWDBBasePath = BasePath + "RWCacheDB";
    OpenCacheDB(RWDBBasePath, false);

    if (RWCacheDB && !FoundMetadata) {
      // we just opened a fresh cache, add a metadata blob
      MesaFOZ::foz_payload_key MetadataKey;
      memset(MetadataKey.bytes, 0xFF, sizeof(MetadataKey));
      RWCacheDB->StoreCacheBlob(MetadataKey, {BucketBytes.data(), BucketBytes.size()}, Index, IndexLock);
      Index.clear();
    }

    std::string_view RONames = RODBNames();
    while (!RONames.empty()) {
      const auto Delim = RONames.find(',');
      const std::string_view ROName = RONames.substr(0, Delim);
      if (!ROName.empty()) {
        fextl::string RODBBasePath = BasePath;
        RODBBasePath += ROName;
        OpenCacheDB(RODBBasePath, true);
      }
      if (Delim == std::string_view::npos) {
        break;
      }
      // advance to next
      RONames.remove_prefix(Delim + 1);
    }

    if (IsWritingDiskCache()) {
      Writer = fextl::make_unique<WorkQueueThread>(true);
    }
  }

  uint64_t DiskCache::MakeBlobKey(const uint64_t ModuleOffset) {
    struct {
      uint64_t ModuleOffset;
      XXH128_hash_t BucketHash;
    } BlobKeyBytes = {ModuleOffset, BucketHash};

    return XXH3_64bits(&BlobKeyBytes, sizeof(BlobKeyBytes));
  }

  std::optional<CodeHitData> DiskCache::Lookup(Core::InternalThreadState* Thread, const ExecutableFileSectionInfo& Region, uint64_t GuestRIP) {
    if (!IsReadingDiskCache()) {
      return std::nullopt;
    }
    uint64_t ModuleOffset = GuestRIP - Region.FileStartVA;

    uint64_t Hash = MakeBlobKey(ModuleOffset);

    IndexEntry Entry;
    {
      std::lock_guard Guard(IndexLock);
      auto It = Index.find(Hash);
      if (It == Index.end()) {
        // definite miss
        return std::nullopt;
      }
      // we can't hold onto the iterator, the map may shift while we don't hold the lock
      Entry = It->second;
    }
    // found a key hash match, could still be a miss, read the blob and verify more
    CodeHitData HitData;
    HitData.Blob.resize(Entry.Size);
    if (!Entry.DB->ReadCacheBlob(Entry.Offset, HitData.Blob)) {
      return std::nullopt;
    }

    const std::span<const uint8_t> BlobBytes {HitData.Blob.data(), HitData.Blob.size()};
    const auto Validation = DiskCacheFile::Validate(std::as_bytes(BlobBytes));
    if (!Validation.Parsed) {
      return std::nullopt;
    }
    const auto& Layout = *Validation.Parsed;
    const auto& Header = Layout.Header;

    // do we have enough room in our live code to even hash GuestSize worth?
    auto RangeInfo = CTX->SyscallHandler->QueryGuestExecutableRange(Thread, GuestRIP);
    if (RangeInfo.Size == 0 || RangeInfo.Base > GuestRIP) {
      return std::nullopt;
    }
    uint64_t Available = RangeInfo.Base + RangeInfo.Size - GuestRIP;
    if (Available < Header.GuestSize) {
      return std::nullopt;
    }

    XXH128_hash_t LiveGuestHash = XXH3_128bits(reinterpret_cast<void*>(GuestRIP), Header.GuestSize);
    if (std::memcmp(&LiveGuestHash, &Header.GuestHash, sizeof(Header.GuestHash)) != 0) {
      // LogMan::Msg::IFmt("hash mismatch! length {:d}", Header.GuestSize);
      return std::nullopt;
    }
    // LogMan::Msg::IFmt("hash ok! length {:d}", Header.GuestSize);

    HitData.HostCode = {HitData.Blob.data() + Layout.HostCodeOffset, Header.HostSize};
    HitData.GuestPages = {reinterpret_cast<uint64_t*>(HitData.Blob.data() + Layout.GuestPagesOffset), Header.TouchedGuestPagesCount};
    HitData.EntryPointRIPs = {reinterpret_cast<uint64_t*>(HitData.Blob.data() + Layout.EntryPointRIPsOffset), Header.EntryPointCount};
    HitData.EntryPointHostOffsets = {
      reinterpret_cast<const uint32_t*>(HitData.Blob.data() + Layout.EntryPointHostOffsetsOffset), Header.EntryPointCount};
    HitData.SmallRelocs = {
      reinterpret_cast<const BlobSmallRelocation*>(HitData.Blob.data() + Layout.SmallRelocationsOffset), Header.SmallRelocCount};
    HitData.ThunkRelocs = {
      reinterpret_cast<const BlobThunkRelocation*>(HitData.Blob.data() + Layout.ThunkRelocationsOffset), Header.ThunkRelocCount};

    for (auto& PageOffset : HitData.GuestPages) {
      PageOffset += GuestRIP;
    }
    for (auto& EntryPointRip : HitData.EntryPointRIPs) {
      EntryPointRip += GuestRIP;
    }

    return HitData;
  }

  struct DiskCache::CacheStoreWorkItem final : WorkQueueThread::WorkItem {
    DiskCache* Self;
    IndexedDB* DB;
    MesaFOZ::foz_payload_key Key;
    fextl::vector<uint8_t> Blob;
    CacheStoreWorkItem(DiskCache* Self, IndexedDB* DB, const MesaFOZ::foz_payload_key& Key, fextl::vector<uint8_t>&& Blob)
      : Self(Self)
      , DB(DB)
      , Key(Key)
      , Blob(std::move(Blob)) {}
    void Run() override {
      DB->StoreCacheBlob(Key, Blob, Self->Index, Self->IndexLock);
    }
  };

  bool DiskCache::Store(Core::InternalThreadState* Thread, const ExecutableFileSectionInfo& Region, uint64_t GuestRIP,
                        std::span<const uint8_t> GuestCode, const CPU::CPUBackend::CompiledCode& CompiledCode,
                        std::span<const FEXCore::CPU::Relocation> Relocations, const Frontend::Decoder::DecodedBlockInformation* DecodedBlockInfo) {
    if (!IsWritingDiskCache()) {
      return false;
    }
    if (!DecodedBlockInfo) {
      return false;
    }

    // check for any reloc targets outside of our jurisdiction
    // todo what are they exactly? caching those blocks is great when it works, so need to figure this out and make finer-grained if we can
    if (RelocationFilter) {
      for (const auto& Reloc : Relocations) {
        if (Reloc.Header.Type != CPU::RelocationTypes::RELOC_GUEST_RIP_LITERAL && Reloc.Header.Type != CPU::RelocationTypes::RELOC_GUEST_RIP_MOVE) {
          continue;
        }
        uint64_t Target = Reloc.GuestRIP.GuestRIP;
        if (Target >= Region.BeginVA && Target < Region.EndVA) {
          continue;
        }
        auto TargetSection = CTX->SyscallHandler->LookupExecutableFileSection(Thread, Target);
        if (!TargetSection || TargetSection->FileInfo.FileId != Region.FileInfo.FileId) {
          // we don't know where it's pointing, so we don't know how to encode the offset, so we can't cache atm
          return false;
        }
      }
    }

    uint32_t SmallRelocCount = 0;
    uint32_t ThunkRelocCount = 0;
    for (const auto& Reloc : Relocations) {
      if (Reloc.Header.Type == CPU::RelocationTypes::RELOC_NAMED_THUNK_MOVE) {
        ThunkRelocCount++;
      } else {
        SmallRelocCount++;
      }
    }

    const uint32_t EntryPointCount = (uint32_t)CompiledCode.EntryPoints.size();
    const uint32_t TouchedGuestPagesCount = DecodedBlockInfo ? (uint32_t)DecodedBlockInfo->CodePages.size() : 0;

    const size_t HeaderOffset = 0;
    const size_t HostCodeOffset = HeaderOffset + sizeof(BlobFixedHeader);
    const size_t TouchedGuestPagesOffset = HostCodeOffset + CompiledCode.Size;
    const size_t EntryPointRIPsOffset = TouchedGuestPagesOffset + TouchedGuestPagesCount * sizeof(uint64_t);
    const size_t EntryPointHostOffsetsOffset = EntryPointRIPsOffset + EntryPointCount * sizeof(uint64_t);
    const size_t SmallRelocsOffset = EntryPointHostOffsetsOffset + EntryPointCount * sizeof(uint32_t);
    const size_t ThunkRelocsOffset = SmallRelocsOffset + SmallRelocCount * sizeof(BlobSmallRelocation);
    const size_t RequiredSize = ThunkRelocsOffset + ThunkRelocCount * sizeof(BlobThunkRelocation);

    // we'll copy everything into here and pass it to the Writer, then return to caller quickly
    fextl::vector<uint8_t> Blob;
    // GuestCode remains an identity input through GuestSize and GuestHash. It was historically
    // appended after the required layout, but no reader consumes that duplicate byte tail.
    Blob.resize(RequiredSize);
    uint8_t* BlobData = Blob.data();

    uint64_t ModuleOffset = GuestRIP - Region.FileStartVA;

    uint64_t BlobKey = MakeBlobKey(ModuleOffset);
    MesaFOZ::foz_payload_key Key = {};
    fextl::string BlobName = fextl::fmt::format("{:016x}", BlobKey);
    memcpy(Key.bytes, BlobName.data(), BlobName.size());

    BlobFixedHeader Header {
      .GuestSize = (uint32_t)GuestCode.size(),
      .HostSize = (uint32_t)CompiledCode.Size,
      .EntryPointCount = EntryPointCount,
      .SmallRelocCount = SmallRelocCount,
      .ThunkRelocCount = ThunkRelocCount,
      .TouchedGuestPagesCount = TouchedGuestPagesCount,
      .GuestHash = XXH3_128bits(GuestCode.data(), GuestCode.size()),
    };
    memcpy(BlobData + HeaderOffset, &Header, sizeof(Header));
    memcpy(BlobData + HostCodeOffset, CompiledCode.BlockBegin, CompiledCode.Size);

    // relocate touched pages relative to GuestRIP
    auto* PageOffsets = reinterpret_cast<uint64_t*>(BlobData + TouchedGuestPagesOffset);
    uint32_t PageIdx = 0;
    for (auto GuestPage : DecodedBlockInfo->CodePages) {
      PageOffsets[PageIdx++] = GuestPage - GuestRIP;
    }

    // pack and relocate entrypoints
    auto* EntryRIPs = reinterpret_cast<uint64_t*>(BlobData + EntryPointRIPsOffset);
    auto* EntryHostOffsets = reinterpret_cast<uint32_t*>(BlobData + EntryPointHostOffsetsOffset);
    uint32_t EntryIdx = 0;
    for (auto [GuestAddr, HostAddr] : CompiledCode.EntryPoints) {
      EntryRIPs[EntryIdx] = GuestAddr - GuestRIP;
      EntryHostOffsets[EntryIdx] = uint32_t(HostAddr - CompiledCode.BlockBegin);
      EntryIdx++;
    }

    // pack relocations
    auto* SmallRelocs = reinterpret_cast<BlobSmallRelocation*>(BlobData + SmallRelocsOffset);
    auto* ThunkRelocs = reinterpret_cast<BlobThunkRelocation*>(BlobData + ThunkRelocsOffset);
    uint32_t SmallIdx = 0;
    uint32_t ThunkIdx = 0;
    for (const auto& Reloc : Relocations) {
      switch (Reloc.Header.Type) {
      // it's important to zero-init the element completely so we don't have garbage in unused fields
      // this way, the caches stay deterministic across machines
      case CPU::RelocationTypes::RELOC_NAMED_SYMBOL_LITERAL: {
        BlobSmallRelocation SmallReloc = {};
        SmallReloc.Offset = Reloc.Header.Offset;
        SmallReloc.Type = uint8_t(Reloc.Header.Type);
        SmallReloc.Named.Symbol = uint32_t(Reloc.NamedSymbolLiteral.Symbol);
        SmallRelocs[SmallIdx++] = SmallReloc;
        break;
      }
      case CPU::RelocationTypes::RELOC_GUEST_RIP_LITERAL: {
        BlobSmallRelocation SmallReloc = {};
        SmallReloc.Offset = Reloc.Header.Offset;
        SmallReloc.Type = uint8_t(Reloc.Header.Type);
        SmallReloc.RIPLiteral.GuestRIP = Reloc.GuestRIP.GuestRIP - GuestRIP;
        SmallRelocs[SmallIdx++] = SmallReloc;
        break;
      }
      case CPU::RelocationTypes::RELOC_GUEST_RIP_MOVE: {
        BlobSmallRelocation SmallReloc = {};
        SmallReloc.Offset = Reloc.Header.Offset;
        SmallReloc.Type = uint8_t(Reloc.Header.Type);
        SmallReloc.RIPMove.RegisterIndex = Reloc.GuestRIP.RegisterIndex;
        SmallReloc.RIPMove.GuestRIP = Reloc.GuestRIP.GuestRIP - GuestRIP;
        SmallRelocs[SmallIdx++] = SmallReloc;
        break;
      }
      case CPU::RelocationTypes::RELOC_NAMED_THUNK_MOVE: {
        BlobThunkRelocation BigReloc = {};
        BigReloc.Offset = Reloc.Header.Offset;
        BigReloc.RegisterIndex = Reloc.NamedThunkMove.RegisterIndex;
        memcpy(BigReloc.SymbolHash, &Reloc.NamedThunkMove.Symbol, sizeof(BigReloc.SymbolHash));
        ThunkRelocs[ThunkIdx++] = BigReloc;
        break;
      }
      }
    }

    // hand the rest off to the writer thread
    Writer->QueueWork(fextl::make_unique<CacheStoreWorkItem>(this, RWCacheDB.get(), Key, std::move(Blob)));
    return true;
  }

} // namespace DiskCache

} // namespace FEXCore
