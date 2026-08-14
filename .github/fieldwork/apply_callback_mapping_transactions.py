#!/usr/bin/env python3
from pathlib import Path
import sys


def function_extent(text: str, needle: str) -> tuple[int, int]:
    start = text.index(needle)
    brace = text.index('{', start)
    depth = 0
    i = brace
    state = 'code'
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ''
        if state == 'code':
            if c == '/' and n == '/':
                state = 'line_comment'
                i += 2
                continue
            if c == '/' and n == '*':
                state = 'block_comment'
                i += 2
                continue
            if c == '"':
                state = 'string'
                i += 1
                continue
            if c == "'":
                state = 'char'
                i += 1
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    while end < len(text) and text[end] == '\n':
                        end += 1
                    return start, end
            i += 1
            continue
        if state == 'line_comment':
            if c == '\n':
                state = 'code'
            i += 1
            continue
        if state == 'block_comment':
            if c == '*' and n == '/':
                state = 'code'
                i += 2
            else:
                i += 1
            continue
        if state in ('string', 'char'):
            quote = '"' if state == 'string' else "'"
            if c == '\\':
                i += 2
                continue
            if c == quote:
                state = 'code'
            i += 1
            continue
    raise RuntimeError(f'unterminated function containing {needle!r}')


def replace_function(text: str, needle: str, replacement: str) -> str:
    start, end = function_extent(text, needle)
    return text[:start] + replacement.rstrip() + '\n\n' + text[end:]


