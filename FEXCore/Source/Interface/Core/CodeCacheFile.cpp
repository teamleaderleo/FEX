// SPDX-License-Identifier: MIT

#include <FEXCore/Core/CodeCacheFile.h>
#include <FEXCore/Utils/MathUtils.h>

#include <Interface/Core/JIT/Relocations.h>

#include <algorithm>
#include <cstring>
#include <limits>
#include <type_traits>

namespace FEXCore::CodeCacheFile {
namespace {

template<typename T>
T Read(std::span<const std::byte> Data, size_t Offset) {
  static_assert(std::is_trivially_copyable_v<T>);
  T Result;
  std::memcpy(&Result, Data.data() + Offset, sizeof(Result));
  return Result;
}

bool Consume(std::span<const std::byte> Data, size_t& Offset, size_t Size) {
  if (Offset > Data.size_bytes() || Size > Data.size_bytes() - Offset) {
    return false;
  }
  Offset += Size;
  return true;
}

bool ConsumeArray(std::span<const std::byte> Data, size_t& Offset, uint64_t Count, size_t ElementSize) {
  if (Offset > Data.size_bytes() || Count > (Data.size_bytes() - Offset) / ElementSize) {
    return false;
  }
  return Consume(Data, Offset, static_cast<size_t>(Count) * ElementSize);
}

ValidationResult Failure(ValidationError Error) {
  return {.Parsed = std::nullopt, .Error = Error};
}

size_t RelocationWidth(CPU::RelocationTypes Type) {
  switch (Type) {
  case CPU::RelocationTypes::RELOC_NAMED_SYMBOL_LITERAL:
  case CPU::RelocationTypes::RELOC_GUEST_RIP_LITERAL: return 8;
  case CPU::RelocationTypes::RELOC_NAMED_THUNK_MOVE:
  case CPU::RelocationTypes::RELOC_GUEST_RIP_MOVE: return 16;
  }
  return 0;
}

} // namespace

ValidationResult Validate(std::span<const std::byte> CacheFile, std::span<const uint8_t, 20> ExpectedFEXVersion,
                          uint64_t ExpectedConfigId) {
  if (CacheFile.size_bytes() > std::numeric_limits<uint32_t>::max()) {
    // MappedCodeCacheFile stores relocation file offsets as uint32_t.
    return Failure(ValidationError::FileTooLarge);
  }
  if (CacheFile.size_bytes() < sizeof(Header)) {
    return Failure(ValidationError::TruncatedHeader);
  }

  const Header FileHeader = Read<Header>(CacheFile, 0);
  if (FileHeader.Magic != Header::ExpectedMagic) {
    return Failure(ValidationError::InvalidMagic);
  }
  if (FileHeader.FormatVersion != Header::ExpectedFormatVersion) {
    return Failure(ValidationError::UnsupportedFormat);
  }
  if (!std::ranges::equal(FileHeader.FEXVersion, ExpectedFEXVersion)) {
    return Failure(ValidationError::FEXVersionMismatch);
  }
  if (FileHeader.ConfigId != ExpectedConfigId) {
    return Failure(ValidationError::ConfigMismatch);
  }
  if (FileHeader.NumBlocks == 0) {
    return Failure(ValidationError::EmptyBlockList);
  }
  if (FileHeader.CodeBufferSize == 0 || FileHeader.CodeBufferSize % Utils::FEX_PAGE_SIZE != 0) {
    return Failure(ValidationError::InvalidCodeBufferSize);
  }

  size_t Offset = sizeof(Header);
  const size_t BlockListOffset = Offset;
  std::optional<uint64_t> PreviousGuest;
  for (uint32_t Block = 0; Block < FileHeader.NumBlocks; ++Block) {
    constexpr size_t FixedBlockSize = 3 * sizeof(uint64_t);
    if (!Consume(CacheFile, Offset, FixedBlockSize)) {
      return Failure(ValidationError::TruncatedBlockList);
    }
    const size_t BlockOffset = Offset - FixedBlockSize;
    const uint64_t Guest = Read<uint64_t>(CacheFile, BlockOffset);
    const uint64_t Host = Read<uint64_t>(CacheFile, BlockOffset + sizeof(uint64_t));
    const uint64_t NumGuestCodePages = Read<uint64_t>(CacheFile, BlockOffset + 2 * sizeof(uint64_t));
    if (PreviousGuest && Guest <= *PreviousGuest) {
      return Failure(ValidationError::UnsortedBlockList);
    }
    PreviousGuest = Guest;
    if (Host >= FileHeader.CodeBufferSize) {
      return Failure(ValidationError::HostCodeOutOfRange);
    }

    std::optional<uint64_t> PreviousCodePage;
    for (uint64_t Page = 0; Page < NumGuestCodePages; ++Page) {
      if (!Consume(CacheFile, Offset, sizeof(uint64_t))) {
        return Failure(ValidationError::TruncatedBlockList);
      }
      const uint64_t CodePage = Read<uint64_t>(CacheFile, Offset - sizeof(uint64_t));
      if (PreviousCodePage && CodePage < *PreviousCodePage) {
        return Failure(ValidationError::UnsortedBlockCodePages);
      }
      PreviousCodePage = CodePage;
    }
  }

  const size_t RelocationsOffset = Offset;
  if (reinterpret_cast<uintptr_t>(CacheFile.data() + Offset) % alignof(CPU::Relocation) != 0) {
    return Failure(ValidationError::MisalignedRelocations);
  }
  if (!ConsumeArray(CacheFile, Offset, FileHeader.NumRelocations, sizeof(CPU::Relocation))) {
    return Failure(ValidationError::TruncatedRelocations);
  }

  std::optional<uint64_t> PreviousRelocation;
  for (uint32_t Index = 0; Index < FileHeader.NumRelocations; ++Index) {
    const auto Relocation = Read<CPU::RelocationHeader>(CacheFile, RelocationsOffset + Index * sizeof(CPU::Relocation));
    const size_t Width = RelocationWidth(Relocation.Type);
    if (Width == 0) {
      return Failure(ValidationError::InvalidRelocationType);
    }
    if (Relocation.Offset > FileHeader.CodeBufferSize || Width > FileHeader.CodeBufferSize - Relocation.Offset) {
      return Failure(ValidationError::RelocationOutOfRange);
    }
    if (PreviousRelocation && Relocation.Offset < *PreviousRelocation) {
      return Failure(ValidationError::UnsortedRelocations);
    }
    PreviousRelocation = Relocation.Offset;
  }

  const size_t Padding = (Utils::FEX_PAGE_SIZE - Offset % Utils::FEX_PAGE_SIZE) % Utils::FEX_PAGE_SIZE;
  if (!Consume(CacheFile, Offset, Padding)) {
    return Failure(ValidationError::TruncatedCodeBuffer);
  }
  const size_t CodeBufferOffset = Offset;
  if (!Consume(CacheFile, Offset, FileHeader.CodeBufferSize)) {
    return Failure(ValidationError::TruncatedCodeBuffer);
  }

  const size_t CodePagesOffset = Offset;
  std::optional<uint64_t> PreviousCodePage;
  for (uint32_t Page = 0; Page < FileHeader.NumCodePages; ++Page) {
    constexpr size_t FixedPageSize = 2 * sizeof(uint64_t);
    if (!Consume(CacheFile, Offset, FixedPageSize)) {
      return Failure(ValidationError::TruncatedCodePages);
    }
    const size_t PageOffset = Offset - FixedPageSize;
    const uint64_t CodePage = Read<uint64_t>(CacheFile, PageOffset);
    const uint64_t NumEntrypoints = Read<uint64_t>(CacheFile, PageOffset + sizeof(uint64_t));
    if (PreviousCodePage && CodePage <= *PreviousCodePage) {
      return Failure(ValidationError::UnsortedCodePages);
    }
    PreviousCodePage = CodePage;
    if (!ConsumeArray(CacheFile, Offset, NumEntrypoints, sizeof(uint64_t))) {
      return Failure(ValidationError::TruncatedCodePages);
    }
  }

  if (Offset != CacheFile.size_bytes()) {
    // Windows reports the mapped view size, which may include the remainder
    // of the final host page beyond the file's logical EOF. Accept only that
    // bounded zero-filled mapping tail; no parsed span reaches it.
    const auto Tail = CacheFile.subspan(Offset);
    if (Tail.size_bytes() >= Utils::FEX_PAGE_SIZE || std::ranges::any_of(Tail, [](std::byte Byte) { return Byte != std::byte {}; })) {
      return Failure(ValidationError::TrailingData);
    }
  }

  return {
    .Parsed = Layout {
      .FileHeader = FileHeader,
      .BlockListOffset = BlockListOffset,
      .RelocationsOffset = RelocationsOffset,
      .CodeBufferOffset = CodeBufferOffset,
      .CodePagesOffset = CodePagesOffset,
    },
    .Error = ValidationError::None,
  };
}

std::string_view ToString(ValidationError Error) {
  switch (Error) {
  case ValidationError::None: return "none";
  case ValidationError::FileTooLarge: return "file exceeds addressable format";
  case ValidationError::TruncatedHeader: return "truncated header";
  case ValidationError::InvalidMagic: return "invalid magic";
  case ValidationError::UnsupportedFormat: return "unsupported format";
  case ValidationError::FEXVersionMismatch: return "FEX version mismatch";
  case ValidationError::ConfigMismatch: return "configuration mismatch";
  case ValidationError::EmptyBlockList: return "empty block list";
  case ValidationError::InvalidCodeBufferSize: return "invalid code buffer size";
  case ValidationError::TruncatedBlockList: return "truncated block list";
  case ValidationError::UnsortedBlockList: return "unsorted block list";
  case ValidationError::HostCodeOutOfRange: return "host code offset out of range";
  case ValidationError::UnsortedBlockCodePages: return "unsorted block code pages";
  case ValidationError::MisalignedRelocations: return "misaligned relocations";
  case ValidationError::TruncatedRelocations: return "truncated relocations";
  case ValidationError::InvalidRelocationType: return "invalid relocation type";
  case ValidationError::RelocationOutOfRange: return "relocation out of range";
  case ValidationError::UnsortedRelocations: return "unsorted relocations";
  case ValidationError::TruncatedCodeBuffer: return "truncated code buffer";
  case ValidationError::TruncatedCodePages: return "truncated code-page index";
  case ValidationError::UnsortedCodePages: return "unsorted code-page index";
  case ValidationError::TrailingData: return "nonzero or oversized trailing data";
  }
  return "unknown validation error";
}

} // namespace FEXCore::CodeCacheFile
