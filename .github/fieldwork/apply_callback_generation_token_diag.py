#!/usr/bin/env python3
from pathlib import Path

thunks = Path("Source/Tools/LinuxEmulation/Thunks.cpp")
s = thunks.read_text()

replacements = [
    (
        '#include <cstdint>\n#include <dlfcn.h>\n',
        '#include <atomic>\n#include <cstdint>\n#include <dlfcn.h>\n',
    ),
    (
        '      ".quad 0, 0, 0, 0 \\n" // TrampolineInstanceInfo\n',
        '      ".quad 0, 0, 0, 0, 0 \\n" // TrampolineInstanceInfo\n',
    ),
    (
        '    ".quad 0, 0, 0, 0 \\n" // TrampolineInstanceInfo\n',
        '    ".quad 0, 0, 0, 0, 0 \\n" // TrampolineInstanceInfo\n',
    ),
    (
        'static thread_local FEX::HLE::ThreadStateObject* ThreadObject {};\n',
        'static thread_local FEX::HLE::ThreadStateObject* ThreadObject {};\n'
        'static std::atomic<uint64_t> NextCallbackBridgeToken {1};\n',
    ),
    (
        '  uintptr_t GuestUnpacker;\n  uintptr_t GuestTarget;\n};\n',
        '  uintptr_t GuestUnpacker;\n  uintptr_t GuestTarget;\n  uintptr_t BridgeToken;\n};\n',
    ),
    (
        '  static void CallCallback(void* callback, void* arg0, void* arg1) {\n',
        '  static void CallCallback(void* callback, void* arg0, void* arg1, uintptr_t BridgeToken) {\n',
    ),
    (
        '    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);\n'
        '    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());\n',
        '    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);\n'
        '    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());\n'
        '    LogMan::Msg::DFmt("DIAG_CALLBACK_TOKEN token={} unpacker={} target={}", BridgeToken, fmt::ptr(callback), fmt::ptr(arg0));\n',
    ),
    (
        '  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {\n'
        '    .HostPacker = HostPacker, .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallback, .GuestUnpacker = GuestUnpacker, .GuestTarget = GuestTarget};\n',
        '  const auto BridgeToken = NextCallbackBridgeToken.fetch_add(1, std::memory_order_relaxed);\n'
        '  LOGMAN_THROW_A_FMT(BridgeToken != 0, "Callback bridge token wrapped to zero");\n'
        '  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {\n'
        '    .HostPacker = HostPacker,\n'
        '    .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallback,\n'
        '    .GuestUnpacker = GuestUnpacker,\n'
        '    .GuestTarget = GuestTarget,\n'
        '    .BridgeToken = BridgeToken};\n',
    ),
]

for old, new in replacements:
    count = s.count(old)
    if count != 1:
        raise SystemExit(f"Thunks.cpp anchor count {count}, expected 1:\n{old}")
    s = s.replace(old, new, 1)

thunks.write_text(s)

host = Path("ThunkLibs/include/common/Host.h")
s = host.read_text()
replacements = [
    (
        '  void (*CallCallback)(uintptr_t GuestUnpacker, uintptr_t GuestTarget, void* argsrv);\n'
        '  uintptr_t GuestUnpacker;\n'
        '  uintptr_t GuestTarget;\n'
        '};\n',
        '  void (*CallCallback)(uintptr_t GuestUnpacker, uintptr_t GuestTarget, void* argsrv, uintptr_t BridgeToken);\n'
        '  uintptr_t GuestUnpacker;\n'
        '  uintptr_t GuestTarget;\n'
        '  uintptr_t BridgeToken;\n'
        '};\n',
    ),
    (
        '    guestcall->CallCallback(guestcall->GuestUnpacker, guestcall->GuestTarget, &packed_args);\n',
        '    guestcall->CallCallback(guestcall->GuestUnpacker, guestcall->GuestTarget, &packed_args, guestcall->BridgeToken);\n',
    ),
]
for old, new in replacements:
    count = s.count(old)
    if count != 1:
        raise SystemExit(f"Host.h anchor count {count}, expected 1:\n{old}")
    s = s.replace(old, new, 1)

host.write_text(s)
