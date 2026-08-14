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

    pub = root / "FEXCore/include/FEXCore/Core/Context.h"
    repl(
        pub,
        '  FEX_DEFAULT_VISIBILITY virtual void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) = 0;',
        '''  FEX_DEFAULT_VISIBILITY virtual void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) = 0;
  FEX_DEFAULT_VISIBILITY virtual bool RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) = 0;''',
        'public definition-only retirement declaration',
    )

    impl = root / "FEXCore/Source/Interface/Context/Context.h"
    repl(
        impl,
        '  void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) override;',
        '''  void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) override;
  bool RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) override;''',
        'impl definition-only retirement declaration',
    )

    sh = root / "FEXCore/include/FEXCore/HLE/SyscallHandler.h"
    repl(
        sh,
        '  virtual void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {}',
        '''  virtual void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {}
  virtual void RetireThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {}''',
        'syscall retirement virtual',
    )

    syscalls = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/Syscalls.h"
    repl(
        syscalls,
        '  void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override { TM.InvalidateGuestCodeEntry(Thread, Address); }',
        '''  void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override { TM.InvalidateGuestCodeEntry(Thread, Address); }
  void RetireThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override {
    TM.RetireThunkTrampolineEntry(Thread, Address);
  }''',
        'linux syscall retirement override',
    )

    tm = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/ThreadManager.h"
    anchor = '''  void InvalidateGuestCodeEntry(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Address) {
    std::lock_guard lk(ThreadCreationMutex);
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), CallingThread);
    CTX->InvalidateCodeBuffersCodeEntry(Address);
    for (auto& Thread : Threads) CTX->InvalidateThreadCachedCodeEntry(Thread->Thread, Address);
  }
'''
    addition = anchor + '''
  void RetireThunkTrampolineEntry(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Address) {
    // Match the existing global invalidation order: freeze thread-list changes,
    // then exclude compilation/code mutation, then mutate the CustomIR definition.
    std::lock_guard lk(ThreadCreationMutex);
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), CallingThread);
    CTX->RemoveThunkTrampolineIRHandlerDefinition(Address);
    CTX->InvalidateCodeBuffersCodeEntry(Address);
    for (auto& Thread : Threads) {
      CTX->InvalidateThreadCachedCodeEntry(Thread->Thread, Address);
    }
  }
'''
    repl(tm, anchor, addition, 'thread-manager coherent retirement')

    core = root / "FEXCore/Source/Interface/Core/Core.cpp"
    repl(
        core,
        '''void ContextImpl::RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) {
  RemoveCustomIREntrypoint(Thread, Entrypoint);
  fprintf(stderr, "DIAG_MT_RETIRE_ALL H=%#lx thread=%p\\n", Entrypoint, Thread);
}
''',
        '''void ContextImpl::RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) {
  SyscallHandler->RetireThunkTrampolineEntry(Thread, Entrypoint);
  fprintf(stderr, "DIAG_LOCKED_RETIRE H=%#lx thread=%p\\n", Entrypoint, Thread);
}

bool ContextImpl::RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) {
  std::scoped_lock lk(CustomIRMutex);
  const auto Erased = CustomIRHandlers.erase(Entrypoint);
  HasCustomIRHandlers = !CustomIRHandlers.empty();
  fprintf(stderr, "DIAG_LOCKED_DEFINITION H=%#lx handler=%zu\\n", Entrypoint, Erased);
  return Erased != 0;
}
''',
        'coherent retirement implementation',
    )

    repl(
        core,
        '''  std::scoped_lock lk(CustomIRMutex);

  auto Erased = CustomIRHandlers.erase(Entrypoint);
  HasCustomIRHandlers = !CustomIRHandlers.empty();
  SyscallHandler->InvalidateGuestCodeEntry(Thread, Entrypoint);
  fprintf(stderr, "DIAG_MT_REMOVE_ALL H=%#lx handler=%zu\\n", Entrypoint, Erased);
''',
        '''  {
    std::scoped_lock lk(CustomIRMutex);
    CustomIRHandlers.erase(Entrypoint);
    HasCustomIRHandlers = !CustomIRHandlers.empty();
  }
  // Preserve the generic remover's existing range-invalidation behavior, but
  // do not hold CustomIRMutex while entering the code-invalidation path.
  SyscallHandler->InvalidateGuestCodeRange(Thread, Entrypoint, 1);
''',
        'restore generic remover without lock inversion',
    )

    print('Applied coherent thunk-retirement lock order')


if __name__ == '__main__':
    main()
