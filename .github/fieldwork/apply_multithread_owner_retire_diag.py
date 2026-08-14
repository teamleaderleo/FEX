#!/usr/bin/env python3
from pathlib import Path
import sys


def repl(path: Path, old: str, new: str, label: str) -> None:
    s = path.read_text()
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor in {path}, found {n}")
    path.write_text(s.replace(old, new, 1))


def add_owner_tracking(root: Path) -> None:
    th = root / "Source/Tools/LinuxEmulation/Thunks.h"
    s = th.read_text()
    marker = "namespace FEX::HLE {\n"
    if marker not in s:
        raise SystemExit("Thunks.h namespace marker missing")
    s = s.replace(marker, "namespace FEXCore::Core { struct InternalThreadState; }\n\n" + marker, 1)
    old = "  virtual void AppendThunkDefinitions(std::span<const FEXCore::IR::ThunkDefinition> Definitions) = 0;"
    new = old + "\n  virtual void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;"
    if old not in s:
        raise SystemExit("Thunks.h interface marker missing")
    th.write_text(s.replace(old, new, 1))

    p = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    s = p.read_text()
    if "#include <FEXCore/fextl/vector.h>" not in s:
        s = s.replace("#include <FEXCore/fextl/unordered_map.h>\n", "#include <FEXCore/fextl/unordered_map.h>\n#include <FEXCore/fextl/vector.h>\n", 1)
    old = "  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;"
    new = old + "\n  fextl::unordered_map<uintptr_t, uintptr_t> LinkedHostToGuest;"
    if old not in s:
        raise SystemExit("owner map marker missing")
    s = s.replace(old, new, 1)
    old = "  void AppendThunkDefinitions(std::span<const FEXCore::IR::ThunkDefinition> Definitions) override {"
    new = "  void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;\n\n" + old
    if old not in s:
        raise SystemExit("owner method marker missing")
    s = s.replace(old, new, 1)
    old = "FEX_DEFAULT_VISIBILITY HostToGuestTrampolinePtr*\nMakeHostTrampolineForGuestFunction"
    new = r'''void ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) return;
  fextl::vector<uintptr_t> ToRetire;
  {
    std::lock_guard lk(ThunksMutex);
    for (auto& [Host, Target] : LinkedHostToGuest) {
      if (Target >= Base && Target - Base < Length) {
        ToRetire.emplace_back(Host);
        fprintf(stderr, "DIAG_MT_MATCH H=%#lx T=%#lx range=%#lx+%#lx\n", Host, Target, Base, Length);
      }
    }
    for (auto Host : ToRetire) LinkedHostToGuest.erase(Host);
  }
  auto CTX = static_cast<FEXCore::Context::Context*>(Thread->CTX);
  for (auto Host : ToRetire) CTX->RetireThunkTrampolineIRHandler(Thread, Host);
}

FEX_DEFAULT_VISIBILITY HostToGuestTrampolinePtr*
MakeHostTrampolineForGuestFunction'''
    if old not in s:
        raise SystemExit("retire definition marker missing")
    s = s.replace(old, new, 1)
    old = "    CTX->AddThunkTrampolineIRHandler(args->original_callee, args->target_addr);\n"
    new = r'''    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
    CTX->AddThunkTrampolineIRHandler(args->original_callee, args->target_addr);
    {
      std::lock_guard lk(ThunkHandler->ThunksMutex);
      ThunkHandler->LinkedHostToGuest[args->original_callee] = args->target_addr;
    }
    fprintf(stderr, "DIAG_MT_OWNER H=%#lx T=%#lx\n", args->original_callee, args->target_addr);
'''
    if old not in s:
        raise SystemExit("LinkAddress owner marker missing")
    p.write_text(s.replace(old, new, 1))

    p = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"
    s = p.read_text()
    if '#include "Thunks.h"' not in s:
        s = s.replace('#include "LinuxSyscalls/Syscalls.h"\n', '#include "LinuxSyscalls/Syscalls.h"\n#include "Thunks.h"\n', 1)
    old = "  bool PendingResourceDeletion;\n\n  {\n    // Frontend calls this with nullptr Thread during initialization."
    new = r'''  bool PendingResourceDeletion;

  if (Thread && Size) {
    if (auto* Thunks = GetThunkHandler()) {
      Thunks->RetireGuestRange(Thread, reinterpret_cast<uintptr_t>(addr), Size);
    }
  }

  {
    // Frontend calls this with nullptr Thread during initialization.'''
    if old not in s:
        raise SystemExit("GuestMunmap pre-unmap marker missing")
    p.write_text(s.replace(old, new, 1))


