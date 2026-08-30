// SPDX-License-Identifier: MIT

#include <FEXCore/Core/DiskCacheIndexFile.h>

#include <charconv>
#include <cstring>
#include <type_traits>
#include <utility>

namespace FEXCore::DiskCacheIndexFile {
namespace {

template<typename T>
T Read(std::span<const uint8_t> Data, size_t Offset) {
  static_assert(std::is_trivially_copyable_v<T>);
  T Result;
  std::memcpy(&Result, Data.data() + Offset, sizeof(Result));
  return Result;
}

ParseResult Incomplete(ParseResult&& Result, size_t RecordOffset) {
  Result.ValidSize = RecordOffset;
  Result.State = ParseState::IncompleteSuffix;
  return std::move(Result);
}

} // namespace

ParseResult Parse(std::span<const uint8_t> Data, uint64_t CacheFileSize) {
  ParseResult Result {
    .ValidSize = 0,
    .State = ParseState::Complete,
  };

  size_t Offset = 0;
  while (Offset < Data.size()) {
    const size_t RecordOffset = Offset;
    constexpr size_t PrefixSize = sizeof(DiskCache::MesaFOZ::foz_payload_key) + sizeof(DiskCache::MesaFOZ::foz_payload_header);
    if (Data.size() - Offset < PrefixSize) {
      return Incomplete(std::move(Result), RecordOffset);
    }

    const auto Key = Read<DiskCache::MesaFOZ::foz_payload_key>(Data, Offset);
    Offset += sizeof(Key);
    const auto Header = Read<DiskCache::MesaFOZ::foz_payload_header>(Data, Offset);
    Offset += sizeof(Header);

    if (Header.payload_size != sizeof(DiskCache::MesaFOZ::mesa_index_db_file_entry) ||
        Data.size() - Offset < Header.payload_size) {
      return Incomplete(std::move(Result), RecordOffset);
    }

    const auto Entry = Read<DiskCache::MesaFOZ::mesa_index_db_file_entry>(Data, Offset);
    Offset += Header.payload_size;
    Result.ValidSize = Offset;

    if (Entry.cache_db_file_offset > CacheFileSize || Entry.size > CacheFileSize - Entry.cache_db_file_offset) {
      continue;
    }

    if (Key.bytes[39] == 0xFF) {
      Result.Records.push_back({
        .Hash = ~0ULL,
        .Size = Entry.size,
        .CacheFileOffset = Entry.cache_db_file_offset,
        .Metadata = true,
      });
      continue;
    }

    uint64_t ParsedKey {};
    const auto* KeyBegin = reinterpret_cast<const char*>(Key.bytes);
    const auto* KeyEnd = KeyBegin + 16;
    const auto Parse = std::from_chars(KeyBegin, KeyEnd, ParsedKey, 16);
    if (Parse.ec != std::errc {} || Parse.ptr != KeyEnd || Entry.hash != ParsedKey) {
      continue;
    }

    Result.Records.push_back({
      .Hash = Entry.hash,
      .Size = Entry.size,
      .CacheFileOffset = Entry.cache_db_file_offset,
      .Metadata = false,
    });
  }

  return Result;
}

} // namespace FEXCore::DiskCacheIndexFile