def patch_smc(path: Path) -> None:
    text = path.read_text()

    mmap_replacement = r'''void* SyscallHandler::GuestMmap(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, void* addr, size_t length, int prot, int flags,
                                int fd, off_t offset) {
  LOGMAN_THROW_A_FMT(Is64Bit || (length >> 32) == 0, "values must fit to 32 bits");

  uint64_t Result {};
  size_t Size = FEXCore::AlignUp(length, FEXCore::Utils::FEX_PAGE_SIZE);
  std::optional<LateApplyExtendedVolatileMetadata> LateMetadata = std::nullopt;

  std::optional<FEXCore::ExecutableFileSectionInfo> CachedSection;
  bool PendingResourceDeletion {};
  bool MmapFailed = false;

  // MAP_FIXED can destroy an existing callback generation at the requested
  // address. Drain before entering the fallible host operation, then commit
  // only if mmap actually succeeds. MAP_FIXED_NOREPLACE never clobbers an
  // existing mapping and therefore needs no retirement transaction.
  auto* Thunks = (Thread && Size && (flags & MAP_FIXED)) ? GetThunkHandler() : nullptr;
  const auto ReplacementBase = reinterpret_cast<uintptr_t>(addr);
  if (Thunks) {
    Thunks->BeginGuestRangeRetirement(Thread, ReplacementBase, Size);
  }

  {
    // BeginGuestRangeRetirement may wait for active callbacks. Keep that wait
    // outside VMATracking.Mutex so callbacks can complete mapping syscalls.
    auto lk = FEXCore::GuardSignalDeferringSectionWithFallback(VMATracking.Mutex, Thread);

    bool Map32Bit = !Is64Bit || (flags & FEX::HLE::X86_64_MAP_32BIT);
    if (Map32Bit) {
      Result = (uint64_t)Get32BitAllocator()->Mmap((void*)addr, length, prot, flags, fd, offset);
      if (FEX::HLE::HasSyscallError(Result)) {
        MmapFailed = true;
      } else {
        LOGMAN_THROW_A_FMT(Is64Bit || (Result >> 32) == 0 || (Result >> 32) == 0xFFFFFFFF, "values must fit to 32 bits");
      }
    } else {
      Result = reinterpret_cast<uint64_t>(::mmap(reinterpret_cast<void*>(addr), length, prot, flags, fd, offset));
      if (Result == ~0ULL) {
        Result = -errno;
        MmapFailed = true;
      }
    }

    if (!MmapFailed) {
      LateMetadata = TrackMmap(Thread, Result, length, prot, flags, fd, offset, CachedSection);
      PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();
    }
  }

  if (MmapFailed) {
    if (Thunks) {
      Thunks->RollbackGuestRangeRetirement(ReplacementBase, Size);
    }
    return reinterpret_cast<void*>(Result);
  }

  if (Thunks) {
    Thunks->CommitGuestRangeRetirement(Thread, ReplacementBase, Size);
  }

  InvalidateCodeRangeIfNecessary(Thread, Result, Size, PendingResourceDeletion);

  if (LateMetadata) {
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), Thread);
    CTX->AddForceTSOInformation(LateMetadata->VolatileValidRanges, std::move(LateMetadata->VolatileInstructions));
  }

  if (EnableCodeCaching && CachedSection) {
    Thread->CTX->GetCodeCache().EnableLoadedSection(
      Thread, *static_cast<const VMATracking::ExecutableFileState&>(CachedSection->FileInfo).MappedCache, *CachedSection);
  }

  return reinterpret_cast<void*>(Result);
}'''
    text = replace_function(text, 'void* SyscallHandler::GuestMmap(', mmap_replacement)

    mremap_replacement = r'''uint64_t SyscallHandler::GuestMremap(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, void* old_address, size_t old_size,
                                     size_t new_size, int flags, void* new_address) {
  uint64_t Result {};
  bool MremapFailed = false;

  const uintptr_t OldBase = reinterpret_cast<uintptr_t>(old_address);
  const uintptr_t NewBase = reinterpret_cast<uintptr_t>(new_address);
  const size_t OldSize = FEXCore::AlignUp(old_size, FEXCore::Utils::FEX_PAGE_SIZE);
  const size_t NewSize = FEXCore::AlignUp(new_size, FEXCore::Utils::FEX_PAGE_SIZE);
  const bool MayMove = flags & MREMAP_MAYMOVE;
  const bool Fixed = flags & MREMAP_FIXED;
#ifdef MREMAP_DONTUNMAP
  const bool DontUnmap = flags & MREMAP_DONTUNMAP;
#else
  const bool DontUnmap = false;
#endif

  auto* Thunks = Thread ? GetThunkHandler() : nullptr;
  const bool DrainPotentialSource = Thunks && OldSize && (MayMove || Fixed || DontUnmap);
  const bool DrainTail = Thunks && OldSize > NewSize;
  const uintptr_t TailBase = OldBase + NewSize;
  const size_t TailSize = DrainTail ? OldSize - NewSize : 0;
  const bool DrainFixedDestination = Thunks && Fixed && NewSize;

  // A MAYMOVE operation can retire the whole source mapping, but we only know
  // whether it moved after the host call. Drain it speculatively and roll it
  // back when the kernel grows/shrinks in place. A shrink tail is a separate
  // nested drain so preserved prefix callbacks can return to Live.
  if (DrainPotentialSource) {
    Thunks->BeginGuestRangeRetirement(Thread, OldBase, OldSize);
  }
  if (DrainTail) {
    Thunks->BeginGuestRangeRetirement(Thread, TailBase, TailSize);
  }
  if (DrainFixedDestination) {
    Thunks->BeginGuestRangeRetirement(Thread, NewBase, NewSize);
  }

  {
    auto lk = FEXCore::GuardSignalDeferringSection(VMATracking.Mutex, Thread);
    if (Is64Bit) {
      Result = reinterpret_cast<uint64_t>(::mremap(old_address, old_size, new_size, flags, new_address));
      if (Result == ~0ULL) {
        Result = -errno;
        MremapFailed = true;
      }
    } else {
      Result = reinterpret_cast<uint64_t>(Get32BitAllocator()->Mremap(old_address, old_size, new_size, flags, new_address));
      if (FEX::HLE::HasSyscallError(Result)) {
        MremapFailed = true;
      }
    }

    if (!MremapFailed) {
      TrackMremap(Thread, OldBase, old_size, new_size, flags, Result);
    }
  }

  if (MremapFailed) {
    if (DrainFixedDestination) {
      Thunks->RollbackGuestRangeRetirement(NewBase, NewSize);
    }
    if (DrainTail) {
      Thunks->RollbackGuestRangeRetirement(TailBase, TailSize);
    }
    if (DrainPotentialSource) {
      Thunks->RollbackGuestRangeRetirement(OldBase, OldSize);
    }
    return Result;
  }

  if (DrainPotentialSource) {
    const bool SourceGenerationRetired = DontUnmap || Result != OldBase;
    if (SourceGenerationRetired) {
      Thunks->CommitGuestRangeRetirement(Thread, OldBase, OldSize);
    } else {
      Thunks->RollbackGuestRangeRetirement(OldBase, OldSize);
    }
  }
  if (DrainTail) {
    Thunks->CommitGuestRangeRetirement(Thread, TailBase, TailSize);
  }
  if (DrainFixedDestination) {
    Thunks->CommitGuestRangeRetirement(Thread, NewBase, NewSize);
  }

  InvalidateCodeRangeIfNecessaryOnRemap(Thread, OldBase, Result, old_size, new_size);
  return Result;
}'''
    text = replace_function(text, 'uint64_t SyscallHandler::GuestMremap(', mremap_replacement)

    shmat_replacement = r'''uint64_t SyscallHandler::GuestShmat(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, int shmid, const void* shmaddr, int shmflg) {
  uint64_t Result {};
  uint64_t Length {};
  bool PendingResourceDeletion {};
  bool ShmatFailed = false;

  auto* Thunks = Thread ? GetThunkHandler() : nullptr;
  const bool ReplacesExisting = Thunks && shmaddr && (shmflg & SHM_REMAP);
  uintptr_t ReplacementBase = reinterpret_cast<uintptr_t>(shmaddr);
  size_t ReplacementSize {};

  if (ReplacesExisting) {
    shmid_ds stat {};
    if (shmctl(shmid, IPC_STAT, &stat) == -1) {
      return -errno;
    }
    if (shmflg & SHM_RND) {
      ReplacementBase &= ~(static_cast<uintptr_t>(FEXCore::Utils::FEX_PAGE_SIZE) - 1);
    }
    ReplacementSize = FEXCore::AlignUp(static_cast<size_t>(stat.shm_segsz), FEXCore::Utils::FEX_PAGE_SIZE);
    if (ReplacementSize) {
      Thunks->BeginGuestRangeRetirement(Thread, ReplacementBase, ReplacementSize);
    }
  }

  {
    auto lk = FEXCore::GuardSignalDeferringSection(VMATracking.Mutex, Thread);
    if (Is64Bit) {
      Result = reinterpret_cast<uint64_t>(::shmat(shmid, shmaddr, shmflg));
      if (Result == ~0ULL) {
        Result = -errno;
        ShmatFailed = true;
      }
    } else {
      uint32_t Addr {};
      Result = Get32BitAllocator()->Shmat(shmid, shmaddr, shmflg, &Addr);
      if (FEX::HLE::HasSyscallError(Result)) {
        ShmatFailed = true;
      } else {
        Result = Addr;
      }
    }

    if (!ShmatFailed) {
      shmid_ds stat {};
      auto res = shmctl(shmid, IPC_STAT, &stat);
      LOGMAN_THROW_A_FMT(res != -1, "shmctl IPC_STAT failed");

      Length = stat.shm_segsz;
      TrackShmat(Thread, shmid, Result, shmflg, Length);
      PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();
    }
  }

  if (ShmatFailed) {
    if (ReplacesExisting && ReplacementSize) {
      Thunks->RollbackGuestRangeRetirement(ReplacementBase, ReplacementSize);
    }
    return Result;
  }

  if (ReplacesExisting && ReplacementSize) {
    Thunks->CommitGuestRangeRetirement(Thread, ReplacementBase, ReplacementSize);
  }

  InvalidateCodeRangeIfNecessary(Thread, Result, Length, PendingResourceDeletion);
  return Result;
}'''
    text = replace_function(text, 'uint64_t SyscallHandler::GuestShmat(', shmat_replacement)

    shmdt_replacement = r'''uint64_t SyscallHandler::GuestShmdt(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, const void* shmaddr) {
  const uintptr_t Base = reinterpret_cast<uintptr_t>(shmaddr);
  auto* Thunks = Thread ? GetThunkHandler() : nullptr;

  for (;;) {
    uint64_t PreflightLength {};
    if (Thunks) {
      {
        auto lk = FEXCore::GuardSignalDeferringSectionWithFallback(VMATracking.Mutex, Thread);
        PreflightLength = VMATracking.GetSHMRegionSize(Base);
      }
      if (PreflightLength) {
        const auto RetireSize = FEXCore::AlignUp(PreflightLength, FEXCore::Utils::FEX_PAGE_SIZE);
        Thunks->BeginGuestRangeRetirement(Thread, Base, RetireSize);
      }
    }

    uint64_t Result {};
    uint64_t Length {};
    bool PendingResourceDeletion {};
    bool ShmdtFailed = false;
    bool Retry = false;

    {
      auto lk = FEXCore::GuardSignalDeferringSection(VMATracking.Mutex, Thread);

      // The drain wait happens outside VMATracking.Mutex. Revalidate that the
      // tracked SysV attachment still covers the same range before detaching;
      // another guest mapping syscall may have changed it while we waited.
      if (Thunks && PreflightLength && VMATracking.GetSHMRegionSize(Base) != PreflightLength) {
        Retry = true;
      } else {
        if (Is64Bit) {
          Result = ::shmdt(shmaddr);
          if (Result == static_cast<uint64_t>(-1)) {
            Result = -errno;
            ShmdtFailed = true;
          }
        } else {
          Result = Get32BitAllocator()->Shmdt(shmaddr);
          if (FEX::HLE::HasSyscallError(Result)) {
            ShmdtFailed = true;
          }
        }

        if (!ShmdtFailed) {
          Length = TrackShmdt(Thread, Base);
          PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();
        }
      }
    }

    if (Retry) {
      const auto RetireSize = FEXCore::AlignUp(PreflightLength, FEXCore::Utils::FEX_PAGE_SIZE);
      Thunks->RollbackGuestRangeRetirement(Base, RetireSize);
      continue;
    }

    if (ShmdtFailed) {
      if (Thunks && PreflightLength) {
        const auto RetireSize = FEXCore::AlignUp(PreflightLength, FEXCore::Utils::FEX_PAGE_SIZE);
        Thunks->RollbackGuestRangeRetirement(Base, RetireSize);
      }
      return Result;
    }

    if (Thunks && PreflightLength) {
      const auto RetireSize = FEXCore::AlignUp(PreflightLength, FEXCore::Utils::FEX_PAGE_SIZE);
      Thunks->CommitGuestRangeRetirement(Thread, Base, RetireSize);
    }

    InvalidateCodeRangeIfNecessary(Thread, Base, Length, PendingResourceDeletion);
    return Result;
  }
}'''
    text = replace_function(text, 'uint64_t SyscallHandler::GuestShmdt(', shmdt_replacement)

    path.write_text(text)


