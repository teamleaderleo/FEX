#!/usr/bin/env python3
"""Apply the FEX-2608 exact CustomIR retirement v2 diagnostic.

This is an internal research applicator, not an upstream-ready patch.

The v1 range-retirement sketch called InvalidateGuestCodeRange(H, 1). That
cannot evict an already-compiled CustomIR block because CustomIR compilation
does not populate the guest CodePages reverse index. v2 adds an exact-address
eviction path for the native PFN H and retires thunk-owned H->T registrations
whose guest target T falls inside a successfully unmapped guest range.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str, marker: str) -> bool:
    target = ROOT / path
    text = target.read_text()
    if marker in text:
        print(f"already applied: {path}: {marker}")
        return False
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one anchor, found {count}")
    target.write_text(text.replace(old, new, 1))
    print(f"patched: {path}")
    return True


def run_check() -> None:
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="verify source anchors without modifying the checkout",
    )
    args = parser.parse_args()

    edits: list[tuple[str, str, str, str]] = []

    edits.append((
        "FEXCore/include/FEXCore/Core/Context.h",
        """  FEX_DEFAULT_VISIBILITY virtual void ClearCodeCache(FEXCore::Core::InternalThreadState* Thread, bool NewCodeBuffer = true) = 0;
  FEX_DEFAULT_VISIBILITY virtual void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) = 0;
  FEX_DEFAULT_VISIBILITY virtual void
  InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) = 0;
  FEX_DEFAULT_VISIBILITY virtual FEXCore::Utils::WritePriorityMutex::Mutex& GetCodeInvalidationMutex() = 0;
""",
        """  FEX_DEFAULT_VISIBILITY virtual void ClearCodeCache(FEXCore::Core::InternalThreadState* Thread, bool NewCodeBuffer = true) = 0;
  FEX_DEFAULT_VISIBILITY virtual void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) = 0;
  FEX_DEFAULT_VISIBILITY virtual void
  InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) = 0;

  // Diagnostic exact-entry invalidation. Unlike range invalidation, this does not
  // depend on CodePages/CachedCodePages reverse indexes.
  FEX_DEFAULT_VISIBILITY virtual void InvalidateCodeBuffersCodeEntry(uint64_t Address) = 0;
  FEX_DEFAULT_VISIBILITY virtual void
  InvalidateThreadCachedCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) = 0;

  FEX_DEFAULT_VISIBILITY virtual FEXCore::Utils::WritePriorityMutex::Mutex& GetCodeInvalidationMutex() = 0;
