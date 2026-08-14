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

    ctxh = root / "FEXCore/Source/Interface/Context/Context.h"
    repl(
        ctxh,
        '''  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint, uint64_t OwnerID = 0) override;''',
        '''  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint, uint64_t OwnerID = 0) override;
  uint64_t GetThunkTrampolineOwnerID(uintptr_t Entrypoint);''',
        'Context owner-token query declaration',
    )
    repl(
        ctxh,
        '''  fextl::unordered_map<uint64_t, CustomIRHandlerEntry> CustomIRHandlers;
  IntervalList<uint64_t> ForceTSOValidRanges;''',
        '''  fextl::unordered_map<uint64_t, CustomIRHandlerEntry> CustomIRHandlers;
  fextl::unordered_map<uint64_t, uint64_t> ThunkOwnerIDs;
  IntervalList<uint64_t> ForceTSOValidRanges;''',
        'Context owner-token storage',
    )

    core = root / "FEXCore/Source/Interface/Core/Core.cpp"
    repl(
        core,
        '''  LogMan::Msg::DFmt("Thunks: Adding guest trampoline from address {:#x} to guest function {:#x}", Entrypoint, GuestThunkEntrypoint);

  auto Result = AddCustomIREntrypoint(''',
        '''  LogMan::Msg::DFmt("Thunks: Adding guest trampoline from address {:#x} to guest function {:#x}", Entrypoint, GuestThunkEntrypoint);

  {
    std::unique_lock lk(CustomIRMutex);
    ThunkOwnerIDs[Entrypoint] = OwnerID;
  }

  auto Result = AddCustomIREntrypoint(''',
        'store owner token before custom IR install',
    )
    repl(
        core,
        '''      const auto Hint = OwnerID ? IR::BranchHint::ThunkOwnerCheck : IR::BranchHint::None;
      const auto OwnerToken = OwnerID ? emit->Constant(OwnerID) : emit->Invalid();
      emit->_ExitFunction(IR::OpSize::i64Bit, emit->Constant(GuestThunkEntrypoint), Hint, OwnerToken, emit->Invalid());''',
        '''      const auto Hint = OwnerID ? IR::BranchHint::ThunkOwnerCheck : IR::BranchHint::None;
      emit->_ExitFunction(IR::OpSize::i64Bit, emit->Constant(GuestThunkEntrypoint), Hint, emit->Invalid(), emit->Invalid());''',
        'remove post-RA owner token operand',
    )

    anchor = '''void ContextImpl::AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) {'''
    method = '''uint64_t ContextImpl::GetThunkTrampolineOwnerID(uintptr_t Entrypoint) {
  std::shared_lock lk(CustomIRMutex);
  auto It = ThunkOwnerIDs.find(Entrypoint);
  return It == ThunkOwnerIDs.end() ? 0 : It->second;
}

'''
    repl(core, anchor, method + anchor, 'Context owner-token query implementation')

    repl(
        core,
        '''  const auto Erased = CustomIRHandlers.erase(Entrypoint);
  HasCustomIRHandlers = !CustomIRHandlers.empty();''',
        '''  const auto Erased = CustomIRHandlers.erase(Entrypoint);
  ThunkOwnerIDs.erase(Entrypoint);
  HasCustomIRHandlers = !CustomIRHandlers.empty();''',
        'erase owner token with H definition',
    )

    branch = root / "FEXCore/Source/Interface/Core/JIT/BranchOps.cpp"
    repl(
        branch,
        '''      if (Op->Hint == IR::BranchHint::ThunkOwnerCheck) {
        uint64_t OwnerID {};
        LOGMAN_THROW_A_FMT(IsInlineConstant(Op->CallReturnAddress, &OwnerID) && OwnerID,
                           "ThunkOwnerCheck requires an inline owner token");
        EmitLinkedThunkOwnerBranch(NewRIP, Entry, OwnerID);
      } else {''',
        '''      if (Op->Hint == IR::BranchHint::ThunkOwnerCheck) {
        const uint64_t OwnerID = CTX->GetThunkTrampolineOwnerID(Entry);
        LOGMAN_THROW_A_FMT(OwnerID, "ThunkOwnerCheck missing owner token for H {:#x}", Entry);
        EmitLinkedThunkOwnerBranch(NewRIP, Entry, OwnerID);
      } else {''',
        'read H owner token from Context metadata',
    )

    dump = root / "FEXCore/Source/Interface/IR/IRDumper.cpp"
    repl(
        dump,
        '''    case BranchHint::CheckTF: return "CheckTF";
    }
    return "<Unknown Branch Hint>";''',
        '''    case BranchHint::CheckTF: return "CheckTF";
    case BranchHint::ThunkOwnerCheck: return "ThunkOwnerCheck";
    }
    return "<Unknown Branch Hint>";''',
        'IR dumper owner hint',
    )

    print('Moved synthetic-H owner token transport out of post-RA GPR operands')


if __name__ == '__main__':
    main()