def patch_vma_header(path: Path) -> None:
    text = path.read_text()
    anchor = '''  // Deletes the SHM region mapped at Base from tracking.\n  // Matches `shmdt` semantics.\n  // - Mutex must be unique_locked before calling\n  // Returns the Size of the Shm or 0 if not found\n  uintptr_t DeleteSHMRegion(FEXCore::Context::Context* Ctx, uintptr_t Base);\n'''
    replacement = '''  // Returns the size of the SHM region attached at Base, or 0 if no matching\n  // SysV attachment is tracked.\n  // - Mutex must be locked before calling\n  uintptr_t GetSHMRegionSize(uintptr_t Base) const;\n\n''' + anchor
    if text.count(anchor) != 1:
        raise RuntimeError('DeleteSHMRegion declaration anchor mismatch')
    path.write_text(text.replace(anchor, replacement, 1))


def patch_vma_cpp(path: Path) -> None:
    text = path.read_text()
    anchor = '''// This matches the peculiarities algorithm used in linux ksys_shmdt (linux kernel 5.16, ipc/shm.c)\nuintptr_t VMATracking::DeleteSHMRegion(FEXCore::Context::Context* CTX, uintptr_t Base) {\n'''
    helper = r'''uintptr_t VMATracking::GetSHMRegionSize(uintptr_t Base) const {
  auto Entry = VMAs.lower_bound(Base);

  for (; Entry != VMAs.end(); ++Entry) {
    LOGMAN_THROW_A_FMT(Entry->second.Base >= Base, "VMA tracking corruption");
    if (Entry->second.Base - Base == Entry->second.Offset && Entry->second.Resource &&
        Entry->second.Resource->Iterator->first.dev == SpecialDev::SHM) {
      return Entry->second.Resource->Iterator->second.Length;
    }
  }

  return 0;
}

// This matches the peculiarities algorithm used in linux ksys_shmdt (linux kernel 5.16, ipc/shm.c)
uintptr_t VMATracking::DeleteSHMRegion(FEXCore::Context::Context* CTX, uintptr_t Base) {
'''
    if text.count(anchor) != 1:
        raise RuntimeError('DeleteSHMRegion implementation anchor mismatch')
    path.write_text(text.replace(anchor, helper, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f'usage: {sys.argv[0]} FEX_SOURCE_ROOT')
    root = Path(sys.argv[1]).resolve()
    patch_smc(root / 'Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp')
    patch_vma_header(root / 'Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsVMATracking.h')
    patch_vma_cpp(root / 'Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsVMATracking.cpp')
    print('Applied callback retirement transactions to mmap/mremap/SysV mapping mutation paths')


if __name__ == '__main__':
    main()
