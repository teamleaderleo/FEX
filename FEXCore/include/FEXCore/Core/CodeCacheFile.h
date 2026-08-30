// SPDX-License-Identifier: MIT
#pragma once

#include <FEXCore/Utils/CompilerDefs.h>
#include <FEXCore/Utils/TypeDefines.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>

namespace FEXCore::CodeCacheFile {

struct Header {
  std::array<char, 4> Magic = ExpectedMagic;
  // Version history:
  // 1: Initial version
  // 2: Padding code buffer data to enable direct mapping
  // 3: Bind contents to the effective code-generation configuration
  uint32_t FormatVersion = ExpectedFormatVersion;
  std::array<uint8_t, 20> FEXVersion {};
  uint64_t ConfigId {};
  uint32_t NumBlocks {};
  uint32_t NumCodePages {};
  uint32_t CodeBufferSize {};
  uint32_t NumRelocations {};
  uint32_t padding {};
  uint64_t SerializedBaseAddress {};

  static constexpr std::array<char, 4> ExpectedMagic = {'F', 'X', 'C', 'C'};
  static constexpr uint32_t ExpectedFormatVersion = 3;
};

static_assert(sizeof(Header) == 72, "Breaking change in code cache header layout");

struct Layout {
  Header FileHeader;
  size_t BlockListOffset;
  size_t RelocationsOffset;
  size_t CodeBufferOffset;
  size_t CodePagesOffset;
};

enum class ValidationError {
  None,
  FileTooLarge,
  TruncatedHeader,
  InvalidMagic,
  UnsupportedFormat,
  FEXVersionMismatch,
  ConfigMismatch,
  EmptyBlockList,
  InvalidCodeBufferSize,
  TruncatedBlockList,
  UnsortedBlockList,
  HostCodeOutOfRange,
  UnsortedBlockCodePages,
  MisalignedRelocations,
  TruncatedRelocations,
  InvalidRelocationType,
  RelocationOutOfRange,
  UnsortedRelocations,
  TruncatedCodeBuffer,
  TruncatedCodePages,
  UnsortedCodePages,
  TrailingData,
};

struct ValidationResult {
  std::optional<Layout> Parsed;
  ValidationError Error;
};

FEX_DEFAULT_VISIBILITY ValidationResult Validate(std::span<const std::byte> CacheFile,
                                                 std::span<const uint8_t, 20> ExpectedFEXVersion,
                                                 uint64_t ExpectedConfigId);
FEX_DEFAULT_VISIBILITY std::string_view ToString(ValidationError Error);

} // namespace FEXCore::CodeCacheFile