""",
        "InvalidateCodeBuffersCodeEntry",
    ))

    edits.append((
        "FEXCore/include/FEXCore/Core/Context.h",
        """  FEX_DEFAULT_VISIBILITY virtual void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) = 0;

  /**
   * @brief Adds additional per-instruction granularity TSO enable/disable information for the given range.
""",
        """  FEX_DEFAULT_VISIBILITY virtual void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) = 0;

  // Diagnostic lifetime probe: erase thunk-owned CustomIR registrations whose
  // captured guest target is inside [Start, Start + Length). The caller is
  // responsible for exact invalidation of each returned native-PFN key.
  FEX_DEFAULT_VISIBILITY virtual fextl::vector<uintptr_t>
  RetireThunkTrampolineIRHandlersInRange(uintptr_t Start, uintptr_t Length) = 0;

  /**
   * @brief Adds additional per-instruction granularity TSO enable/disable information for the given range.
""",
        "RetireThunkTrampolineIRHandlersInRange",
    ))

    edits.append((
        "FEXCore/Source/Interface/Context/Context.h",
        """  void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) override;
  void InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) override;
  FEXCore::Utils::WritePriorityMutex::Mutex& GetCodeInvalidationMutex() override {
""",
        """  void InvalidateCodeBuffersCodeRange(uint64_t Start, uint64_t Length) override;
  void InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) override;
  void InvalidateCodeBuffersCodeEntry(uint64_t Address) override;
  void InvalidateThreadCachedCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override;
  FEXCore::Utils::WritePriorityMutex::Mutex& GetCodeInvalidationMutex() override {
""",
        "void InvalidateCodeBuffersCodeEntry(uint64_t Address) override;",
    ))

    edits.append((
        "FEXCore/Source/Interface/Context/Context.h",
        """  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;

  void AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) override;
""",
        """  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;
  fextl::vector<uintptr_t> RetireThunkTrampolineIRHandlersInRange(uintptr_t Start, uintptr_t Length) override;

  void AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) override;
""",
        "fextl::vector<uintptr_t> RetireThunkTrampolineIRHandlersInRange",
    ))

    edits.append((
        "FEXCore/Source/Interface/Core/LookupCache.h",
        """  // Invalidates all L1/L2 entries for all guest block that intersect the given range
  bool InvalidateCacheRange(uint64_t Start, uint64_t Length) {
    auto lk = Shared->AcquireWriteLock();

    auto lower = CachedCodePages.lower_bound(Start >> 12);
    auto upper = CachedCodePages.upper_bound((Start + Length - 1) >> 12);

    for (auto it = lower; it != upper; it++) {
      for (const auto& Entry : it->second) {
        InvalidateCache(Entry, lk);
      }
    }
    bool ret = upper != lower;
    CachedCodePages.erase(lower, upper);
    return ret;
  }

  void AddBlockLink(uint64_t GuestDestination, FEXCore::Context::ExitFunctionLinkData* HostLink,
""",
        """  // Invalidates all L1/L2 entries for all guest block that intersect the given range
  bool InvalidateCacheRange(uint64_t Start, uint64_t Length) {
    auto lk = Shared->AcquireWriteLock();

    auto lower = CachedCodePages.lower_bound(Start >> 12);
    auto upper = CachedCodePages.upper_bound((Start + Length - 1) >> 12);

    for (auto it = lower; it != upper; it++) {
      for (const auto& Entry : it->second) {
        InvalidateCache(Entry, lk);
      }
    }
    bool ret = upper != lower;
    CachedCodePages.erase(lower, upper);
    return ret;
  }

  // Exact invalidation helpers for synthetic entrypoints which intentionally
  // have no CodePages/CachedCodePages reverse-index entries.
  void InvalidateSharedEntry(uint64_t Address) {
    auto lk = Shared->AcquireWriteLock();
    Shared->Erase(Address, lk);
  }

  void InvalidateCacheEntry(uint64_t Address) {
    auto lk = Shared->AcquireWriteLock();
    InvalidateCache(Address, lk);
  }

  void AddBlockLink(uint64_t GuestDestination, FEXCore::Context::ExitFunctionLinkData* HostLink,
""",
        "void InvalidateSharedEntry(uint64_t Address)",
    ))

    edits.append((
        "FEXCore/Source/Interface/Core/Core.cpp",
        """void ContextImpl::InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {
  LOGMAN_THROW_A_FMT(CodeInvalidationMutex.try_lock() == false, "CodeInvalidationMutex needs to be unique_locked here");

  // Ensures now-modified mappings aren't cached as being in their previous non-executable state.
  // Accessing FrontendDecoder is safe as the thread's code invalidation mutex must be locked here.
  Thread->FrontendDecoder->ResetExecutableRangeCache();

  if (Thread->LookupCache->InvalidateCacheRange(Start, Length)) {
    FEXCORE_PROFILE_SCOPED("InvalidateCallRet");

    // This may cause access violations in the thread on Windows as zeroing is not atomic, this is handled by the frontend
    Allocator::VirtualDontNeed(Thread->CallRetStackBase, FEXCore::Core::InternalThreadState::CALLRET_STACK_SIZE);
  }
}

void ContextImpl::ThreadRemoveCodeEntryFromJit(FEXCore::Core::CpuStateFrame* Frame, uint64_t GuestRIP) {
""",
        """void ContextImpl::InvalidateThreadCachedCodeRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {
  LOGMAN_THROW_A_FMT(CodeInvalidationMutex.try_lock() == false, "CodeInvalidationMutex needs to be unique_locked here");

  // Ensures now-modified mappings aren't cached as being in their previous non-executable state.
  // Accessing FrontendDecoder is safe as the thread's code invalidation mutex must be locked here.
  Thread->FrontendDecoder->ResetExecutableRangeCache();

  if (Thread->LookupCache->InvalidateCacheRange(Start, Length)) {
    FEXCORE_PROFILE_SCOPED("InvalidateCallRet");

    // This may cause access violations in the thread on Windows as zeroing is not atomic, this is handled by the frontend
    Allocator::VirtualDontNeed(Thread->CallRetStackBase, FEXCore::Core::InternalThreadState::CALLRET_STACK_SIZE);
  }
}

void ContextImpl::InvalidateCodeBuffersCodeEntry(uint64_t Address) {
  LOGMAN_THROW_A_FMT(CodeInvalidationMutex.try_lock() == false, "CodeInvalidationMutex needs to be unique_locked here");

  std::scoped_lock lk {CodeBufferListLock};
  auto it = CodeBufferList.begin();
  while (it != CodeBufferList.end()) {
    if (auto Strong = it->lock()) {
      Strong->LookupCache->InvalidateSharedEntry(Address);
      ++it;
    } else {
      it = CodeBufferList.erase(it);
    }
  }
}

void ContextImpl::InvalidateThreadCachedCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {
  LOGMAN_THROW_A_FMT(CodeInvalidationMutex.try_lock() == false, "CodeInvalidationMutex needs to be unique_locked here");

  Thread->FrontendDecoder->ResetExecutableRangeCache();
  Thread->LookupCache->InvalidateCacheEntry(Address);

  // A cached call/return prediction can bypass the ordinary lookup path. Flush
  // it unconditionally for the exact-entry diagnostic.
  Allocator::VirtualDontNeed(Thread->CallRetStackBase, FEXCore::Core::InternalThreadState::CALLRET_STACK_SIZE);
}

void ContextImpl::ThreadRemoveCodeEntryFromJit(FEXCore::Core::CpuStateFrame* Frame, uint64_t GuestRIP) {
""",
        "void ContextImpl::InvalidateCodeBuffersCodeEntry(uint64_t Address)",
    ))

    edits.append((
        "FEXCore/Source/Interface/Core/Core.cpp",
        """void ContextImpl::AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) {
""",
        """fextl::vector<uintptr_t> ContextImpl::RetireThunkTrampolineIRHandlersInRange(uintptr_t Start, uintptr_t Length) {
  fextl::vector<uintptr_t> RetiredEntrypoints;
  if (Length == 0) {
    return RetiredEntrypoints;
  }

  std::unique_lock lk(CustomIRMutex);
  for (auto it = CustomIRHandlers.begin(); it != CustomIRHandlers.end();) {
    auto& Entry = it->second;
    if (Entry.Creator == ThunkHandler && Entry.Data != nullptr) {
      const auto GuestTarget = reinterpret_cast<uintptr_t>(Entry.Data);
      if (GuestTarget >= Start && (GuestTarget - Start) < Length) {
        LogMan::Msg::IFmt("Thunks: Retiring guest trampoline from address {:#x} to unmapped guest function {:#x}", it->first, GuestTarget);
        RetiredEntrypoints.emplace_back(it->first);
        it = CustomIRHandlers.erase(it);
        continue;
      }
    }
    ++it;
  }

  HasCustomIRHandlers = !CustomIRHandlers.empty();
  return RetiredEntrypoints;
}

void ContextImpl::AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) {
""",
        "ContextImpl::RetireThunkTrampolineIRHandlersInRange",
    ))

    edits.append((
        "Source/Tools/LinuxEmulation/LinuxSyscalls/ThreadManager.h",
        """  void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Start, uint64_t Length,
                                FEXCore::Context::CodeRangeInvalidationFn after_callback) {
    std::lock_guard lk(ThreadCreationMutex);

    // Potential deferred since Thread might not be valid.
    // Thread object isn't valid very early in frontend's initialization.
    // To be more optimal the frontend should provide this code with a valid Thread object earlier.
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), CallingThread);
    CTX->InvalidateCodeBuffersCodeRange(Start, Length);
    for (auto& Thread : Threads) {
      CTX->InvalidateThreadCachedCodeRange(Thread->Thread, Start, Length);
    }

    // Callback while holding the locks.
    after_callback(Start, Length);
  }

  const fextl::vector<FEX::HLE::ThreadStateObject*>* GetThreads() const {
""",
        """  void InvalidateGuestCodeRange(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Start, uint64_t Length,
                                FEXCore::Context::CodeRangeInvalidationFn after_callback) {
    std::lock_guard lk(ThreadCreationMutex);

    // Potential deferred since Thread might not be valid.
    // Thread object isn't valid very early in frontend's initialization.
    // To be more optimal the frontend should provide this code with a valid Thread object earlier.
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), CallingThread);
    CTX->InvalidateCodeBuffersCodeRange(Start, Length);
    for (auto& Thread : Threads) {
      CTX->InvalidateThreadCachedCodeRange(Thread->Thread, Start, Length);
    }

    // Callback while holding the locks.
    after_callback(Start, Length);
  }

  void RetireThunkTrampolineIRHandlersInRange(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Start, uint64_t Length) {
    std::lock_guard lk(ThreadCreationMutex);

    // Keep registration retirement and exact cache eviction in the same code
    // invalidation transaction. This is a causal diagnostic; it does not park
    // peer threads that already selected old translated code.
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), CallingThread);
    auto Entrypoints = CTX->RetireThunkTrampolineIRHandlersInRange(Start, Length);
    for (const auto Entrypoint : Entrypoints) {
      CTX->InvalidateCodeBuffersCodeEntry(Entrypoint);
      for (auto& Thread : Threads) {
        CTX->InvalidateThreadCachedCodeEntry(Thread->Thread, Entrypoint);
      }
    }
  }

  const fextl::vector<FEX::HLE::ThreadStateObject*>* GetThreads() const {
""",
        "void RetireThunkTrampolineIRHandlersInRange(FEXCore::Core::InternalThreadState* CallingThread",
    ))

    edits.append((
        "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp",
        """  }
  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);

  if (length) {
""",
        """  }

  // v2 diagnostic: ordinary guest-range invalidation cannot discover a compiled
  // CustomIR block indexed by native PFN H. Retire any H->T thunk registrations
  // whose captured guest target T was in the successfully unmapped range, then
  // evict H exactly from shared and per-thread lookup caches.
  TM.RetireThunkTrampolineIRHandlersInRange(Thread, reinterpret_cast<uint64_t>(addr), Size);

  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);

  if (length) {
""",
        "TM.RetireThunkTrampolineIRHandlersInRange(Thread, reinterpret_cast<uint64_t>(addr), Size);",
    ))

    # Check anchors before touching anything so a partial application cannot
    # happen when the checkout differs from the expected FEX-2608 source.
    for path, old, _new, marker in edits:
        text = (ROOT / path).read_text()
        if marker in text:
            continue
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{path}: expected exactly one anchor for {marker!r}, found {count}")

    if args.check_only:
        print(f"all {len(edits)} source anchors verified")
        return

    changed = 0
    for path, old, new, marker in edits:
        changed += replace_once(path, old, new, marker)

    run_check()
    print(f"exact CustomIR retirement v2: {changed} edits applied; git diff --check PASS")


if __name__ == "__main__":
    main()
