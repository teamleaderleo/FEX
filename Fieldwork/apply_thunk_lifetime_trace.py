#!/usr/bin/env python3
"""Apply diagnostic-only lifetime tracing to the exact FEX-2608 tree.

This lives in the owned fork for investigation work. It is intentionally not an
upstream contribution candidate.
"""
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if new in text:
        print(f"already patched: {path}")
        return
    if old not in text:
        raise SystemExit(f"expected patch anchor missing in {path}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1))
    print(f"patched: {path}")


core = Path("FEXCore/Source/Interface/Core/Core.cpp")
smc = Path("Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp")
thunks = Path("Source/Tools/LinuxEmulation/Thunks.cpp")

replace_once(
    core,
    """    if (Handler != CustomIRHandlers.end()) {\n      TotalInstructions = 1;\n      TotalInstructionsLength = 1;\n      Handler->second.Handler(GuestRIP, Thread->OpDispatcher.get());\n      HasCustomIR = true;\n    }\n""",
    """    if (Handler != CustomIRHandlers.end()) {\n      TotalInstructions = 1;\n      TotalInstructionsLength = 1;\n      LogMan::Msg::EFmt(\"FEXLIFE CUSTOMIR_HIT entry={:#x} creator={:#x} target={:#x}\", GuestRIP,\n                        reinterpret_cast<uintptr_t>(Handler->second.Creator), reinterpret_cast<uintptr_t>(Handler->second.Data));\n      Handler->second.Handler(GuestRIP, Thread->OpDispatcher.get());\n      HasCustomIR = true;\n    }\n""",
)

replace_once(
    core,
    """  auto InsertedIterator = CustomIRHandlers.emplace(Entrypoint, CustomIRHandlerEntry {Handler, Creator, Data});\n  HasCustomIRHandlers = true;\n\n  if (!InsertedIterator.second) {\n    const auto& [fn, Creator, Data] = InsertedIterator.first->second;\n    return CustomIRResult(Creator, Data);\n  }\n\n  return std::nullopt;\n""",
    """  LogMan::Msg::EFmt(\"FEXLIFE CUSTOMIR_REGISTER entry={:#x} creator={:#x} target={:#x}\", Entrypoint,\n                    reinterpret_cast<uintptr_t>(Creator), reinterpret_cast<uintptr_t>(Data));\n  auto InsertedIterator = CustomIRHandlers.emplace(Entrypoint, CustomIRHandlerEntry {Handler, Creator, Data});\n  HasCustomIRHandlers = true;\n\n  if (!InsertedIterator.second) {\n    const auto& Existing = InsertedIterator.first->second;\n    LogMan::Msg::EFmt(\"FEXLIFE CUSTOMIR_DUP entry={:#x} old_creator={:#x} old_target={:#x} new_creator={:#x} new_target={:#x}\",\n                      Entrypoint, reinterpret_cast<uintptr_t>(Existing.Creator), reinterpret_cast<uintptr_t>(Existing.Data),\n                      reinterpret_cast<uintptr_t>(Creator), reinterpret_cast<uintptr_t>(Data));\n    return CustomIRResult(Existing.Creator, Existing.Data);\n  }\n\n  return std::nullopt;\n""",
)

replace_once(
    core,
    """  LogMan::Msg::DFmt(\"Thunks: Adding guest trampoline from address {:#x} to guest function {:#x}\", Entrypoint, GuestThunkEntrypoint);\n\n  auto Result = AddCustomIREntrypoint(\n""",
    """  LogMan::Msg::DFmt(\"Thunks: Adding guest trampoline from address {:#x} to guest function {:#x}\", Entrypoint, GuestThunkEntrypoint);\n  LogMan::Msg::EFmt(\"FEXLIFE THUNK_LINK host_entry={:#x} guest_target={:#x}\", Entrypoint, GuestThunkEntrypoint);\n\n  auto Result = AddCustomIREntrypoint(\n""",
)

replace_once(
    core,
    """  std::scoped_lock lk(CustomIRMutex);\n\n  CustomIRHandlers.erase(Entrypoint);\n  HasCustomIRHandlers = !CustomIRHandlers.empty();\n  SyscallHandler->InvalidateGuestCodeRange(Thread, Entrypoint, 1);\n""",
    """  std::scoped_lock lk(CustomIRMutex);\n\n  auto Existing = CustomIRHandlers.find(Entrypoint);\n  if (Existing != CustomIRHandlers.end()) {\n    LogMan::Msg::EFmt(\"FEXLIFE CUSTOMIR_REMOVE entry={:#x} creator={:#x} target={:#x}\", Entrypoint,\n                      reinterpret_cast<uintptr_t>(Existing->second.Creator), reinterpret_cast<uintptr_t>(Existing->second.Data));\n  } else {\n    LogMan::Msg::EFmt(\"FEXLIFE CUSTOMIR_REMOVE_MISS entry={:#x}\", Entrypoint);\n  }\n  CustomIRHandlers.erase(Entrypoint);\n  HasCustomIRHandlers = !CustomIRHandlers.empty();\n  SyscallHandler->InvalidateGuestCodeRange(Thread, Entrypoint, 1);\n""",
)

replace_once(
    smc,
    """  uint64_t Result;\n  uint64_t Size = FEXCore::AlignUp(length, FEXCore::Utils::FEX_PAGE_SIZE);\n  bool PendingResourceDeletion;\n\n  {\n""",
    """  uint64_t Result;\n  uint64_t Size = FEXCore::AlignUp(length, FEXCore::Utils::FEX_PAGE_SIZE);\n  bool PendingResourceDeletion;\n\n  LogMan::Msg::EFmt(\"FEXLIFE MUNMAP_REQUEST base={:#x} requested={:#x} aligned={:#x}\",\n                    reinterpret_cast<uintptr_t>(addr), length, Size);\n\n  {\n""",
)

replace_once(
    smc,
    """    TrackMunmap(Thread, addr, length);\n    PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();\n  }\n  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);\n""",
    """    TrackMunmap(Thread, addr, length);\n    PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();\n  }\n  LogMan::Msg::EFmt(\"FEXLIFE MUNMAP_INVALIDATE base={:#x} size={:#x} pending_resource_deletion={}\",\n                    reinterpret_cast<uintptr_t>(addr), Size, PendingResourceDeletion);\n  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);\n  LogMan::Msg::EFmt(\"FEXLIFE MUNMAP_DONE base={:#x} size={:#x}\", reinterpret_cast<uintptr_t>(addr), Size);\n""",
)

replace_once(
    thunks,
    """  static void CallCallback(void* callback, void* arg0, void* arg1) {\n    if (!ThreadObject) {\n""",
    """  static void CallCallback(void* callback, void* arg0, void* arg1) {\n    LogMan::Msg::EFmt(\"FEXLIFE HOST_TO_GUEST_CALLBACK guest_target={:#x}\", reinterpret_cast<uintptr_t>(callback));\n    if (!ThreadObject) {\n""",
)

replace_once(
    thunks,
    """    auto found = ThunkHandler->GuestcallToHostTrampoline.find(gci);\n    if (found != ThunkHandler->GuestcallToHostTrampoline.end()) {\n      return found->second;\n    }\n  }\n\n  std::lock_guard lk(ThunkHandler->ThunksMutex);\n""",
    """    auto found = ThunkHandler->GuestcallToHostTrampoline.find(gci);\n    if (found != ThunkHandler->GuestcallToHostTrampoline.end()) {\n      LogMan::Msg::EFmt(\"FEXLIFE CALLBACK_TRAMPOLINE_REUSE trampoline={:#x} unpacker={:#x} target={:#x}\",\n                        reinterpret_cast<uintptr_t>(found->second), GuestUnpacker, GuestTarget);\n      return found->second;\n    }\n  }\n\n  std::lock_guard lk(ThunkHandler->ThunksMutex);\n""",
)

replace_once(
    thunks,
    """  ThunkHandler->GuestcallToHostTrampoline[gci] = HostTrampoline;\n  return HostTrampoline;\n""",
    """  ThunkHandler->GuestcallToHostTrampoline[gci] = HostTrampoline;\n  LogMan::Msg::EFmt(\"FEXLIFE CALLBACK_TRAMPOLINE_ADD trampoline={:#x} unpacker={:#x} target={:#x}\",\n                    reinterpret_cast<uintptr_t>(HostTrampoline), GuestUnpacker, GuestTarget);\n  return HostTrampoline;\n""",
)

print("FEXLIFE trace instrumentation applied")
