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

    # Give ExitFunction a true immediate generation field. Unlike an SSA/GPR
    # operand this survives register allocation as definition metadata.
    ir_json = root / "FEXCore/Source/Interface/IR/IR.json"
    repl(
        ir_json,
        '"ExitFunction OpSize:#Size, GPR:$NewRIP, BranchHint:$Hint, GPR:$CallReturnAddress, SSA:$CallReturnBlock": {',
        '"ExitFunction OpSize:#Size, GPR:$NewRIP, BranchHint:$Hint, GPR:$CallReturnAddress, SSA:$CallReturnBlock, u64:$ThunkGeneration{0}": {',
        'ExitFunction generation immediate',
    )

    ir_h = root / "FEXCore/Source/Interface/IR/IR.h"
    repl(
        ir_h,
        'enum class BranchHint : uint8_t { None = 0, Call, Return, CheckTF };',
        'enum class BranchHint : uint8_t { None = 0, Call, Return, CheckTF, ThunkGenerationCheck };',
        'generation branch hint',
    )

    dump = root / "FEXCore/Source/Interface/IR/IRDumper.cpp"
    repl(
        dump,
        '''    case BranchHint::CheckTF: return "CheckTF";\n    }\n    return "<Unknown Branch Hint>";''',
        '''    case BranchHint::CheckTF: return "CheckTF";\n    case BranchHint::ThunkGenerationCheck: return "ThunkGenerationCheck";\n    }\n    return "<Unknown Branch Hint>";''',
        'generation hint dumper',
    )

    ctx_h = root / "FEXCore/Source/Interface/Context/Context.h"
    repl(
        ctx_h,
        '''struct FEX_PACKED ExitFunctionLinkData {\n  uint64_t HostCode;\n  uint64_t GuestRIP;\n  int64_t CallerOffset;\n};''',
        '''struct FEX_PACKED ExitFunctionLinkData {\n  uint64_t HostCode;\n  uint64_t GuestRIP;\n  int64_t CallerOffset;\n  uint64_t ThunkHost;\n  uint64_t ThunkGeneration;\n};''',
        'exit-link generation record',
    )
    repl(
        ctx_h,
        '''  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;''',
        '''  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;\n  uint64_t BumpThunkTrampolineGeneration(uintptr_t Entrypoint);\n  uint64_t GetThunkTrampolineGeneration(uintptr_t Entrypoint);''',
        'generation API declarations',
    )
    repl(
        ctx_h,
        '''  fextl::unordered_map<uint64_t, CustomIRHandlerEntry> CustomIRHandlers;\n  IntervalList<uint64_t> ForceTSOValidRanges;''',
        '''  fextl::unordered_map<uint64_t, CustomIRHandlerEntry> CustomIRHandlers;\n  fextl::unordered_map<uint64_t, uint64_t> ThunkGenerations;\n  IntervalList<uint64_t> ForceTSOValidRanges;''',
        'generation storage',
    )

    core = root / "FEXCore/Source/Interface/Core/Core.cpp"
    repl(
        core,
        '''  LogMan::Msg::DFmt("Thunks: Adding guest trampoline from address {:#x} to guest function {:#x}", Entrypoint, GuestThunkEntrypoint);\n\n  auto Result = AddCustomIREntrypoint(\n    Entrypoint,\n    [this, GuestThunkEntrypoint](uintptr_t Entrypoint, FEXCore::IR::IREmitter* emit) {''',
        '''  LogMan::Msg::DFmt("Thunks: Adding guest trampoline from address {:#x} to guest function {:#x}", Entrypoint, GuestThunkEntrypoint);\n\n  const uint64_t Generation = BumpThunkTrampolineGeneration(Entrypoint);\n  fprintf(stderr, "DIAG_HGEN_ACTIVE H=%#lx generation=%#lx T=%#lx\\n", Entrypoint, Generation, GuestThunkEntrypoint);\n\n  auto Result = AddCustomIREntrypoint(\n    Entrypoint,\n    [this, GuestThunkEntrypoint, Generation](uintptr_t Entrypoint, FEXCore::IR::IREmitter* emit) {''',
        'active definition generation snapshot',
    )
    repl(
        core,
        '''      emit->_ExitFunction(IR::OpSize::i64Bit, emit->Constant(GuestThunkEntrypoint), IR::BranchHint::None, emit->Invalid(), emit->Invalid());''',
        '''      emit->_ExitFunction(IR::OpSize::i64Bit, emit->Constant(GuestThunkEntrypoint), IR::BranchHint::ThunkGenerationCheck,\n                          emit->Invalid(), emit->Invalid(), Generation);''',
        'active H generation exit',
    )

    add_force_anchor = '''void ContextImpl::AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) {'''
    generation_methods = '''uint64_t ContextImpl::BumpThunkTrampolineGeneration(uintptr_t Entrypoint) {\n  std::unique_lock lk(CustomIRMutex);\n  auto& Generation = ThunkGenerations[Entrypoint];\n  ++Generation;\n  if (Generation == 0) {\n    ++Generation;\n  }\n  return Generation;\n}\n\nuint64_t ContextImpl::GetThunkTrampolineGeneration(uintptr_t Entrypoint) {\n  std::shared_lock lk(CustomIRMutex);\n  auto It = ThunkGenerations.find(Entrypoint);\n  return It == ThunkGenerations.end() ? 0 : It->second;\n}\n\n'''
    repl(core, add_force_anchor, generation_methods + add_force_anchor, 'generation method implementations')

    repl(
        core,
        '''void ContextImpl::AddRevokedThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) {\n  auto Result = AddCustomIREntrypoint(''',
        '''void ContextImpl::AddRevokedThunkTrampolineIRHandlerDefinition(uintptr_t Entrypoint) {\n  const uint64_t Generation = BumpThunkTrampolineGeneration(Entrypoint);\n  fprintf(stderr, "DIAG_HGEN_REVOKED H=%#lx generation=%#lx\\n", Entrypoint, Generation);\n  auto Result = AddCustomIREntrypoint(''',
        'revoked definition generation advance',
    )

    jitclass = root / "FEXCore/Source/Interface/Core/JIT/JITClass.h"
    repl(
        jitclass,
        '''  struct PendingJumpThunk {\n    uint64_t CallerAddress;\n    uint64_t GuestRIP;\n    ARMEmitter::ForwardLabel Label;\n  };''',
        '''  struct PendingJumpThunk {\n    uint64_t CallerAddress;\n    uint64_t GuestRIP;\n    uint64_t ThunkHost;\n    uint64_t ThunkGeneration;\n    ARMEmitter::ForwardLabel Label;\n  };''',
        'pending jump generation token',
    )
    repl(
        jitclass,
        '''    PendingJumpThunks.push_back({GetCursorAddress<uint64_t>(), GuestRIP, {}});''',
        '''    PendingJumpThunks.push_back({GetCursorAddress<uint64_t>(), GuestRIP, 0, 0, {}});''',
        'regular jump zero generation',
    )
    repl(
        jitclass,
        '''  Utils::PoolBufferWithTimedRetirement<uint8_t*, 5000, 500> TempCodeBufferAllocator;''',
        '''  void EmitLinkedThunkGenerationBranch(uint64_t GuestRIP, uint64_t ThunkHost, uint64_t ThunkGeneration) {\n    PendingJumpThunks.push_back({GetCursorAddress<uint64_t>(), GuestRIP, ThunkHost, ThunkGeneration, {}});\n    auto& Thunk = PendingJumpThunks.back();\n    BindOrRestart(&Thunk.Label);\n    b_OrRestart(&Thunk.Label);\n  }\n\n  Utils::PoolBufferWithTimedRetirement<uint8_t*, 5000, 500> TempCodeBufferAllocator;''',
        'generation-aware jump emitter',
    )

    branch = root / "FEXCore/Source/Interface/Core/JIT/BranchOps.cpp"
    repl(
        branch,
        '''      EmitLinkedBranch(NewRIP, Op->Hint == IR::BranchHint::Call);\n      (void)Bind(&l_CallReturn);''',
        '''      if (Op->Hint == IR::BranchHint::ThunkGenerationCheck) {\n        LOGMAN_THROW_A_FMT(Op->ThunkGeneration, "ThunkGenerationCheck requires a nonzero generation");\n        EmitLinkedThunkGenerationBranch(NewRIP, Entry, Op->ThunkGeneration);\n      } else {\n        EmitLinkedBranch(NewRIP, Op->Hint == IR::BranchHint::Call);\n      }\n      (void)Bind(&l_CallReturn);''',
        'lower generation-aware exit',
    )

    jit = root / "FEXCore/Source/Interface/Core/JIT/JIT.cpp"
    repl(
        jit,
        '''    dc64(PendingJumpThunk.CallerAddress - ThunkAddress);                       // CallerOffset\n  }''',
        '''    dc64(PendingJumpThunk.CallerAddress - ThunkAddress);                       // CallerOffset\n    dc64(PendingJumpThunk.ThunkHost);                                                     // ThunkHost\n    dc64(PendingJumpThunk.ThunkGeneration);                                               // ThunkGeneration\n  }''',
        'emit generation record',
    )
    repl(
        jit,
        '''  if (std::getenv("FEX_DIAG_INFLIGHT_SELECT")) {\n    DiagnosticPauseBeforeTargetSelection(GuestRip);\n  }\n\n  if (TFSet) {''',
        '''  if (std::getenv("FEX_DIAG_INFLIGHT_SELECT")) {\n    DiagnosticPauseBeforeTargetSelection(GuestRip);\n  }\n\n  if (Record->ThunkGeneration) {\n    auto* CTX = static_cast<Context::ContextImpl*>(Thread->CTX);\n    const uint64_t CurrentGeneration = CTX->GetThunkTrampolineGeneration(Record->ThunkHost);\n    if (CurrentGeneration != Record->ThunkGeneration) {\n      fprintf(stderr,\n              "DIAG_HGEN_REJECT H=%#lx T=%#lx expected=%#lx current=%#lx\\n",\n              Record->ThunkHost, GuestRip, Record->ThunkGeneration, CurrentGeneration);\n      GuestRip = Record->ThunkHost;\n    } else {\n      fprintf(stderr,\n              "DIAG_HGEN_ACCEPT H=%#lx T=%#lx generation=%#lx\\n",\n              Record->ThunkHost, GuestRip, CurrentGeneration);\n    }\n  }\n\n  if (TFSet) {''',
        'validate H generation after deterministic barrier',
    )

    print('Added per-H generation snapshot to synthetic exit-link records')


if __name__ == '__main__':
    main()
