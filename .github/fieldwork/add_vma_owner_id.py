#!/usr/bin/env python3
from pathlib import Path
import sys


def repl(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1))


def replace_all(path: Path, old: str, new: str, expected: int, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} anchors in {path}, found {count}")
    path.write_text(text.replace(old, new))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()

    h = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsVMATracking.h"
    repl(
        h,
        '''  bool RequiresDelayedCacheLoad = false;
  fextl::vector<Elf64_Phdr> ProgramHeaders;''',
        '''  bool RequiresDelayedCacheLoad = false;
  fextl::vector<Elf64_Phdr> ProgramHeaders;

  // Non-reusable identity for this mapped-resource generation. All VMAs that
  // belong to one mapped instance share this value.
  uint64_t OwnerID {};''',
        'mapped-resource owner ID',
    )
    repl(
        h,
        '''  VMAFlags Flags;
  VMAProt Prot;
};''',
        '''  VMAFlags Flags;
  VMAProt Prot;

  // Mapping-generation identity. Private anonymous mappings have no Resource,
  // so the ID must also live directly on each VMA entry.
  uint64_t OwnerID {};
};''',
        'VMA-entry owner ID',
    )
    repl(
        h,
        '''  void TrackVMARange(FEXCore::Context::Context* Ctx, MappedResource* MappedResource, uintptr_t Base, uintptr_t Offset, uintptr_t Length,
                     VMAFlags Flags, VMAProt Prot);''',
        '''  void TrackVMARange(FEXCore::Context::Context* Ctx, MappedResource* MappedResource, uintptr_t Base, uintptr_t Offset, uintptr_t Length,
                     VMAFlags Flags, VMAProt Prot, uint64_t OwnerID = 0);''',
        'TrackVMARange owner parameter',
    )
    repl(
        h,
        '''private:
  MappedResource::ContainerType MappedResources;
  fextl::vector<MappedResource> PendingResourceDeletions;''',
        '''private:
  // Zero is reserved for "unassigned". Process-lifetime wrap is outside this
  // diagnostic's reachable state space; production can make exhaustion fatal.
  uint64_t AllocateOwnerID() {
    return NextOwnerID++;
  }

  uint64_t NextOwnerID {1};
  MappedResource::ContainerType MappedResources;
  fextl::vector<MappedResource> PendingResourceDeletions;''',
        'owner allocator',
    )

    cpp = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsVMATracking.cpp"
    repl(
        cpp,
        '''void VMATracking::TrackVMARange(FEXCore::Context::Context* CTX, MappedResource* MappedResource, uintptr_t Base, uintptr_t Offset,
                                uintptr_t Length, VMAFlags Flags, VMAProt Prot) {
  Mutex.check_lock_owned_by_self_as_write();

  DeleteVMARange(CTX, Base, Length, MappedResource);''',
        '''void VMATracking::TrackVMARange(FEXCore::Context::Context* CTX, MappedResource* MappedResource, uintptr_t Base, uintptr_t Offset,
                                uintptr_t Length, VMAFlags Flags, VMAProt Prot, uint64_t OwnerID) {
  Mutex.check_lock_owned_by_self_as_write();

  if (OwnerID == 0) {
    if (MappedResource && MappedResource->OwnerID != 0) {
      OwnerID = MappedResource->OwnerID;
    } else {
      OwnerID = AllocateOwnerID();
      if (MappedResource) {
        MappedResource->OwnerID = OwnerID;
      }
    }
  } else if (MappedResource && MappedResource->OwnerID == 0) {
    MappedResource->OwnerID = OwnerID;
  } else if (MappedResource) {
    LOGMAN_THROW_A_FMT(MappedResource->OwnerID == OwnerID,
                       "VMA owner mismatch: resource={} incoming={}", MappedResource->OwnerID, OwnerID);
  }

  DeleteVMARange(CTX, Base, Length, MappedResource);''',
        'TrackVMARange owner assignment',
    )
    repl(
        cpp,
        'VMAEntry {MappedResource, PrevResVMA, NextResVMA, Base, Offset, Length, Flags, Prot}',
        'VMAEntry {MappedResource, PrevResVMA, NextResVMA, Base, Offset, Length, Flags, Prot, OwnerID}',
        'primary VMA insertion owner',
    )
    repl(
        cpp,
        '''VMAEntry {Current->Resource, ReplaceAndErase ? Current->ResourcePrevVMA : Current,
                                                            Current->ResourceNextVMA, Top, NewOffset, NewLength, Current->Flags, Current->Prot}''',
        '''VMAEntry {Current->Resource, ReplaceAndErase ? Current->ResourcePrevVMA : Current,
                                                            Current->ResourceNextVMA, Top, NewOffset, NewLength, Current->Flags, Current->Prot,
                                                            Current->OwnerID}''',
        'DeleteVMARange split owner',
    )
    # ChangeProtectionFlags has three original-protection split insertions in
    # two indentation shapes: merge strategies 4 and 3 share one; strategy 2
    # uses the shorter initializer indentation.
    replace_all(
        cpp,
        '''.Flags = CurrentFlags,
                                                          .Prot = CurrentProt});''',
        '''.Flags = CurrentFlags,
                                                          .Prot = CurrentProt,
                                                          .OwnerID = Current->OwnerID});''',
        2,
        'mprotect original-protection split owners wide',
    )
    repl(
        cpp,
        '''.Flags = CurrentFlags,
                                                        .Prot = CurrentProt});''',
        '''.Flags = CurrentFlags,
                                                        .Prot = CurrentProt,
                                                        .OwnerID = Current->OwnerID});''',
        'mprotect original-protection split owner strategy2',
    )
    repl(
        cpp,
        '''.Flags = CurrentFlags,
                                                           .Prot = NewProt});''',
        '''.Flags = CurrentFlags,
                                                           .Prot = NewProt,
                                                           .OwnerID = Current->OwnerID});''',
        'mprotect changed-protection split owner',
    )

    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"
    repl(
        smc,
        '''  const auto OldFlags = OldVMA->second.Flags;
  const auto OldProt = OldVMA->second.Prot;''',
        '''  const auto OldFlags = OldVMA->second.Flags;
  const auto OldProt = OldVMA->second.Prot;
  const auto OldOwnerID = OldVMA->second.OwnerID;''',
        'mremap capture old owner',
    )
    repl(
        smc,
        '''    VMATracking.TrackVMARange(CTX, OldResource, NewAddress, OldOffset, NewSize, OldFlags, OldProt);
  } else {''',
        '''    VMATracking.TrackVMARange(CTX, OldResource, NewAddress, OldOffset, NewSize, OldFlags, OldProt, OldOwnerID);
  } else {''',
        'mremap mirror owner',
    )
    repl(
        smc,
        '''    VMATracking.TrackVMARange(CTX, OldResource, NewAddress, OldOffset, NewSize, OldFlags, OldProt);
  }
}''',
        '''    VMATracking.TrackVMARange(CTX, OldResource, NewAddress, OldOffset, NewSize, OldFlags, OldProt, OldOwnerID);
  }
}''',
        'mremap move owner',
    )

    repl(
        smc,
        '''  std::optional<FEXCore::ExecutableFileSectionInfo> CachedSection;
  bool PendingResourceDeletion;
  std::optional<uint64_t> MmapFailureResult;''',
        '''  std::optional<FEXCore::ExecutableFileSectionInfo> CachedSection;
  bool PendingResourceDeletion;
  std::optional<uint64_t> MmapFailureResult;
  uint64_t DiagnosticOldOwner {};
  uint64_t DiagnosticNewOwner {};''',
        'mmap owner diagnostics storage',
    )
    repl(
        smc,
        '''    bool Map32Bit = !Is64Bit || (flags & FEX::HLE::X86_64_MAP_32BIT);''',
        '''    if ((flags & MAP_FIXED) && addr) {
      auto OldEntry = VMATracking.FindVMAEntry(reinterpret_cast<uintptr_t>(addr));
      if (OldEntry != VMATracking.VMAs.end()) {
        DiagnosticOldOwner = OldEntry->second.OwnerID;
      }
    }

    bool Map32Bit = !Is64Bit || (flags & FEX::HLE::X86_64_MAP_32BIT);''',
        'capture pre-MAP_FIXED owner',
    )
    repl(
        smc,
        '''      LateMetadata = TrackMmap(Thread, Result, length, prot, flags, fd, offset, CachedSection);
      PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();''',
        '''      LateMetadata = TrackMmap(Thread, Result, length, prot, flags, fd, offset, CachedSection);
      PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();
      if ((flags & MAP_FIXED) && addr) {
        auto NewEntry = VMATracking.FindVMAEntry(Result);
        if (NewEntry != VMATracking.VMAs.end()) {
          DiagnosticNewOwner = NewEntry->second.OwnerID;
        }
      }''',
        'capture post-MAP_FIXED owner',
    )
    repl(
        smc,
        '''  if (MmapFailureResult) {
    if (ThunkRetirementToken) {''',
        '''  if ((flags & MAP_FIXED) && addr) {
    fprintf(stderr, "DIAG_OWNER_MAP_FIXED addr=%#lx old=%#lx new=%#lx success=%d\\n",
            reinterpret_cast<uintptr_t>(addr), DiagnosticOldOwner, DiagnosticNewOwner, MmapFailureResult ? 0 : 1);
  }

  if (MmapFailureResult) {
    if (ThunkRetirementToken) {''',
        'print MAP_FIXED owner transition',
    )

    repl(
        smc,
        '''uint64_t SyscallHandler::GuestMprotect(FEXCore::Core::InternalThreadState* Thread, void* addr, size_t len, int prot) {
  uint64_t Result {};

  {
    auto lk = FEXCore::GuardSignalDeferringSection(VMATracking.Mutex, Thread);''',
        '''uint64_t SyscallHandler::GuestMprotect(FEXCore::Core::InternalThreadState* Thread, void* addr, size_t len, int prot) {
  uint64_t Result {};
  uint64_t DiagnosticOwnerBefore {};
  uint64_t DiagnosticOwnerAfter {};

  {
    auto lk = FEXCore::GuardSignalDeferringSection(VMATracking.Mutex, Thread);
    auto BeforeEntry = VMATracking.FindVMAEntry(reinterpret_cast<uintptr_t>(addr));
    if (BeforeEntry != VMATracking.VMAs.end()) {
      DiagnosticOwnerBefore = BeforeEntry->second.OwnerID;
    }''',
        'mprotect owner before',
    )
    repl(
        smc,
        '''    TrackMprotect(Thread, addr, len, prot);
  }

  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), len, false);''',
        '''    TrackMprotect(Thread, addr, len, prot);
    auto AfterEntry = VMATracking.FindVMAEntry(reinterpret_cast<uintptr_t>(addr));
    if (AfterEntry != VMATracking.VMAs.end()) {
      DiagnosticOwnerAfter = AfterEntry->second.OwnerID;
    }
  }

  fprintf(stderr, "DIAG_OWNER_MPROTECT addr=%#lx before=%#lx after=%#lx prot=%#x\\n",
          reinterpret_cast<uintptr_t>(addr), DiagnosticOwnerBefore, DiagnosticOwnerAfter, prot);

  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), len, false);''',
        'mprotect owner after',
    )

    print('Added VMA mapping-generation owner IDs and transition diagnostics')


if __name__ == '__main__':
    main()