def add_retire_api(root: Path) -> None:
    pub = root / "FEXCore/include/FEXCore/Core/Context.h"
    repl(pub,
         "  FEX_DEFAULT_VISIBILITY virtual void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) = 0;",
         "  FEX_DEFAULT_VISIBILITY virtual void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) = 0;\n  FEX_DEFAULT_VISIBILITY virtual void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) = 0;",
         "public retire declaration")
    impl = root / "FEXCore/Source/Interface/Context/Context.h"
    repl(impl,
         "  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;",
         "  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;\n  void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) override;",
         "impl retire declaration")


def apply_local(root: Path) -> None:
    lookup = root / "FEXCore/Source/Interface/Core/LookupCache.h"
    repl(lookup,
         "  // Invalidates all L1/L2 entries for all guest block that intersect the given range\n  bool InvalidateCacheRange(uint64_t Start, uint64_t Length) {",
         r'''  bool InvalidateExactEntry(uint64_t Address) {
    auto lk = Shared->AcquireWriteLock();
    InvalidateCache(Address, lk);
    return Shared->Erase(Address, lk);
  }

  // Invalidates all L1/L2 entries for all guest block that intersect the given range
  bool InvalidateCacheRange(uint64_t Start, uint64_t Length) {''',
         "local exact cache entry")
    core = root / "FEXCore/Source/Interface/Core/Core.cpp"
    repl(core,
         "void ContextImpl::AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) {",
         r'''void ContextImpl::RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) {
  RemoveCustomIREntrypoint(Thread, Entrypoint);
  bool SharedErased = false;
  if (Thread) SharedErased = Thread->LookupCache->InvalidateExactEntry(Entrypoint);
  fprintf(stderr, "DIAG_MT_RETIRE_LOCAL H=%#lx thread=%p shared=%d\n", Entrypoint, Thread, SharedErased ? 1 : 0);
}

void ContextImpl::AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) {''',
         "local retire implementation")


