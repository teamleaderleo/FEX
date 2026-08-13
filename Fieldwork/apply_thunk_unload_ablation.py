#!/usr/bin/env python3
"""Apply a diagnostic-only 2x2 thunk-unload ablation to FEX-2608.

Use after (or independently of) apply_thunk_lifetime_trace.py in the owned fork.

Runtime control:
  FEX_FIELDWORK_THUNK_UNLOAD_MODE=observe
  FEX_FIELDWORK_THUNK_UNLOAD_MODE=erase
  FEX_FIELDWORK_THUNK_UNLOAD_MODE=invalidate
  FEX_FIELDWORK_THUNK_UNLOAD_MODE=erase-invalidate

For each guest munmap range, this finds thunk-created CustomIR entries whose
recorded guest target lies inside the range. The four modes independently vary:
  - whether the H->T CustomIR registry entry is erased
  - whether translated/lookup state at H is invalidated

This is investigation code only, not an upstream contribution candidate.
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
    """  FEX_DEFAULT_VISIBILITY virtual void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) = 0;\n\n  // FIELDWORK diagnostic only: vary thunk CustomIR registration and compiled-H lifetime independently\n  // when a guest range containing recorded thunk targets is unmapped.\n  FEX_DEFAULT_VISIBILITY virtual void FieldworkHandleThunkTargetUnmap(\n    FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) = 0;\n\n  /**\n   * @brief Adds additional per-instruction granularity TSO enable/disable information for the given range.\n""",
)

replace_once(
    impl_context,
    """  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;\n\n  void AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) override;\n""",
    """  void AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) override;\n  void FieldworkHandleThunkTargetUnmap(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) override;\n\n  void AddForceTSOInformation(const IntervalList<uint64_t>& ValidRanges, fextl::set<uint64_t>&& Instructions) override;\n""",
)

replace_once(
    core,
    """#include <condition_variable>\n#include <fcntl.h>\n#include <functional>\n""",
    """#include <condition_variable>\n#include <cstdlib>\n#include <fcntl.h>\n#include <functional>\n""",
)

anchor = """void ContextImpl::AddThunkTrampolineIRHandler(uintptr_t Entrypoint, uintptr_t GuestThunkEntrypoint) {\n"""
text = core.read_text()
start = text.find(anchor)
if start == -1:
    raise SystemExit("AddThunkTrampolineIRHandler anchor missing")
next_fn = text.find("\nvoid ContextImpl::", start + len(anchor))
if next_fn == -1:
    raise SystemExit("next ContextImpl function anchor missing")
method = r'''

void ContextImpl::FieldworkHandleThunkTargetUnmap(FEXCore::Core::InternalThreadState* Thread, uint64_t Start, uint64_t Length) {
  const char* ModeEnv = std::getenv("FEX_FIELDWORK_THUNK_UNLOAD_MODE");
  if (!ModeEnv || !ModeEnv[0] || Length == 0) {
    return;
  }

  const std::string_view Mode {ModeEnv};
  const bool EraseRegistration = Mode == "erase" || Mode == "erase-invalidate";
  const bool InvalidateH = Mode == "invalidate" || Mode == "erase-invalidate";
  if (Mode != "observe" && !EraseRegistration && !InvalidateH) {
    LogMan::Msg::EFmt("FEXLIFE ABLATION_UNKNOWN_MODE mode={} treating_as=observe", Mode);
  }

  fextl::vector<uintptr_t> HostEntries;
  {
    std::unique_lock lk(CustomIRMutex);
    auto It = CustomIRHandlers.begin();
    while (It != CustomIRHandlers.end()) {
      const uintptr_t H = It->first;
      const auto& Entry = It->second;
      const uintptr_t T = reinterpret_cast<uintptr_t>(Entry.Data);
      const bool IsThunkEntry = Entry.Creator == ThunkHandler;
      const bool TargetInRange = T >= Start && (T - Start) < Length;

      if (!IsThunkEntry || !TargetInRange) {
        ++It;
        continue;
      }

      LogMan::Msg::EFmt(
        "FEXLIFE ABLATION_MATCH mode={} unmap={:#x}+{:#x} H={:#x} T={:#x} erase={} invalidate_h={}",
        Mode, Start, Length, H, T, EraseRegistration, InvalidateH);
      HostEntries.emplace_back(H);

      if (EraseRegistration) {
        It = CustomIRHandlers.erase(It);
      } else {
        ++It;
      }
    }
    HasCustomIRHandlers = !CustomIRHandlers.empty();
  }

  if (InvalidateH) {
    for (const auto H : HostEntries) {
      LogMan::Msg::EFmt("FEXLIFE ABLATION_INVALIDATE_H H={:#x}", H);
      SyscallHandler->InvalidateGuestCodeRange(Thread, H, 1);
    }
  }

  LogMan::Msg::EFmt("FEXLIFE ABLATION_DONE mode={} matches={}", Mode, HostEntries.size());
}
'''
if "void ContextImpl::FieldworkHandleThunkTargetUnmap" not in text:
    text = text[:next_fn] + method + text[next_fn:]
    core.write_text(text)
    print(f"patched: {core} (added ablation method)")
else:
    print(f"already patched: {core} (ablation method)")

replace_once(
    smc,
    """  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);\n\n  if (length) {\n""",
    """  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);\n\n  // FIELDWORK diagnostic only. Normal T-range invalidation above has already run.\n  // Now independently vary the H->T registration and translated H block so the\n  // immediate stale layer can be identified.\n  CTX->FieldworkHandleThunkTargetUnmap(Thread, reinterpret_cast<uint64_t>(addr), Size);\n\n  if (length) {\n""",
)

print("FEX FIELDWORK thunk unload ablation applied")
