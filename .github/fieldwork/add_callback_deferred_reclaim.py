#!/usr/bin/env python3
from pathlib import Path
import sys


def repl(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()

    th = root / "Source/Tools/LinuxEmulation/Thunks.h"
    repl(
        th,
        "  virtual void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;",
        "  virtual bool RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;",
        "RetireGuestRange interface return",
    )

    p = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    text = p.read_text()
    if "#include <optional>" not in text:
        text = text.replace("#include <mutex>\n", "#include <mutex>\n#include <optional>\n", 1)
    p.write_text(text)

    repl(
        p,
        '''struct GuestCallbackDescriptor {
  enum class State : uint32_t { Live, Revoked };

  explicit GuestCallbackDescriptor(uintptr_t Unpacker, uintptr_t Target)
    : GuestUnpacker {Unpacker}, GuestTarget {Target} {}

  std::atomic<State> Status {State::Live};
  const uintptr_t GuestUnpacker;
  const uintptr_t GuestTarget;
};
''',
        '''struct DeferredGuestUnmap {
  uintptr_t Base;
  uint64_t Length;
};

struct GuestCallbackDescriptor {
  enum class State : uint32_t { Live, Retired };

  explicit GuestCallbackDescriptor(uintptr_t Unpacker, uintptr_t Target)
    : GuestUnpacker {Unpacker}, GuestTarget {Target} {}

  bool TryAcquire() {
    std::lock_guard lk(Mutex);
    if (Status != State::Live) {
      return false;
    }
    ++Active;
    return true;
  }

  fextl::vector<DeferredGuestUnmap> ReleaseAndTakeDeferred() {
    std::lock_guard lk(Mutex);
    LOGMAN_THROW_A_FMT(Active != 0, "Callback descriptor active-count underflow");
    --Active;
    fextl::vector<DeferredGuestUnmap> Result;
    if (Active == 0 && Status == State::Retired) {
      Result.swap(DeferredUnmaps);
    }
    return Result;
  }

  bool RetireAndMaybeDefer(uintptr_t Base, uint64_t Length) {
    std::lock_guard lk(Mutex);
    Status = State::Retired;
    if (Active != 0) {
      DeferredUnmaps.push_back({Base, Length});
      return true;
    }
    return false;
  }

  State GetState() {
    std::lock_guard lk(Mutex);
    return Status;
  }

  size_t GetActive() {
    std::lock_guard lk(Mutex);
    return Active;
  }

  std::mutex Mutex;
  State Status {State::Live};
  size_t Active {};
  fextl::vector<DeferredGuestUnmap> DeferredUnmaps;
  const uintptr_t GuestUnpacker;
  const uintptr_t GuestTarget;
};
''',
        "nonblocking callback descriptor",
    )

    repl(
        p,
        '''  void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;

  void AppendThunkDefinitions''',
        '''  bool RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;

  void AppendThunkDefinitions''',
        "RetireGuestRange class declaration",
    )

    old_call = '''  static void CallCallbackDescriptor(void* DescriptorV, void* UnusedTarget, void* ArgsRV) {
    (void)UnusedTarget;
    auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(DescriptorV);
    LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Host callback trampoline has no descriptor");

    if (Descriptor->Status.load(std::memory_order_acquire) != GuestCallbackDescriptor::State::Live) {
      fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_REVOKED descriptor=%p\\n", Descriptor);
      std::_Exit(113);
    }

    if (!ThreadObject) {
      ERROR_AND_DIE_FMT("Thunked library attempted to invoke guest callback asynchronously");
    }

    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);
    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
    const uintptr_t GuestUnpacker = Descriptor->GuestUnpacker;
    const uintptr_t GuestTarget = Descriptor->GuestTarget;

    if (ThunkHandler->Is64BitMode()) {
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDI] = GuestTarget;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSI] = reinterpret_cast<uintptr_t>(ArgsRV);
    } else {
      if ((reinterpret_cast<uintptr_t>(ArgsRV) >> 32) != 0 || (GuestTarget >> 32) != 0) {
        ERROR_AND_DIE_FMT("Tried to call guest function with arguments packed to a 64-bit address");
      }
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RCX] = GuestTarget;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDX] = reinterpret_cast<uintptr_t>(ArgsRV);
    }

    fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_LIVE descriptor=%p unpacker=%#lx target=%#lx\\n", Descriptor, GuestUnpacker, GuestTarget);
    CTX->HandleCallback(ThreadObject->Thread, GuestUnpacker);
  }
'''
    new_call = '''  void ReleaseCallbackLease(FEXCore::Core::InternalThreadState* Thread, GuestCallbackDescriptor* Descriptor) {
    auto Deferred = Descriptor->ReleaseAndTakeDeferred();
    fprintf(stderr, "DIAG_CALLBACK_LEASE_RELEASE descriptor=%p active=%zu deferred=%zu\\n",
            Descriptor, Descriptor->GetActive(), Deferred.size());
    for (const auto& Range : Deferred) {
      fprintf(stderr, "DIAG_CALLBACK_DEFERRED_RECLAIM_BEGIN descriptor=%p range=%#lx+%#lx\\n",
              Descriptor, Range.Base, Range.Length);
      const auto Result = FEX::HLE::_SyscallHandler->GuestMunmap(
        Is64BitMode(), Thread, reinterpret_cast<void*>(Range.Base), Range.Length);
      fprintf(stderr, "DIAG_CALLBACK_DEFERRED_RECLAIM_DONE descriptor=%p range=%#lx+%#lx result=%#lx\\n",
              Descriptor, Range.Base, Range.Length, Result);
      LOGMAN_THROW_A_FMT(Result == 0, "Deferred callback-owner munmap failed: {:#x}", Result);
    }
  }

  static void CallCallbackDescriptor(void* DescriptorV, void* UnusedTarget, void* ArgsRV) {
    (void)UnusedTarget;
    auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(DescriptorV);
    LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Host callback trampoline has no descriptor");

    if (!Descriptor->TryAcquire()) {
      fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_REVOKED descriptor=%p state=%u active=%zu\\n",
              Descriptor, static_cast<unsigned>(Descriptor->GetState()), Descriptor->GetActive());
      std::_Exit(113);
    }

    if (!ThreadObject) {
      Descriptor->ReleaseAndTakeDeferred();
      ERROR_AND_DIE_FMT("Thunked library attempted to invoke guest callback asynchronously");
    }

    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);
    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
    const uintptr_t GuestUnpacker = Descriptor->GuestUnpacker;
    const uintptr_t GuestTarget = Descriptor->GuestTarget;

    struct CallbackExecutionLease final {
      ThunkHandler_impl* Handler;
      FEXCore::Core::InternalThreadState* Thread;
      GuestCallbackDescriptor* Descriptor;
      ~CallbackExecutionLease() { Handler->ReleaseCallbackLease(Thread, Descriptor); }
    } Lease {ThunkHandler, ThreadObject->Thread, Descriptor};

    fprintf(stderr, "DIAG_CALLBACK_LEASE_ACQUIRE descriptor=%p active=%zu unpacker=%#lx target=%#lx\\n",
            Descriptor, Descriptor->GetActive(), GuestUnpacker, GuestTarget);

    if (ThunkHandler->Is64BitMode()) {
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDI] = GuestTarget;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSI] = reinterpret_cast<uintptr_t>(ArgsRV);
    } else {
      if ((reinterpret_cast<uintptr_t>(ArgsRV) >> 32) != 0 || (GuestTarget >> 32) != 0) {
        ERROR_AND_DIE_FMT("Tried to call guest function with arguments packed to a 64-bit address");
      }
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RCX] = GuestTarget;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDX] = reinterpret_cast<uintptr_t>(ArgsRV);
    }

    CTX->HandleCallback(ThreadObject->Thread, GuestUnpacker);
  }
'''
    repl(p, old_call, new_call, "callback lease dispatcher")

    text = p.read_text()
    old_sig = "void ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {\n  if (!Thread || !Length) return;"
    new_sig = "bool ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {\n  if (!Thread || !Length) return false;\n  bool DeferPhysicalUnmap = false;"
    if text.count(old_sig) != 1:
      raise SystemExit(f"retire definition signature: expected one anchor, found {text.count(old_sig)}")
    p.write_text(text.replace(old_sig, new_sig, 1))

    repl(
        p,
        '''      Descriptor->Status.store(GuestCallbackDescriptor::State::Revoked, std::memory_order_release);
      fprintf(stderr,
              "DIAG_CALLBACK_DESCRIPTOR_RETIRE trampoline=%p descriptor=%p unpacker=%#lx target=%#lx range=%#lx+%#lx\\n",
              Trampoline, Descriptor, Unpacker, Target, Base, Length);
      It = GuestcallToHostTrampoline.erase(It);
''',
        '''      const bool Deferred = Descriptor->RetireAndMaybeDefer(Base, Length);
      DeferPhysicalUnmap |= Deferred;
      fprintf(stderr,
              "DIAG_CALLBACK_DESCRIPTOR_RETIRE trampoline=%p descriptor=%p unpacker=%#lx target=%#lx active=%zu defer=%d range=%#lx+%#lx\\n",
              Trampoline, Descriptor, Unpacker, Target, Descriptor->GetActive(), Deferred ? 1 : 0, Base, Length);
      It = GuestcallToHostTrampoline.erase(It);
''',
        "nonblocking callback retirement",
    )

    text = p.read_text()
    tail = '''      fprintf(stderr, "DIAG_MULTI_PROMOTE H=%#lx T=%#lx\\n", Transition.Host, Transition.NewTarget);
    }
  }
}

FEX_DEFAULT_VISIBILITY HostToGuestTrampolinePtr*
'''
    replacement = '''      fprintf(stderr, "DIAG_MULTI_PROMOTE H=%#lx T=%#lx\\n", Transition.Host, Transition.NewTarget);
    }
  }
  return DeferPhysicalUnmap;
}

FEX_DEFAULT_VISIBILITY HostToGuestTrampolinePtr*
'''
    if text.count(tail) != 1:
      raise SystemExit(f"retire return anchor: expected one anchor, found {text.count(tail)}")
    p.write_text(text.replace(tail, replacement, 1))

    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"
    repl(
        smc,
        '''  if (Thread && Size) {
    if (auto* Thunks = GetThunkHandler()) {
      Thunks->RetireGuestRange(Thread, reinterpret_cast<uintptr_t>(addr), Size);
    }
  }

  {
''',
        '''  if (Thread && Size) {
    if (auto* Thunks = GetThunkHandler()) {
      if (Thunks->RetireGuestRange(Thread, reinterpret_cast<uintptr_t>(addr), Size)) {
        fprintf(stderr, "DIAG_CALLBACK_DEFER_HOST_UNMAP range=%p+%#lx\\n", addr, Size);
        return 0;
      }
    }
  }

  {
''',
        "defer GuestMunmap while callback leased",
    )

    print("Added nonblocking callback lease + diagnostic deferred guest munmap")


if __name__ == "__main__":
    main()
