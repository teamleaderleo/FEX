#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()

    lookup = root / "FEXCore/Source/Interface/Core/LookupCache.h"
    replace_once(
        lookup,
        "  void InvalidateRange(uint64_t Start, uint64_t Length) {\n    auto lk = AcquireWriteLock();",
        """  bool InvalidateExactEntry(uint64_t Address) {
    auto lk = AcquireWriteLock();
    return Erase(Address, lk);
  }

  void InvalidateRange(uint64_t Start, uint64_t Length) {
    auto lk = AcquireWriteLock();""",
        "shared exact eviction",
    )
    replace_once(
        lookup,
        "  // Invalidates all L1/L2 entries for all guest block that intersect the given range\n  bool InvalidateCacheRange(uint64_t Start, uint64_t Length) {",
        """  void InvalidateExactEntry(uint64_t Address) {
    auto lk = Shared->AcquireWriteLock();
    InvalidateCache(Address, lk);
  }

  // Invalidates all L1/L2 entries for all guest block that intersect the given range
  bool InvalidateCacheRange(uint64_t Start, uint64_t Length) {""",
        "thread-local exact eviction",
    )

    public_context = root / "FEXCore/include/FEXCore/Core/Context.h"
    replace_once(
        public_context,
        "  FEX_DEFAULT_VISIBILITY virtual void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) = 0;",
        """  FEX_DEFAULT_VISIBILITY virtual void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) = 0;
  FEX_DEFAULT_VISIBILITY virtual void InvalidateCodeBuffersCodeEntry(uint64_t Address) = 0;""",
        "public shared exact declaration",
    )
    replace_once(
        public_context,
        "InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) = 0;",
        """InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) = 0;
  FEX_DEFAULT_VISIBILITY virtual void
  InvalidateThreadCachedCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) = 0;""",
        "public per-thread exact declaration",
    )

    impl_context = root / "FEXCore/Source/Interface/Context/Context.h"
    replace_once(
        impl_context,
        "  void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) override;",
        """  void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) override;
  void InvalidateCodeBuffersCodeEntry(uint64_t Address) override;""",
        "implementation shared exact declaration",
    )
    replace_once(
        impl_context,
        "  void InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) override;",
        """  void InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) override;
  void InvalidateThreadCachedCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override;""",
        "implementation per-thread exact declaration",
    )

    core = root / "FEXCore/Source/Interface/Core/Core.cpp"
    replace_once(
        core,
        "void ContextImpl::InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) {",
        """void ContextImpl::InvalidateCodeBuffersCodeEntry(uint64_t Address) {
  LOGMAN_THROW_A_FMT(CodeInvalidationMutex.try_lock() == false, \"CodeInvalidationMutex needs to be unique_locked here\");
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
  fprintf(stderr, \"DIAG_EXACT_SHARED H=%#lx erased=%zu\\n\", Address, Erased);
}

void ContextImpl::InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) {""",
        "Core shared exact implementation",
    )
    replace_once(
        core,
        "void ContextImpl::InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {",
        """void ContextImpl::InvalidateThreadCachedCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {
  LOGMAN_THROW_A_FMT(CodeInvalidationMutex.try_lock() == false, \"CodeInvalidationMutex needs to be unique_locked here\");
  Thread->LookupCache->InvalidateExactEntry(Address);
  fprintf(stderr, \"DIAG_EXACT_LOCAL H=%#lx thread=%p\\n\", Address, Thread);
}

void ContextImpl::InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {""",
        "Core per-thread exact implementation",
    )

    syscall_handler = root / "FEXCore/include/FEXCore/HLE/SyscallHandler.h"
    replace_once(
        syscall_handler,
        "  virtual void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {}",
        """  virtual void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {}
  virtual void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {}""",
        "SyscallHandler exact invalidation virtual",
    )

    thread_manager = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/ThreadManager.h"
    replace_once(
        thread_manager,
        "  void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Start, uint64_t Length) {\n    std::lock_guard lk(ThreadCreationMutex);",
        """  void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Address) {
    std::lock_guard lk(ThreadCreationMutex);
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), CallingThread);
    CTX->InvalidateCodeBuffersCodeEntry(Address);
    for (auto& Thread : Threads) {
      CTX->InvalidateThreadCachedCodeEntry(Thread->Thread, Address);
    }
  }

  void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Start, uint64_t Length) {
    std::lock_guard lk(ThreadCreationMutex);""",
        "ThreadManager exact invalidation",
    )

    syscalls = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/Syscalls.h"
    replace_once(
        syscalls,
        "  uint64_t GuestMprotect(FEXCore::Core::InternalThreadState*, void* addr, size_t len, int prot);",
        """  uint64_t GuestMprotect(FEXCore::Core::InternalThreadState*, void* addr, size_t len, int prot);
  void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override {
    TM.InvalidateGuestCodeEntry(Thread, Address);
  }""",
        "Linux syscall exact invalidation override",
    )

    replace_once(
        core,
        "  auto InsertedIterator = CustomIRHandlers.emplace(Entrypoint, CustomIRHandlerEntry {Handler, Creator, Data});\n  HasCustomIRHandlers = true;",
        "  auto InsertedIterator = CustomIRHandlers.emplace(Entrypoint, CustomIRHandlerEntry {Handler, Creator, Data});\n  fprintf(stderr, \"DIAG_CUSTOM_ADD H=%#lx inserted=%d data=%p\\n\", Entrypoint, InsertedIterator.second ? 1 : 0, Data);\n  HasCustomIRHandlers = true;",
        "CustomIR add trace",
    )
    replace_once(
        core,
        "  CustomIRHandlers.erase(Entrypoint);\n  HasCustomIRHandlers = !CustomIRHandlers.empty();\n  SyscallHandler->InvalidateGuestCodeRange(Thread, Entrypoint, 1);",
        "  auto Erased = CustomIRHandlers.erase(Entrypoint);\n  HasCustomIRHandlers = !CustomIRHandlers.empty();\n  SyscallHandler->InvalidateGuestCodeEntry(Thread, Entrypoint);\n  fprintf(stderr, \"DIAG_CUSTOM_REMOVE H=%#lx handler=%zu\\n\", Entrypoint, Erased);",
        "CustomIR exact retirement",
    )

    replace_once(
        core,
        """    if (Result->Data != (void*)GuestThunkEntrypoint) {
      // NOTE: This may happen in Vulkan thunks if the Vulkan driver resolves two different symbols
      //       to the same function (e.g. vkGetPhysicalDeviceFeatures2/vkGetPhysicalDeviceFeatures2KHR)
      LogMan::Msg::EFmt(\"Input address for AddThunkTrampoline is already linked elsewhere\");
    }""",
        """    if (Result->Data != (void*)GuestThunkEntrypoint) {
      fprintf(stderr, \"DIAG_DUP H=%#lx OLD=%p NEW=%#lx\\n\", Entrypoint, Result->Data, GuestThunkEntrypoint);
      RemoveCustomIREntrypoint(nullptr, Entrypoint);
      AddThunkTrampolineIRHandler(Entrypoint, GuestThunkEntrypoint);
      return;
    }""",
        "duplicate thunk rebind",
    )

    print("Applied exact all-cache thunk rebind diagnostic")


if __name__ == "__main__":
    main()
