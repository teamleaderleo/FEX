// SPDX-License-Identifier: MIT

#include "FEXCore/Core/DiskCacheStorage.h"

#include <charconv>
#include <cstdint>
#include <cstring>
#include <span>

namespace FEXCore::DiskCache {

namespace MesaFOZ {

enum { FOSSILIZE_COMPRESSION_NONE = 1, FOSSILIZE_COMPRESSION_DEFLATE = 2 };

enum { FOSSILIZE_FORMAT_VERSION = 6, FOSSILIZE_FORMAT_MIN_COMPAT_VERSION = 5 };

#define FOZ_REF_MAGIC_SIZE 16

static const uint8_t stream_reference_magic_and_version[FOZ_REF_MAGIC_SIZE] = {
  0x81, 'F', 'O', 'S', 'S', 'I', 'L', 'I', 'Z', 'E', 'D', 'B', 0, 0, 0, FOSSILIZE_FORMAT_VERSION,
};

} // namespace MesaFOZ

static FileMapperFunc FileMapper = nullptr;

bool FOZFile::Open(const fextl::string& FOZFileName, bool ReadOnly) {
  FileName = FOZFileName;
  this->ReadOnly = ReadOnly;

  File::FileModes Modes = File::FileModes::READ;
  if (!ReadOnly) {
    Modes = Modes | File::FileModes::WRITE | File::FileModes::CREATE;
  }
  FD = fextl::make_unique<File::File>(FileName.c_str(), Modes, false);
  if (!FD->IsValid()) {
    FD.reset();
    return false;
  }

  bool Valid = false;
  bool TookLock = false;
  ssize_t Size = FD->Size();

  if (Size < FOZ_REF_MAGIC_SIZE && !ReadOnly) {
    if (!FD->Lock(OPEN_LOCK_TIMEOUT_MS)) {
      FD.reset();
      return false;
    }
    TookLock = true;
    Size = FD->Size();
  }

  if (Size == 0 && !ReadOnly) {
    Valid = FD->PWrite(MesaFOZ::stream_reference_magic_and_version, FOZ_REF_MAGIC_SIZE, 0) == FOZ_REF_MAGIC_SIZE;
  } else {
    uint8_t Magic[FOZ_REF_MAGIC_SIZE];
    if (FD->PRead(Magic, FOZ_REF_MAGIC_SIZE, 0) == FOZ_REF_MAGIC_SIZE &&
        memcmp(Magic, MesaFOZ::stream_reference_magic_and_version, FOZ_REF_MAGIC_SIZE - 1) == 0) {
      const int Version = Magic[FOZ_REF_MAGIC_SIZE - 1];
      Valid = Version <= MesaFOZ::FOSSILIZE_FORMAT_VERSION && Version >= MesaFOZ::FOSSILIZE_FORMAT_MIN_COMPAT_VERSION;
    }
  }

  if (TookLock) {
    FD->Unlock();
  }

  if (!Valid) {
    FD.reset();
  }
  return Valid;
}

ssize_t FOZFile::Size() {
  return FD ? FD->Size() : -1;
}

bool FOZFile::Truncate(uint64_t Size) {
  return FD && FD->Truncate(Size);
}

bool FOZFile::ReadAll(fextl::vector<uint8_t>& Out) {
  const ssize_t FileSize = Size();
  if (FileSize < FOZ_REF_MAGIC_SIZE) {
    return false;
  }
  Out.resize(static_cast<size_t>(FileSize) - FOZ_REF_MAGIC_SIZE);
  return FD->PRead(Out.data(), Out.size(), FOZ_REF_MAGIC_SIZE) == static_cast<ssize_t>(Out.size());
}

bool FOZFile::ReadBlob(uint64_t Offset, std::span<uint8_t> OutBlob) {
  return FD->PRead(OutBlob.data(), OutBlob.size(), Offset) == static_cast<ssize_t>(OutBlob.size());
}

bool FOZFile::WriteBlob(const MesaFOZ::foz_payload_key& Key, std::span<const std::span<const uint8_t>> BlobChunks,
                        uint64_t& OutBlobOffset, std::optional<uint64_t> RequestedWriteOffset) {
  const ssize_t FileSize = FD->Size();
  if (FileSize < 0) {
    return false;
  }
  if (RequestedWriteOffset && (*RequestedWriteOffset < FOZ_REF_MAGIC_SIZE || *RequestedWriteOffset > static_cast<uint64_t>(FileSize))) {
    return false;
  }
  uint64_t WriteOffset = RequestedWriteOffset.value_or(static_cast<uint64_t>(FileSize));

  if (FD->PWrite(Key.bytes, sizeof(Key.bytes), WriteOffset) != sizeof(Key.bytes)) {
    return false;
  }
  WriteOffset += sizeof(Key.bytes);

  uint64_t TotalBlobSize = 0;
  for (const std::span<const uint8_t>& Chunk : BlobChunks) {
    TotalBlobSize += Chunk.size();
  }

  MesaFOZ::foz_payload_header ScratchHeader {
    .payload_size = static_cast<uint32_t>(TotalBlobSize),
    .format = MesaFOZ::FOSSILIZE_COMPRESSION_NONE,
    .crc = 0,
    .uncompressed_size = static_cast<uint32_t>(TotalBlobSize),
  };

  if (FD->PWrite(&ScratchHeader, sizeof(ScratchHeader), WriteOffset) != sizeof(ScratchHeader)) {
    return false;
  }
  WriteOffset += sizeof(ScratchHeader);

  OutBlobOffset = WriteOffset;

  for (const std::span<const uint8_t>& Chunk : BlobChunks) {
    if (Chunk.empty()) {
      continue;
    }
    if (FD->PWrite(Chunk.data(), Chunk.size(), WriteOffset) != static_cast<ssize_t>(Chunk.size())) {
      return false;
    }
    WriteOffset += Chunk.size();
  }

  return true;
}

bool IndexedDB::Open(const fextl::string& CacheDBName, bool ReadOnly) {
  if (!CacheFOZ.Open(CacheDBName + ".foz", ReadOnly)) {
    return false;
  }
  if (!IndexFOZ.Open(CacheDBName + "_idx.foz", ReadOnly)) {
    return false;
  }

  const File::File::FileHandleType CacheFileHandle = CacheFOZ.GetHandle();
  if (FileMapper && CacheFileHandle != static_cast<File::File::FileHandleType>(-1)) {
    CacheFileMapping = reinterpret_cast<uint8_t*>(FileMapper(CacheFileHandle, ReadOnly ? CacheFOZ.Size() : BIG_MAPPING_SIZE));
    CacheFileSize = CacheFOZ.Size();
  }

  this->ReadOnly = ReadOnly;
  return true;
}

static uint64_t ReferencedCacheFileEnd(const DiskCacheIndexFile::ParseResult& Parsed) {
  uint64_t End = FOZ_REF_MAGIC_SIZE;
  for (const auto& Record : Parsed.Records) {
    const uint64_t RecordEnd = Record.CacheFileOffset + Record.Size;
    if (RecordEnd > End) {
      End = RecordEnd;
    }
  }
  return End;
}

void IndexedDB::PopulateIndex(Index& CacheIndex, bool& FoundMetadata) {
  fextl::vector<uint8_t> Data;
  if (!IndexFOZ.ReadAll(Data)) {
    return;
  }

  const ssize_t CacheFOZSize = CacheFOZ.Size();
  if (CacheFOZSize < 0) {
    return;
  }

  const auto Parsed = DiskCacheIndexFile::Parse(Data, static_cast<uint64_t>(CacheFOZSize));
  for (const auto& Record : Parsed.Records) {
    if (Record.Metadata) {
      FoundMetadata = true;
    } else {
      CacheIndex.insert({Record.Hash, {this, Record.CacheFileOffset, Record.Size}});
    }
  }

  ObservedIndexFileSize = FOZ_REF_MAGIC_SIZE + Data.size();
  CacheFileSize = static_cast<uint64_t>(CacheFOZSize);
  if (!ReadOnly) {
    if (Parsed.State == DiskCacheIndexFile::ParseState::IncompleteSuffix) {
      IndexAppendOffset = FOZ_REF_MAGIC_SIZE + Parsed.ValidSize;
    } else {
      IndexAppendOffset.reset();
    }
    const uint64_t ReferencedEnd = ReferencedCacheFileEnd(Parsed);
    if (ReferencedEnd < static_cast<uint64_t>(CacheFOZSize)) {
      CacheAppendOffset = ReferencedEnd;
    } else {
      CacheAppendOffset.reset();
    }
  }
}

bool IndexedDB::ReadCacheBlob(uint64_t Offset, std::span<uint8_t> OutBlob) {
  if (CacheFileMapping && (ReadOnly || Offset + OutBlob.size() <= BIG_MAPPING_SIZE)) {
    if (Offset + OutBlob.size() > CacheFileSize) {
      return false;
    }
    memcpy(OutBlob.data(), CacheFileMapping + Offset, OutBlob.size());
    return true;
  }
  return CacheFOZ.ReadBlob(Offset, OutBlob);
}

bool IndexedDB::StoreCacheBlob(const MesaFOZ::foz_payload_key& Key, std::span<const uint8_t> Blob, Index& CacheIndex,
                               std::mutex& IndexMutex) {
  if (ReadOnly) {
    return false;
  }
  uint64_t Hash {};
  if (Key.bytes[39] != 0xFF) {
    const auto* KeyBegin = reinterpret_cast<const char*>(Key.bytes);
    const auto* KeyEnd = KeyBegin + 16;
    const auto ParsedKey = std::from_chars(KeyBegin, KeyEnd, Hash, 16);
    if (ParsedKey.ec != std::errc {} || ParsedKey.ptr != KeyEnd) {
      return false;
    }
  } else {
    Hash = ~0ULL;
  }
  {
    std::lock_guard Guard(IndexMutex);
    if (CacheIndex.contains(Hash)) {
      return true;
    }
  }

  if (!CacheFOZ.Lock(STORE_LOCK_TIMEOUT_MS) || !IndexFOZ.Lock(STORE_LOCK_TIMEOUT_MS)) {
    CacheFOZ.Unlock();
    IndexFOZ.Unlock();
    return false;
  }

  const ssize_t CurrentIndexFileSize = IndexFOZ.Size();
  const ssize_t CurrentCacheFileSize = CacheFOZ.Size();
  if (CurrentIndexFileSize < 0 || CurrentCacheFileSize < 0) {
    CacheFOZ.Unlock();
    IndexFOZ.Unlock();
    return false;
  }
  if (IndexAppendOffset || CacheAppendOffset || static_cast<uint64_t>(CurrentIndexFileSize) != ObservedIndexFileSize ||
      static_cast<uint64_t>(CurrentCacheFileSize) != CacheFileSize) {
    fextl::vector<uint8_t> IndexData;
    if (!IndexFOZ.ReadAll(IndexData)) {
      CacheFOZ.Unlock();
      IndexFOZ.Unlock();
      return false;
    }
    const auto Parsed = DiskCacheIndexFile::Parse(IndexData, static_cast<uint64_t>(CurrentCacheFileSize));
    ObservedIndexFileSize = FOZ_REF_MAGIC_SIZE + IndexData.size();
    if (Parsed.State == DiskCacheIndexFile::ParseState::IncompleteSuffix) {
      IndexAppendOffset = FOZ_REF_MAGIC_SIZE + Parsed.ValidSize;
    } else {
      IndexAppendOffset.reset();
    }
    const uint64_t ReferencedEnd = ReferencedCacheFileEnd(Parsed);
    if (ReferencedEnd < static_cast<uint64_t>(CurrentCacheFileSize)) {
      CacheAppendOffset = ReferencedEnd;
    } else {
      CacheAppendOffset.reset();
    }
  }

  if (CacheAppendOffset) {
    if (!CacheFOZ.Truncate(*CacheAppendOffset)) {
      CacheFOZ.Unlock();
      IndexFOZ.Unlock();
      return false;
    }
    CacheFileSize = *CacheAppendOffset;
    CacheAppendOffset.reset();
  }

  std::span<const uint8_t> BlobChunks[] = {Blob};
  uint64_t BlobOffset = 0;
  if (!CacheFOZ.WriteBlob(Key, BlobChunks, BlobOffset)) {
    CacheFOZ.Unlock();
    IndexFOZ.Unlock();
    return false;
  }

  MesaFOZ::mesa_index_db_file_entry IndexEntry {
    .hash = Hash,
    .size = static_cast<uint32_t>(Blob.size()),
    .last_access_time = 0,
    .cache_db_file_offset = BlobOffset,
  };

  std::span<const uint8_t> IndexBlobChunks[] = {{reinterpret_cast<const uint8_t*>(&IndexEntry), sizeof(IndexEntry)}};
  uint64_t UnusedIndexBlobOffset = 0;
  if (!IndexFOZ.WriteBlob(Key, IndexBlobChunks, UnusedIndexBlobOffset, IndexAppendOffset)) {
    CacheFOZ.Unlock();
    IndexFOZ.Unlock();
    return false;
  }

  const uint64_t IndexRecordEnd = UnusedIndexBlobOffset + sizeof(IndexEntry);
  const ssize_t NewIndexFileSize = IndexFOZ.Size();
  if (NewIndexFileSize < 0) {
    CacheFOZ.Unlock();
    IndexFOZ.Unlock();
    return false;
  }
  ObservedIndexFileSize = static_cast<uint64_t>(NewIndexFileSize);
  if (IndexRecordEnd < ObservedIndexFileSize) {
    IndexAppendOffset = IndexRecordEnd;
  } else {
    IndexAppendOffset.reset();
  }

  CacheFOZ.Unlock();
  IndexFOZ.Unlock();

  if (CacheFileMapping) {
    CacheFileSize = BlobOffset + Blob.size();
  }

  std::lock_guard Guard(IndexMutex);
  CacheIndex[Hash] = {this, BlobOffset, static_cast<uint32_t>(Blob.size())};
  return true;
}

FEX_DEFAULT_VISIBILITY void SetFileMapper(FileMapperFunc Func) {
  FileMapper = Func;
}

} // namespace FEXCore::DiskCache