def apply_all(root: Path) -> None:
    lookup = root / "FEXCore/Source/Interface/Core/LookupCache.h"
    repl(lookup,
         "  void InvalidateRange(uint64_t Start, uint64_t Length) {\n    auto lk = AcquireWriteLock();",
         r'''  bool InvalidateExactEntry(uint64_t Address) {
    auto lk = AcquireWriteLock();
    return Erase(Address, lk);
  }

  void InvalidateRange(uint64_t Start, uint64_t Length) {
    auto lk = AcquireWriteLock();''',
         "shared exact cache entry")
    repl(lookup,
         "  // Invalidates all L1/L2 entries for all guest block that intersect the given range\n  bool InvalidateCacheRange(uint64_t Start, uint64_t Length) {",
         r'''  void InvalidateExactEntry(uint64_t Address) {
    auto lk = Shared->AcquireWriteLock();
    InvalidateCache(Address, lk);
  }

  // Invalidates all L1/L2 entries for all guest block that intersect the given range
  bool InvalidateCacheRange(uint64_t Start, uint64_t Length) {''',
         "thread exact cache entry")

    pub = root / "FEXCore/include/FEXCore/Core/Context.h"
    repl(pub,
         "  FEX_DEFAULT_VISIBILITY virtual void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) = 0;",
         "  FEX_DEFAULT_VISIBILITY virtual void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) = 0;\n  FEX_DEFAULT_VISIBILITY virtual void InvalidateCodeBuffersCodeEntry(uint64_t Address) = 0;",
         "public shared exact declaration")
    repl(pub,
         "InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) = 0;",
         "InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) = 0;\n  FEX_DEFAULT_VISIBILITY virtual void InvalidateThreadCachedCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) = 0;",
         "public thread exact declaration")

    impl = root / "FEXCore/Source/Interface/Context/Context.h"
    repl(impl,
         "  void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) override;",
         "  void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) override;\n  void InvalidateCodeBuffersCodeEntry(uint64_t Address) override;",
         "impl shared exact declaration")
    repl(impl,
         "  void InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) override;",
         "  void InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) override;\n  void InvalidateThreadCachedCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override;",
         "impl thread exact declaration")

    core = root / "FEXCore/Source/Interface/Core/Core.cpp"
    repl(core,
         "void ContextImpl::InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) {",
         r'''void ContextImpl::InvalidateCodeBuffersCodeEntry(uint64_t Address) {
  LOGMAN_THROW_A_FMT(CodeInvalidationMutex.try_lock() == false, "CodeInvalidationMutex needs to be unique_locked here");
  std::scoped_lock lk {CodeBufferListLock};
  size_t Erased = 0;
  for (auto it = CodeBufferList.begin(); it != CodeBufferList.end();) {
    if (auto Strong = it->lock()) {
      Erased += Strong->LookupCache->InvalidateExactEntry(Address) ? 1 : 0;
      ++it;
    } else {
      it = CodeBufferList.erase(it);
    }
  }
  fprintf(stderr, "DIAG_MT_SHARED H=%#lx erased=%zu\n", Address, Erased);
}

void ContextImpl::InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) {''',
         "shared exact implementation")
    repl(core,
         "void ContextImpl::InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {",
         r'''void ContextImpl::InvalidateThreadCachedCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {
  LOGMAN_THROW_A_FMT(CodeInvalidationMutex.try_lock() == false, "CodeInvalidationMutex needs to be unique_locked here");
  Thread->LookupCache->InvalidateExactEntry(Address);
  fprintf(stderr, "DIAG_MT_THREAD H=%#lx thread=%p\n", Address, Thread);
}

void ContextImpl::InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {''',
         "thread exact implementation")

    sh = root / "FEXCore/include/FEXCore/HLE/SyscallHandler.h"
    repl(sh,
         "  virtual void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {}",
         "  virtual void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {}\n  virtual void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {}",
         "syscall exact virtual")

    tm = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/ThreadManager.h"
    repl(tm,
         "  void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Start, uint64_t Length) {\n    std::lock_guard lk(ThreadCreationMutex);",
         r'''  void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Address) {
    std::lock_guard lk(ThreadCreationMutex);
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), CallingThread);
    CTX->InvalidateCodeBuffersCodeEntry(Address);
    for (auto& Thread : Threads) CTX->InvalidateThreadCachedCodeEntry(Thread->Thread, Address);
  }

  void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Start, uint64_t Length) {
    std::lock_guard lk(ThreadCreationMutex);''',
         "thread manager exact invalidation")

    syscalls = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/Syscalls.h"
    repl(syscalls,
         "  uint64_t GuestMprotect(FEXCore::Core::InternalThreadState*, void* addr, size_t len, int prot);",
         "  uint64_t GuestMprotect(FEXCore::Core::InternalThreadState*, void* addr, size_t len, int prot);\n  void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override { TM.InvalidateGuestCodeEntry(Thread, Address); }",
         "syscall exact override")

    repl(core,
         "  CustomIRHandlers.erase(Entrypoint);\n  HasCustomIRHandlers = !CustomIRHandlers.empty();\n  SyscallHandler->InvalidateGuestCodeRange(Thread, Entrypoint, 1);",
         "  auto Erased = CustomIRHandlers.erase(Entrypoint);\n  HasCustomIRHandlers = !CustomIRHandlers.empty();\n  SyscallHandler->InvalidateGuestCodeEntry(Thread, Entrypoint);\n  fprintf(stderr, \"DIAG_MT_REMOVE_ALL H=%#lx handler=%zu\\n\", Entrypoint, Erased);",
         "exact all-thread CustomIR removal")
    repl(core,
         "void ContextImpl::AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) {",
         r'''void ContextImpl::RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) {
  RemoveCustomIREntrypoint(Thread, Entrypoint);
  fprintf(stderr, "DIAG_MT_RETIRE_ALL H=%#lx thread=%p\n", Entrypoint, Thread);
}

void ContextImpl::AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) {''',
         "all-thread retire implementation")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[2] not in {"local", "all"}:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT local|all")
    root = Path(sys.argv[1]).resolve()
    mode = sys.argv[2]
    add_retire_api(root)
    add_owner_tracking(root)
    if mode == "local":
        apply_local(root)
    else:
        apply_all(root)
    print(f"Applied multithread owner-retirement diagnostic mode={mode}")


if __name__ == "__main__":
    main()
