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
        '  FEX_DEFAULT_VISIBILITY virtual void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) = 0;\n  FEX_DEFAULT_VISIBILITY virtual bool RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) = 0;',
        '''  FEX_DEFAULT_VISIBILITY virtual void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) = 0;
  FEX_DEFAULT_VISIBILITY virtual void ActivateThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint,
                                                                       uintptr_t GuestThunkEntrypoint) = 0;
  FEX_DEFAULT_VISIBILITY virtual bool RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) = 0;
  FEX_DEFAULT_VISIBILITY virtual void AddRevokedThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) = 0;''',
        'public revoked-H API',
    )

    impl = root / "FEXCore/Source/Interface/Context/Context.h"
    repl(
        impl,
        '  void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) override;\n  bool RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) override;',
        '''  void RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) override;
  void ActivateThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint,
                                        uintptr_t GuestThunkEntrypoint) override;
  bool RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) override;
  void AddRevokedThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) override;''',
        'impl revoked-H API',
    )

    sh = root / "FEXCore/include/FEXCore/HLE/SyscallHandler.h"
    repl(
        sh,
        '  virtual void RetireThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {}',
        '''  virtual void RetireThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {}
  virtual void ActivateThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address, uint64_t GuestTarget) {}''',
        'HLE revoked-H activation API',
    )

    syscalls = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/Syscalls.h"
    repl(
        syscalls,
        '''  void RetireThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override {
    TM.RetireThunkTrampolineEntry(Thread, Address);
  }''',
        '''  void RetireThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override {
    TM.RetireThunkTrampolineEntry(Thread, Address);
  }
  void ActivateThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address, uint64_t GuestTarget) override {
    TM.ActivateThunkTrampolineEntry(Thread, Address, GuestTarget);
  }''',
        'Linux activation override',
    )

    tm = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/ThreadManager.h"
    old = '''  void RetireThunkTrampolineEntry(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Address) {
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
    new = '''  void RetireThunkTrampolineEntry(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Address) {
    // Freeze thread-list changes, then exclude compilation/code mutation before
    // changing the synthetic definition and every compiled copy of this key.
    std::lock_guard lk(ThreadCreationMutex);
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), CallingThread);
    CTX->RemoveThunkTrampolineIRHandlerDefinition(Address);
    CTX->InvalidateCodeBuffersCodeEntry(Address);
    for (auto& Thread : Threads) {
      CTX->InvalidateThreadCachedCodeEntry(Thread->Thread, Address);
    }
    CTX->AddRevokedThunkTrampolineIRHandlerDefinition(Address);
  }

  void ActivateThunkTrampolineEntry(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Address, uint64_t GuestTarget) {
    // Reactivation uses the same transaction so a compiled revoked handler (or
    // any prior active generation) cannot survive the state transition.
    std::lock_guard lk(ThreadCreationMutex);
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), CallingThread);
    CTX->RemoveThunkTrampolineIRHandlerDefinition(Address);
    CTX->InvalidateCodeBuffersCodeEntry(Address);
    for (auto& Thread : Threads) {
      CTX->InvalidateThreadCachedCodeEntry(Thread->Thread, Address);
    }
    CTX->AddThunkTrampolineIRHandler(Address, GuestTarget);
  }
'''
    repl(tm, old, new, 'ThreadManager revoked-H state transitions')

    core = root / "FEXCore/Source/Interface/Core/Core.cpp"
    repl(
        core,
        '''void ContextImpl::RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) {
  SyscallHandler->RetireThunkTrampolineEntry(Thread, Entrypoint);
  fprintf(stderr, "DIAG_LOCKED_RETIRE H=%#lx thread=%p\\n", Entrypoint, Thread);
}

bool ContextImpl::RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) {''',
        '''void ContextImpl::RetireThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint) {
  SyscallHandler->RetireThunkTrampolineEntry(Thread, Entrypoint);
  fprintf(stderr, "DIAG_LOCKED_RETIRE H=%#lx thread=%p\\n", Entrypoint, Thread);
}

void ContextImpl::ActivateThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint,
                                                    uintptr_t GuestThunkEntrypoint) {
  SyscallHandler->ActivateThunkTrampolineEntry(Thread, Entrypoint, GuestThunkEntrypoint);
  fprintf(stderr, "DIAG_REVOKED_H_ACTIVATE H=%#lx T=%#lx thread=%p\\n", Entrypoint, GuestThunkEntrypoint, Thread);
}

bool ContextImpl::RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) {''',
        'Context activation implementation',
    )

    anchor = '''bool ContextImpl::RemoveThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) {
  std::scoped_lock lk(CustomIRMutex);
  const auto Erased = CustomIRHandlers.erase(Entrypoint);
  HasCustomIRHandlers = !CustomIRHandlers.empty();
  fprintf(stderr, "DIAG_LOCKED_DEFINITION H=%#lx handler=%zu\\n", Entrypoint, Erased);
  return Erased != 0;
}
'''
    addition = anchor + r'''
void ContextImpl::AddRevokedThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) {
  auto Result = AddCustomIREntrypoint(
    Entrypoint,
    [](uintptr_t Entrypoint, FEXCore::IR::IREmitter* emit) {
      fprintf(stderr, "DIAG_REVOKED_H_COMPILE H=%#lx\n", Entrypoint);
      auto IRHeader = emit->_IRHeader(emit->Invalid(), Entrypoint, 0, 0, 0, 0);
      auto Block = emit->CreateCodeNode(true, 0);
      IRHeader.first->Blocks = emit->WrapNode(Block);
      emit->SetCurrentCodeBlock(Block);
      // Keep H synthetic after its last owner retires. A stale call exits toward
      // guest address zero instead of letting the frontend decode native host bytes.
      emit->_ExitFunction(IR::OpSize::i64Bit, emit->Constant(0), IR::BranchHint::None, emit->Invalid(), emit->Invalid());
    },
    this,
    nullptr);

  LOGMAN_THROW_A_FMT(!Result.has_value(), "Revoked synthetic H unexpectedly collided at {:#x}", Entrypoint);
  fprintf(stderr, "DIAG_REVOKED_H_INSTALL H=%#lx\n", Entrypoint);
}
'''
    repl(core, anchor, addition, 'revoked definition implementation')

    thunks = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    repl(
        thunks,
        '      CTX->AddThunkTrampolineIRHandler(Transition.Host, Transition.NewTarget);\n      fprintf(stderr, "DIAG_MULTI_PROMOTE H=%#lx T=%#lx\\n", Transition.Host, Transition.NewTarget);',
        '      CTX->ActivateThunkTrampolineIRHandler(Thread, Transition.Host, Transition.NewTarget);\n      fprintf(stderr, "DIAG_MULTI_PROMOTE H=%#lx T=%#lx\\n", Transition.Host, Transition.NewTarget);',
        'promotion activation',
    )
    repl(
        thunks,
        '      CTX->AddThunkTrampolineIRHandler(args->original_callee, args->target_addr);\n      fprintf(stderr, "DIAG_MULTI_ACTIVE H=%#lx T=%#lx\\n", args->original_callee, args->target_addr);',
        '      CTX->ActivateThunkTrampolineIRHandler(ThreadObject->Thread, args->original_callee, args->target_addr);\n      fprintf(stderr, "DIAG_MULTI_ACTIVE H=%#lx T=%#lx\\n", args->original_callee, args->target_addr);',
        'first-claim activation',
    )

    print('Added ACTIVE -> REVOKED -> ACTIVE synthetic-H state transitions')


if __name__ == '__main__':
    main()
