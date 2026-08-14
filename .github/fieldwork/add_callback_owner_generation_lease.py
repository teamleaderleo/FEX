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
    if "#include <mutex>" not in text:
        text = text.replace("#include <memory>\n", "#include <memory>\n#include <mutex>\n", 1)
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

struct GuestCallbackOwnerGeneration {
  enum class State : uint32_t { Live, Retired };

  explicit GuestCallbackOwnerGeneration(uint64_t ID)
    : OwnerID {ID} {}

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
    LOGMAN_THROW_A_FMT(Active != 0, "Callback owner active-count underflow");
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
  const uint64_t OwnerID;
};

struct GuestCallbackDescriptor {
  GuestCallbackDescriptor(uintptr_t Unpacker, uintptr_t Target, GuestCallbackOwnerGeneration* Owner_)
    : GuestUnpacker {Unpacker}, GuestTarget {Target}, Owner {Owner_} {}

  const uintptr_t GuestUnpacker;
  const uintptr_t GuestTarget;
  GuestCallbackOwnerGeneration* const Owner;
};
''',
        "owner-generation callback descriptor",
    )

    repl(
        p,
        '''  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;

  uint8_t* HostTrampolineInstanceDataPtr;''',
        '''  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;
  // Diagnostic process-lifetime registry. Product code should give owner generations
  // explicit reclamation independent of the stable escaped trampoline lifetime.
  fextl::unordered_map<uint64_t, GuestCallbackOwnerGeneration*> CallbackOwnerGenerations;

  uint8_t* HostTrampolineInstanceDataPtr;''',
        "callback owner registry",
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
    new_call = '''  void ReleaseCallbackOwnerLease(FEXCore::Core::InternalThreadState* Thread, GuestCallbackDescriptor* Descriptor) {
    auto* Owner = Descriptor->Owner;
    auto Deferred = Owner->ReleaseAndTakeDeferred();
    fprintf(stderr, "DIAG_CALLBACK_OWNER_RELEASE owner=%#lx active=%zu deferred=%zu\\n",
            Owner->OwnerID, Owner->GetActive(), Deferred.size());
    for (const auto& Range : Deferred) {
      fprintf(stderr, "DIAG_CALLBACK_OWNER_RECLAIM_BEGIN owner=%#lx range=%#lx+%#lx\\n",
              Owner->OwnerID, Range.Base, Range.Length);
      const auto Result = FEX::HLE::_SyscallHandler->GuestMunmap(
        Is64BitMode(), Thread, reinterpret_cast<void*>(Range.Base), Range.Length);
      fprintf(stderr, "DIAG_CALLBACK_OWNER_RECLAIM_DONE owner=%#lx range=%#lx+%#lx result=%#lx\\n",
              Owner->OwnerID, Range.Base, Range.Length, Result);
      LOGMAN_THROW_A_FMT(Result == 0, "Deferred callback-owner munmap failed: {:#x}", Result);
    }
  }

  static void CallCallbackDescriptor(void* DescriptorV, void* UnusedTarget, void* ArgsRV) {
    (void)UnusedTarget;
    auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(DescriptorV);
    LOGMAN_THROW_A_FMT(Descriptor != nullptr && Descriptor->Owner != nullptr, "Host callback trampoline has no owner descriptor");

    if (!ThreadObject) {
      ERROR_AND_DIE_FMT("Thunked library attempted to invoke guest callback asynchronously");
    }

    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);
    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
    auto* Owner = Descriptor->Owner;
    if (!Owner->TryAcquire()) {
      fprintf(stderr, "DIAG_CALLBACK_OWNER_REVOKED owner=%#lx descriptor=%p state=%u active=%zu\\n",
              Owner->OwnerID, Descriptor, static_cast<unsigned>(Owner->GetState()), Owner->GetActive());
      std::_Exit(113);
    }

    struct CallbackOwnerLease final {
      ThunkHandler_impl* Handler;
      FEXCore::Core::InternalThreadState* Thread;
      GuestCallbackDescriptor* Descriptor;
      ~CallbackOwnerLease() { Handler->ReleaseCallbackOwnerLease(Thread, Descriptor); }
    } Lease {ThunkHandler, ThreadObject->Thread, Descriptor};

    const uintptr_t GuestUnpacker = Descriptor->GuestUnpacker;
    const uintptr_t GuestTarget = Descriptor->GuestTarget;
    fprintf(stderr, "DIAG_CALLBACK_OWNER_ACQUIRE owner=%#lx active=%zu descriptor=%p unpacker=%#lx target=%#lx\\n",
            Owner->OwnerID, Owner->GetActive(), Descriptor, GuestUnpacker, GuestTarget);

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
    repl(p, old_call, new_call, "owner-generation callback dispatcher")

    repl(
        p,
        '''  const GuestcallInfo gci = {GuestUnpacker, GuestTarget};

  // Try first with shared_lock''',
        '''  const GuestcallInfo gci = {GuestUnpacker, GuestTarget};
  LOGMAN_THROW_A_FMT(ThreadObject != nullptr, "Guest callback registration requires guest thread state");
  const uint64_t UnpackerOwnerID = FEX::HLE::_SyscallHandler->QueryGuestMappingOwner(ThreadObject->Thread, GuestUnpacker);
  const uint64_t TargetOwnerID = FEX::HLE::_SyscallHandler->QueryGuestMappingOwner(ThreadObject->Thread, GuestTarget);
  LOGMAN_THROW_A_FMT(UnpackerOwnerID != 0 && TargetOwnerID != 0,
                     "Diagnostic callback owner IDs must be non-zero: unpacker={:#x} target={:#x}", UnpackerOwnerID, TargetOwnerID);
  LOGMAN_THROW_A_FMT(UnpackerOwnerID == TargetOwnerID,
                     "Diagnostic owner-generation prototype requires one owner: unpacker={} target={}", UnpackerOwnerID, TargetOwnerID);

  // Try first with shared_lock''',
        "query callback owner identity before thunk lock",
    )

    old_alloc = '''  auto* Descriptor = new GuestCallbackDescriptor {GuestUnpacker, GuestTarget};
  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {
    .HostPacker = HostPacker,
    .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallbackDescriptor,
    .GuestUnpacker = reinterpret_cast<uintptr_t>(Descriptor),
    .GuestTarget = 0};
  fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_CREATE trampoline=%p descriptor=%p unpacker=%#lx target=%#lx\\n",
          HostTrampoline, Descriptor, GuestUnpacker, GuestTarget);
'''
    new_alloc = '''  auto OwnerIt = ThunkHandler->CallbackOwnerGenerations.find(TargetOwnerID);
  if (OwnerIt == ThunkHandler->CallbackOwnerGenerations.end()) {
    auto* NewOwner = new GuestCallbackOwnerGeneration {TargetOwnerID};
    OwnerIt = ThunkHandler->CallbackOwnerGenerations.emplace(TargetOwnerID, NewOwner).first;
    fprintf(stderr, "DIAG_CALLBACK_OWNER_CREATE owner=%#lx object=%p\\n", TargetOwnerID, NewOwner);
  }
  auto* Descriptor = new GuestCallbackDescriptor {GuestUnpacker, GuestTarget, OwnerIt->second};
  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {
    .HostPacker = HostPacker,
    .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallbackDescriptor,
    .GuestUnpacker = reinterpret_cast<uintptr_t>(Descriptor),
    .GuestTarget = 0};
  fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_CREATE trampoline=%p descriptor=%p owner=%#lx unpacker=%#lx target=%#lx\\n",
          HostTrampoline, Descriptor, TargetOwnerID, GuestUnpacker, GuestTarget);
'''
    repl(p, old_alloc, new_alloc, "owner-generation trampoline allocation")

    text = p.read_text()
    old_sig = "void ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {\n  if (!Thread || !Length) return;"
    new_sig = "bool ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {\n  if (!Thread || !Length) return false;\n  const uint64_t RetiringOwnerID = FEX::HLE::_SyscallHandler->QueryGuestMappingOwner(Thread, Base);\n  bool DeferPhysicalUnmap = false;"
    if text.count(old_sig) != 1:
        raise SystemExit(f"retire definition signature: expected one anchor, found {text.count(old_sig)}")
    p.write_text(text.replace(old_sig, new_sig, 1))

    old_retire = '''    for (auto It = GuestcallToHostTrampoline.begin(); It != GuestcallToHostTrampoline.end();) {
      const auto Unpacker = It->first.GuestUnpacker;
      const auto Target = It->first.GuestTarget;
      const bool UnpackerInRange = Unpacker >= Base && (Unpacker - Base) < Length;
      const bool TargetInRange = Target >= Base && (Target - Base) < Length;
      if (!UnpackerInRange && !TargetInRange) {
        ++It;
        continue;
      }

      auto* Trampoline = It->second;
      auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(GetInstanceInfo(Trampoline).GuestUnpacker);
      LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Retiring callback trampoline without descriptor");
      Descriptor->Status.store(GuestCallbackDescriptor::State::Revoked, std::memory_order_release);
      fprintf(stderr,
              "DIAG_CALLBACK_DESCRIPTOR_RETIRE trampoline=%p descriptor=%p unpacker=%#lx target=%#lx range=%#lx+%#lx\\n",
              Trampoline, Descriptor, Unpacker, Target, Base, Length);
      It = GuestcallToHostTrampoline.erase(It);
    }

'''
    new_retire = '''    GuestCallbackOwnerGeneration* RetiringOwner {};
    if (RetiringOwnerID != 0) {
      auto OwnerIt = CallbackOwnerGenerations.find(RetiringOwnerID);
      if (OwnerIt != CallbackOwnerGenerations.end()) {
        RetiringOwner = OwnerIt->second;
        const bool Deferred = RetiringOwner->RetireAndMaybeDefer(Base, Length);
        DeferPhysicalUnmap |= Deferred;
        fprintf(stderr,
                "DIAG_CALLBACK_OWNER_RETIRE owner=%#lx active=%zu defer=%d range=%#lx+%#lx\\n",
                RetiringOwnerID, RetiringOwner->GetActive(), Deferred ? 1 : 0, Base, Length);
      }
    }

    for (auto It = GuestcallToHostTrampoline.begin(); It != GuestcallToHostTrampoline.end();) {
      auto* Trampoline = It->second;
      auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(GetInstanceInfo(Trampoline).GuestUnpacker);
      LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Retiring callback trampoline without descriptor");
      if (!RetiringOwner || Descriptor->Owner != RetiringOwner) {
        ++It;
        continue;
      }
      fprintf(stderr,
              "DIAG_CALLBACK_DESCRIPTOR_RETIRE trampoline=%p descriptor=%p owner=%#lx unpacker=%#lx target=%#lx range=%#lx+%#lx\\n",
              Trampoline, Descriptor, RetiringOwnerID, Descriptor->GuestUnpacker, Descriptor->GuestTarget, Base, Length);
      It = GuestcallToHostTrampoline.erase(It);
    }

'''
    repl(p, old_retire, new_retire, "owner-generation callback retirement")

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
        fprintf(stderr, "DIAG_CALLBACK_OWNER_DEFER_HOST_UNMAP range=%p+%#lx\\n", addr, Size);
        return 0;
      }
    }
  }

  {
''',
        "defer GuestMunmap for active owner generation",
    )

    print("Promoted callback execution lease to shared VMA owner-generation state")


if __name__ == "__main__":
    main()
