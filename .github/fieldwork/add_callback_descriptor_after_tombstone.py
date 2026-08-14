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
    p = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    text = p.read_text()
    if "#include <atomic>" not in text:
        text = text.replace("#include <cstdint>\n", "#include <atomic>\n#include <cstdint>\n", 1)
    if "#include <cstdlib>" not in text:
        text = text.replace("#include <cstdint>\n", "#include <cstdint>\n#include <cstdlib>\n", 1)
    p.write_text(text)

    repl(
        p,
        '''struct TrampolineInstanceInfo {
  void* HostPacker;
  uintptr_t CallCallback;
  uintptr_t GuestUnpacker;
  uintptr_t GuestTarget;
};
''',
        '''struct TrampolineInstanceInfo {
  void* HostPacker;
  uintptr_t CallCallback;
  uintptr_t GuestUnpacker;
  uintptr_t GuestTarget;
};

struct GuestCallbackDescriptor {
  enum class State : uint32_t { Live, Revoked };

  explicit GuestCallbackDescriptor(uintptr_t Unpacker, uintptr_t Target)
    : GuestUnpacker {Unpacker}, GuestTarget {Target} {}

  std::atomic<State> Status {State::Live};
  const uintptr_t GuestUnpacker;
  const uintptr_t GuestTarget;
};
''',
        "callback descriptor type",
    )

    call_anchor = '''  static void CallCallback(void* callback, void* arg0, void* arg1) {
    if (!ThreadObject) {
      ERROR_AND_DIE_FMT("Thunked library attempted to invoke guest callback asynchronously");
    }

    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);
    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());

    if (ThunkHandler->Is64BitMode()) {
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDI] = (uintptr_t)arg0;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSI] = (uintptr_t)arg1;
    } else {
      if ((reinterpret_cast<uintptr_t>(arg1) >> 32) != 0) {
        ERROR_AND_DIE_FMT("Tried to call guest function with arguments packed to a 64-bit address");
      }
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RCX] = (uintptr_t)arg0;
      ThreadObject->Thread->CurrentFrame->State.gregs[FEXCore::X86State::REG_RDX] = (uintptr_t)arg1;
    }

    CTX->HandleCallback(ThreadObject->Thread, (uintptr_t)callback);
  }
'''
    descriptor_call = call_anchor + '''
  static void CallCallbackDescriptor(void* DescriptorV, void* UnusedTarget, void* ArgsRV) {
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
    repl(p, call_anchor, descriptor_call, "descriptor callback dispatcher")

    allocation_old = '''  memcpy(HostTrampoline, (void*)&HostToGuestTrampolineTemplate, HostToGuestTrampolineSize);
  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {
    .HostPacker = HostPacker, .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallback, .GuestUnpacker = GuestUnpacker, .GuestTarget = GuestTarget};

  ThunkHandler->GuestcallToHostTrampoline[gci] = HostTrampoline;
'''
    allocation_new = '''  memcpy(HostTrampoline, (void*)&HostToGuestTrampolineTemplate, HostToGuestTrampolineSize);
  auto* Descriptor = new GuestCallbackDescriptor {GuestUnpacker, GuestTarget};
  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {
    .HostPacker = HostPacker,
    .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallbackDescriptor,
    .GuestUnpacker = reinterpret_cast<uintptr_t>(Descriptor),
    .GuestTarget = 0};
  fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_CREATE trampoline=%p descriptor=%p unpacker=%#lx target=%#lx\\n",
          HostTrampoline, Descriptor, GuestUnpacker, GuestTarget);

  ThunkHandler->GuestcallToHostTrampoline[gci] = HostTrampoline;
'''
    repl(p, allocation_old, allocation_new, "descriptor trampoline allocation")

    repl(
        p,
        '''  LOGMAN_THROW_A_FMT(Trampoline.CallCallback == (uintptr_t)&ThunkHandler_impl::CallCallback, "Invalid trampoline at {} passed to {}",
                     fmt::ptr(TrampolineAddress), __FUNCTION__);
''',
        '''  LOGMAN_THROW_A_FMT(Trampoline.CallCallback == (uintptr_t)&ThunkHandler_impl::CallCallbackDescriptor,
                     "Invalid trampoline at {} passed to {}", fmt::ptr(TrampolineAddress), __FUNCTION__);
''',
        "descriptor finalize validation",
    )

    # Remove the raw trampoline-rewrite revoke helper introduced by the integrated
    # tombstone patch. Descriptor state now owns future-entry revocation.
    repl(
        p,
        '''static void IntegratedRevokedGuestCallback(void* GuestUnpacker, void* GuestTarget, void* ArgsRV) {
  (void)GuestUnpacker;
  (void)GuestTarget;
  (void)ArgsRV;
  fprintf(stderr, "DIAG_INTEGRATED_CALLBACK_REVOKED invoked\\n");
  std::_Exit(113);
}

''',
        '',
        "remove integrated raw revoke helper",
    )

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
      auto& Info = GetInstanceInfo(Trampoline);
      fprintf(stderr,
              "DIAG_INTEGRATED_CALLBACK_TOMBSTONE trampoline=%p unpacker=%#lx target=%#lx range=%#lx+%#lx\\n",
              Trampoline, Unpacker, Target, Base, Length);
      Info.CallCallback = reinterpret_cast<uintptr_t>(&IntegratedRevokedGuestCallback);
      Info.GuestUnpacker = 0;
      Info.GuestTarget = 0;
      It = GuestcallToHostTrampoline.erase(It);
    }
'''
    new_retire = '''    for (auto It = GuestcallToHostTrampoline.begin(); It != GuestcallToHostTrampoline.end();) {
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
    repl(p, old_retire, new_retire, "upgrade integrated tombstone retirement")

    print("Upgraded integrated callback tombstone to stable descriptor form")


if __name__ == "__main__":
    main()
