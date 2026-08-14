#!/usr/bin/env python3
from pathlib import Path
import sys


def repl(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()

    # ThunkOwnerCheck is private to FEX-generated H -> T bridges. Existing x86
    # call/return/TF branch hints keep their semantics unchanged.
    ir = root / "FEXCore/Source/Interface/IR/IR.h"
    repl(
        ir,
        'enum class BranchHint : uint8_t { None = 0, Call, Return, CheckTF };',
        'enum class BranchHint : uint8_t { None = 0, Call, Return, CheckTF, ThunkOwnerCheck };',
        'branch hint',
    )

    # Persist H + expected target-owner generation beside the existing exit
    # linker record. These trailing fields do not change the existing record
    # offsets used by the backpatch/delink code.
    ctx_impl = root / "FEXCore/Source/Interface/Context/Context.h"
    repl(
        ctx_impl,
        '''struct FEX_PACKED ExitFunctionLinkData {
  uint64_t HostCode;
  uint64_t GuestRIP;
  int64_t CallerOffset;
};''',
        '''struct FEX_PACKED ExitFunctionLinkData {
  uint64_t HostCode;
  uint64_t GuestRIP;
  int64_t CallerOffset;
  uint64_t ThunkHost;
  uint64_t ThunkOwnerID;
};''',
        'exit-link record token',
    )
    repl(
        ctx_impl,
        '''  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;''',
        '''  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint, uint64_t OwnerID = 0) override;''',
        'impl add-thunk owner parameter',
    )
    repl(
        ctx_impl,
        '''  void ActivateThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint,
                                        uintptr_t GuestThunkEntrypoint) override;''',
        '''  void ActivateThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint,
                                        uintptr_t GuestThunkEntrypoint, uint64_t OwnerID) override;''',
        'impl activate owner parameter',
    )

    ctx_pub = root / "FEXCore/include/FEXCore/Core/Context.h"
    repl(
        ctx_pub,
        '''  FEX_DEFAULT_VISIBILITY virtual void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) = 0;''',
        '''  FEX_DEFAULT_VISIBILITY virtual void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint,
                                                                            uint64_t OwnerID = 0) = 0;''',
        'public add-thunk owner parameter',
    )
    repl(
        ctx_pub,
        '''  FEX_DEFAULT_VISIBILITY virtual void ActivateThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint,
                                                                       uintptr_t GuestThunkEntrypoint) = 0;''',
        '''  FEX_DEFAULT_VISIBILITY virtual void ActivateThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint,
                                                                       uintptr_t GuestThunkEntrypoint, uint64_t OwnerID) = 0;''',
        'public activate owner parameter',
    )

    hle = root / "FEXCore/include/FEXCore/HLE/SyscallHandler.h"
    repl(
        hle,
        '''  virtual void ActivateThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address, uint64_t GuestTarget) {}''',
        '''  virtual void ActivateThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address, uint64_t GuestTarget,
                                                    uint64_t OwnerID) {}''',
        'HLE activation owner parameter',
    )

    syscalls_h = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/Syscalls.h"
    repl(
        syscalls_h,
        '''  void ActivateThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address, uint64_t GuestTarget) override {
    TM.ActivateThunkTrampolineEntry(Thread, Address, GuestTarget);
  }''',
        '''  void ActivateThunkTrampolineEntry(FEXCore::Core::InternalThreadState* Thread, uint64_t Address, uint64_t GuestTarget,
                                            uint64_t OwnerID) override {
    TM.ActivateThunkTrampolineEntry(Thread, Address, GuestTarget, OwnerID);
  }''',
        'Linux activation owner parameter',
    )

    tm = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/ThreadManager.h"
    repl(
        tm,
        '''  void ActivateThunkTrampolineEntry(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Address, uint64_t GuestTarget) {''',
        '''  void ActivateThunkTrampolineEntry(FEXCore::Core::InternalThreadState* CallingThread, uint64_t Address, uint64_t GuestTarget,
                                           uint64_t OwnerID) {''',
        'ThreadManager activation owner parameter',
    )
    repl(
        tm,
        '''    CTX->AddThunkTrampolineIRHandler(Address, GuestTarget);''',
        '''    CTX->AddThunkTrampolineIRHandler(Address, GuestTarget, OwnerID);''',
        'ThreadManager forwards owner',
    )

    core = root / "FEXCore/Source/Interface/Core/Core.cpp"
    repl(
        core,
        '''void ContextImpl::AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) {''',
        '''void ContextImpl::AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint, uint64_t OwnerID) {''',
        'Core add-thunk owner parameter',
    )
    repl(
        core,
        '''    [this, GuestThunkEntrypoint](uintptr_t Entrypoint, FEXCore::IR::IREmitter* emit) {''',
        '''    [this, GuestThunkEntrypoint, OwnerID](uintptr_t Entrypoint, FEXCore::IR::IREmitter* emit) {''',
        'custom IR captures owner',
    )
    repl(
        core,
        '''      emit->_ExitFunction(IR::OpSize::i64Bit, emit->Constant(GuestThunkEntrypoint), IR::BranchHint::None, emit->Invalid(), emit->Invalid());''',
        '''      const auto Hint = OwnerID ? IR::BranchHint::ThunkOwnerCheck : IR::BranchHint::None;
      const auto OwnerToken = OwnerID ? emit->Constant(OwnerID) : emit->Invalid();
      emit->_ExitFunction(IR::OpSize::i64Bit, emit->Constant(GuestThunkEntrypoint), Hint, OwnerToken, emit->Invalid());''',
        'custom IR owner-aware exit',
    )
    repl(
        core,
        '''void ContextImpl::ActivateThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint,
                                                    uintptr_t GuestThunkEntrypoint) {
  SyscallHandler->ActivateThunkTrampolineEntry(Thread, Entrypoint, GuestThunkEntrypoint);''',
        '''void ContextImpl::ActivateThunkTrampolineIRHandler(FEXCore::Core::InternalThreadState* Thread, uintptr_t Entrypoint,
                                                    uintptr_t GuestThunkEntrypoint, uint64_t OwnerID) {
  SyscallHandler->ActivateThunkTrampolineEntry(Thread, Entrypoint, GuestThunkEntrypoint, OwnerID);''',
        'Core activation owner parameter',
    )

    # Initial and promoted active claims both carry the currently live target
    # VMA owner into the synthetic H definition.
    thunks = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    repl(
        thunks,
        '''      CTX->ActivateThunkTrampolineIRHandler(ThreadObject->Thread, args->original_callee, args->target_addr);
      fprintf(stderr, "DIAG_OWNER_CLAIM_ACTIVE H=%#lx T=%#lx owner=%#lx new=%d\\n",''',
        '''      CTX->ActivateThunkTrampolineIRHandler(ThreadObject->Thread, args->original_callee, args->target_addr, OwnerID);
      fprintf(stderr, "DIAG_OWNER_CLAIM_ACTIVE H=%#lx T=%#lx owner=%#lx new=%d\\n",''',
        'first claim carries owner',
    )
    repl(
        thunks,
        '''      CTX->ActivateThunkTrampolineIRHandler(Thread, Transition.Host, Transition.NewTarget);
      fprintf(stderr, "DIAG_MULTI_PROMOTE H=%#lx T=%#lx\\n", Transition.Host, Transition.NewTarget);''',
        '''      const uint64_t NewOwnerID = FEX::HLE::_SyscallHandler->QueryGuestMappingOwner(Thread, Transition.NewTarget);
      CTX->ActivateThunkTrampolineIRHandler(Thread, Transition.Host, Transition.NewTarget, NewOwnerID);
      fprintf(stderr, "DIAG_MULTI_PROMOTE H=%#lx T=%#lx owner=%#lx\\n", Transition.Host, Transition.NewTarget, NewOwnerID);''',
        'promoted claim carries owner',
    )

    # The branch backend stores owner metadata in the same emitted jump thunk
    # record that already survives backpatch and target invalidation.
    jitclass = root / "FEXCore/Source/Interface/Core/JIT/JITClass.h"
    repl(
        jitclass,
        '''  struct PendingJumpThunk {
    uint64_t CallerAddress;
    uint64_t GuestRIP;
    ARMEmitter::ForwardLabel Label;
  };''',
        '''  struct PendingJumpThunk {
    uint64_t CallerAddress;
    uint64_t GuestRIP;
    uint64_t ThunkHost;
    uint64_t ThunkOwnerID;
    ARMEmitter::ForwardLabel Label;
  };''',
        'pending jump owner token',
    )
    repl(
        jitclass,
        '''    PendingJumpThunks.push_back({GetCursorAddress<uint64_t>(), GuestRIP, {}});''',
        '''    PendingJumpThunks.push_back({GetCursorAddress<uint64_t>(), GuestRIP, 0, 0, {}});''',
        'regular jump zero token',
    )
    repl(
        jitclass,
        '''  Utils::PoolBufferWithTimedRetirement<uint8_t*, 5000, 500> TempCodeBufferAllocator;''',
        '''  void EmitLinkedThunkOwnerBranch(uint64_t GuestRIP, uint64_t ThunkHost, uint64_t ThunkOwnerID) {
    PendingJumpThunks.push_back({GetCursorAddress<uint64_t>(), GuestRIP, ThunkHost, ThunkOwnerID, {}});
    auto& Thunk = PendingJumpThunks.back();
    BindOrRestart(&Thunk.Label);
    b_OrRestart(&Thunk.Label);
  }

  Utils::PoolBufferWithTimedRetirement<uint8_t*, 5000, 500> TempCodeBufferAllocator;''',
        'owner-aware jump emitter',
    )

    branch = root / "FEXCore/Source/Interface/Core/JIT/BranchOps.cpp"
    repl(
        branch,
        '''      EmitLinkedBranch(NewRIP, Op->Hint == IR::BranchHint::Call);
      (void)Bind(&l_CallReturn);''',
        '''      if (Op->Hint == IR::BranchHint::ThunkOwnerCheck) {
        uint64_t OwnerID {};
        LOGMAN_THROW_A_FMT(IsInlineConstant(Op->CallReturnAddress, &OwnerID) && OwnerID,
                           "ThunkOwnerCheck requires an inline owner token");
        EmitLinkedThunkOwnerBranch(NewRIP, Entry, OwnerID);
      } else {
        EmitLinkedBranch(NewRIP, Op->Hint == IR::BranchHint::Call);
      }
      (void)Bind(&l_CallReturn);''',
        'lower owner-aware exit',
    )

    jit = root / "FEXCore/Source/Interface/Core/JIT/JIT.cpp"
    repl(
        jit,
        '''    dc64(PendingJumpThunk.CallerAddress - ThunkAddress);                       // CallerOffset
  }''',
        '''    dc64(PendingJumpThunk.CallerAddress - ThunkAddress);                       // CallerOffset
    dc64(PendingJumpThunk.ThunkHost);                                                     // ThunkHost
    dc64(PendingJumpThunk.ThunkOwnerID);                                                  // ThunkOwnerID
  }''',
        'emit owner-token record',
    )
    # The deterministic race hook is applied before this script in the repair
    # workflow. Validation therefore runs after the worker is released from the
    # exact old-H/before-T-selection barrier.
    repl(
        jit,
        '''  if (std::getenv("FEX_DIAG_INFLIGHT_SELECT")) {
    DiagnosticPauseBeforeTargetSelection(GuestRip);
  }

  if (TFSet) {''',
        '''  if (std::getenv("FEX_DIAG_INFLIGHT_SELECT")) {
    DiagnosticPauseBeforeTargetSelection(GuestRip);
  }

  if (Record->ThunkOwnerID) {
    auto* CTX = static_cast<Context::ContextImpl*>(Thread->CTX);
    const uint64_t CurrentOwner = CTX->SyscallHandler->QueryGuestMappingOwner(Thread, GuestRip);
    if (CurrentOwner != Record->ThunkOwnerID) {
      fprintf(stderr,
              "DIAG_OWNER_EXIT_REJECT H=%#lx T=%#lx expected=%#lx current=%#lx\\n",
              Record->ThunkHost, GuestRip, Record->ThunkOwnerID, CurrentOwner);
      GuestRip = Record->ThunkHost;
    } else {
      fprintf(stderr,
              "DIAG_OWNER_EXIT_ACCEPT H=%#lx T=%#lx owner=%#lx\\n",
              Record->ThunkHost, GuestRip, CurrentOwner);
    }
  }

  if (TFSet) {''',
        'validate owner after diagnostic barrier',
    )

    print('Added owner-generation token to synthetic H exit-link records')


if __name__ == '__main__':
    main()
