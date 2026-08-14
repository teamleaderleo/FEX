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
    if "#include <condition_variable>" not in text:
        text = text.replace("#include <atomic>\n", "#include <atomic>\n#include <condition_variable>\n", 1)
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
        '''struct GuestCallbackDescriptor {
  enum class State : uint32_t { Live, Draining, Revoked };

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

  void Release() {
    std::lock_guard lk(Mutex);
    LOGMAN_THROW_A_FMT(Active != 0, "Callback descriptor active-count underflow");
    --Active;
    if (Active == 0) {
      CV.notify_all();
    }
  }

  void BeginDrain() {
    std::lock_guard lk(Mutex);
    if (Status == State::Live) {
      Status = State::Draining;
    }
  }

  void DrainAndRevoke() {
    std::unique_lock lk(Mutex);
    CV.wait(lk, [this]() { return Active == 0; });
    Status = State::Revoked;
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
  std::condition_variable CV;
  State Status {State::Live};
  size_t Active {};
  const uintptr_t GuestUnpacker;
  const uintptr_t GuestTarget;
};
''',
        'draining callback descriptor type',
    )

    repl(
        p,
        '''    if (Descriptor->Status.load(std::memory_order_acquire) != GuestCallbackDescriptor::State::Live) {
      fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_REVOKED descriptor=%p\\n", Descriptor);
      std::_Exit(113);
    }

    if (!ThreadObject) {
''',
        '''    if (!Descriptor->TryAcquire()) {
      fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_REVOKED descriptor=%p state=%u active=%zu\\n",
              Descriptor, static_cast<unsigned>(Descriptor->GetState()), Descriptor->GetActive());
      std::_Exit(113);
    }

    struct CallbackExecutionLease final {
      GuestCallbackDescriptor* Descriptor;
      ~CallbackExecutionLease() { Descriptor->Release(); }
    } Lease {Descriptor};

    fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_ACQUIRE descriptor=%p active=%zu\\n",
            Descriptor, Descriptor->GetActive());

    if (!ThreadObject) {
''',
        'callback execution lease acquire',
    )

    repl(
        p,
        '''  fextl::vector<Transition> Transitions;

  {
''',
        '''  fextl::vector<Transition> Transitions;
  fextl::vector<GuestCallbackDescriptor*> CallbackDescriptorsToDrain;

  {
''',
        'drain collection declaration',
    )

    repl(
        p,
        '''      Descriptor->Status.store(GuestCallbackDescriptor::State::Revoked, std::memory_order_release);
      fprintf(stderr,
              "DIAG_CALLBACK_DESCRIPTOR_RETIRE trampoline=%p descriptor=%p unpacker=%#lx target=%#lx range=%#lx+%#lx\\n",
              Trampoline, Descriptor, Unpacker, Target, Base, Length);
      It = GuestcallToHostTrampoline.erase(It);
''',
        '''      Descriptor->BeginDrain();
      CallbackDescriptorsToDrain.emplace_back(Descriptor);
      fprintf(stderr,
              "DIAG_CALLBACK_DESCRIPTOR_DRAIN_BEGIN trampoline=%p descriptor=%p unpacker=%#lx target=%#lx active=%zu range=%#lx+%#lx\\n",
              Trampoline, Descriptor, Unpacker, Target, Descriptor->GetActive(), Base, Length);
      It = GuestcallToHostTrampoline.erase(It);
''',
        'begin callback descriptor drain',
    )

    repl(
        p,
        '''  auto CTX = static_cast<FEXCore::Context::Context*>(Thread->CTX);
  for (auto& Transition : Transitions) {
''',
        '''  // Do not wait while holding ThunksMutex. An already-active guest callback
  // is allowed to call another thunk while it drains, and that path may need the
  // global thunk registry. Waiting under ThunksMutex would therefore deadlock.
  for (auto* Descriptor : CallbackDescriptorsToDrain) {
    fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_DRAIN_WAIT descriptor=%p active=%zu\\n",
            Descriptor, Descriptor->GetActive());
    Descriptor->DrainAndRevoke();
    fprintf(stderr, "DIAG_CALLBACK_DESCRIPTOR_DRAIN_COMPLETE descriptor=%p active=%zu\\n",
            Descriptor, Descriptor->GetActive());
  }

  auto CTX = static_cast<FEXCore::Context::Context*>(Thread->CTX);
  for (auto& Transition : Transitions) {
''',
        'wait for callback descriptors outside thunk registry lock',
    )

    print('Added callback descriptor Active/Draining/Revoked execution lease and two-phase drain')


if __name__ == '__main__':
    main()
