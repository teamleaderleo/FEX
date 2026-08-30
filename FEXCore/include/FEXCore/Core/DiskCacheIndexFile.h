// SPDX-License-Identifier: MIT
#pragma once

#include <FEXCore/Utils/CompilerDefs.h>

#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace FEXCore::DiskCache::MesaFOZ {

inline constexpr size_t FOSSILIZE_BLOB_HASH_LENGTH = 40;

struct __attribute__((packed)) foz_payload_key {
  uint8_t bytes[FOSSILIZE_BLOB_HASH_LENGTH];
};

struct __attribute__((packed)) foz_payload_header {
  uint32_t payload_size;
  uint32_t format;
  uint32_t crc;
  uint32_t uncompressed_size;
};

struct __attribute__((packed)) mesa_index_db_file_entry {
  uint64_t hash;
  uint32_t size;
  uint64_t last_access_time;
  uint64_t cache_db_file_offset;
};

static_assert(sizeof(foz_payload_key) == 40, "Breaking change in Fossilize key layout");
static_assert(sizeof(foz_payload_header) == 16, "Breaking change in Fossilize payload-header layout");
static_assert(sizeof(mesa_index_db_file_entry) == 28, "Breaking change in Fossilize index-entry layout");

} // namespace FEXCore::DiskCache::MesaFOZ

namespace FEXCore::DiskCacheIndexFile {

struct Record {
  uint64_t Hash;
  uint32_t Size;
  uint64_t CacheFileOffset;
  bool Metadata;
};

enum class ParseState {
  Complete,
  IncompleteSuffix,
};

struct ParseResult {
  std::vector<Record> Records;
  size_t ValidSize;
  ParseState State;
};

FEX_DEFAULT_VISIBILITY ParseResult Parse(std::span<const uint8_t> Data, uint64_t CacheFileSize);

} // namespace FEXCore::DiskCacheIndexFile
