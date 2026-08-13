#!/usr/bin/env python3
"""Apply a diagnostic CustomIR tombstone experiment to the exact FEX-2608 tree.

This is owned-fork research code. It tests one hypothesis only: a thunk-created
native-PFN CustomIR entry outlives the guest thunk target that it captured.

The transform keeps the CustomIR key registered after target-range retirement,
replaces its handler with an explicit tombstone, and invalidates translated code
at the native PFN key. A later stale call therefore cannot silently fall through
to ordinary x86 decoding of the native host address.

Apply after Fieldwork/apply_thunk_lifetime_trace.py if detailed FEXLIFE logging is
desired. Do not combine with apply_thunk_unload_ablation.py in the same checkout.
"""
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if new in text:
        print(f"already patched: {path}")
        return
    if old not in text:
        raise SystemExit(f"expected patch anchor missing in {path}: {old[:140]!r}")
    path.write_text(text.replace(old, new, 1))
    print(f"patched: {path}")


public_context = Path("FEXCore/include/FEXCore/Core/Context.h")
impl_context = Path("FEXCore/Source/Interface/Context/Context.h")
core = Path("FEXCore/Source/Interface/Core/Core.cpp")
smc = Path("Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp")

replace_once(
    public_context,
    """  FEX_DEFAULT_VISIBILITY virtual void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) = 0;\n\n  /**\n   * @brief Adds additional per-instruction granularity TSO enable/disable information for the given range.\n""",
    """  FEX_DEFAULT_VISIBILITY virtual void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) = 0;\n\n  // FIELDWORK diagnostic only: retire thunk-created CustomIR bridges when their\n  // recorded guest target is removed from the guest address space.\n  FEX_DEFAULT_VISIBILITY virtual void FieldworkRetireThunkTargetsInRange(\n    FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) = 0;\n\n  /**\n   * @brief Adds additional per-instruction granularity TSO enable/disable information for the given range.\n""",
)

replace_once(
    impl_context,
    """  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;\n\n  void AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) override;\n""",
    """  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;\n  void FieldworkRetireThunkTargetsInRange(\n    FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) override;\n\n  void AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) override;\n""",
)

anchor = "void ContextImpl::AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) {\n"
text = core.read_text()
start = text.find(anchor)
if start == -1:
    raise SystemExit("AddThunkTrampolineIRHandler anchor missing")
next_fn = text.find("\nvoid ContextImpl::", start + len(anchor))
if next_fn == -1:
    raise SystemExit("next ContextImpl function anchor missing")

method = r'''

void ContextImpl::FieldworkRetireThunkTargetsInRange(
  FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {
  if (Length == 0) {
    return;
  }

  fextl::vector<uintptr_t> HostEntries;
  {
    std::unique_lock lk(CustomIRMutex);
    for (auto& [Entrypoint, Entry] : CustomIRHandlers) {
      if (Entry.Creator != ThunkHandler || Entry.Data == nullptr) {
        continue;
      }

      const auto GuestTarget = reinterpret_cast<uintptr_t>(Entry.Data);
      if (!(GuestTarget >= Start && (GuestTarget - Start) < Length)) {
        continue;
      }

      LogMan::Msg::EFmt(
        "FEXLIFE TOMBSTONE_RETIRE unmap={:#x}+{:#x} H={:#x} old_T={:#x}",
        Start, Length, Entrypoint, GuestTarget);

      Entry.Handler = [this](uintptr_t TombstonedEntrypoint, FEXCore::IR::IREmitter* emit) {
        LogMan::Msg::EFmt("FEXLIFE TOMBSTONE_HIT H={:#x}", TombstonedEntrypoint);

        auto IRHeader = emit->_IRHeader(emit->Invalid(), TombstonedEntrypoint, 0, 0, 0, 0);
        auto Block = emit->CreateCodeNode(true, 0);
        IRHeader.first->Blocks = emit->WrapNode(Block);
        emit->SetCurrentCodeBlock(Block);

        const auto GPRSize = this->Config.Is64BitMode ? IR::OpSize::i64Bit : IR::OpSize::i32Bit;
        if (GPRSize == IR::OpSize::i64Bit) {
          IR::Ref R = emit->_StoreRegister(emit->Constant(TombstonedEntrypoint), GPRSize);
          R->Reg = IR::PhysicalRegister(IR::RegClass::GPRFixed, X86State::REG_R11).Raw;
        } else {
          emit->_StoreContextFPR(
            GPRSize,
            emit->_VCastFromGPR(IR::OpSize::i64Bit, IR::OpSize::i64Bit, emit->Constant(TombstonedEntrypoint)),
            offsetof(Core::CPUState, mm[0][0]));
        }

        // Keep the entry classified as CustomIR, but make any post-retirement use
        // fail at an explicit sentinel rather than at the old unmapped thunk target.
        emit->_ExitFunction(IR::OpSize::i64Bit, emit->Constant(0), IR::BranchHint::None, emit->Invalid(), emit->Invalid());
      };
      Entry.Data = nullptr;
      HostEntries.emplace_back(Entrypoint);
    }
  }

  // Never hold CustomIRMutex while taking the code-invalidation path.
  for (const auto Entrypoint : HostEntries) {
    LogMan::Msg::EFmt("FEXLIFE TOMBSTONE_INVALIDATE_H H={:#x}", Entrypoint);
    SyscallHandler->InvalidateGuestCodeRange(Thread, Entrypoint, 1);
  }

  if (!HostEntries.empty()) {
    LogMan::Msg::EFmt("FEXLIFE TOMBSTONE_DONE matches={}", HostEntries.size());
  }
}
'''

if "void ContextImpl::FieldworkRetireThunkTargetsInRange" not in text:
    text = text[:next_fn] + method + text[next_fn:]
    core.write_text(text)
    print(f"patched: {core} (added tombstone retirement method)")
else:
    print(f"already patched: {core} (tombstone retirement method)")

replace_once(
    smc,
    """  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);\n\n  if (length) {\n""",
    """  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);\n\n  // FIELDWORK diagnostic only: ordinary invalidation above retires translated\n  // code keyed by T. Retire bridge objects keyed by native H separately.\n  CTX->FieldworkRetireThunkTargetsInRange(Thread, reinterpret_cast<uint64_t>(addr), Size);\n\n  if (length) {\n""",
)

print("FEX FIELDWORK thunk CustomIR tombstone retirement applied")
