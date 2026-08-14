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
        'static thread_local FEX::HLE::ThreadStateObject* ThreadObject {};\n',
        'static thread_local FEX::HLE::ThreadStateObject* ThreadObject {};\n'
        'static std::atomic<uint64_t> NextCallbackBridgeToken {1};\n',
    ),
    (
        'struct TrampolineInstanceInfo {\n'
        '  void* HostPacker;\n'
        '  uintptr_t CallCallback;\n'
        '  uintptr_t GuestUnpacker;\n'
        '  uintptr_t GuestTarget;\n'
        '};\n',
        'struct CallbackBridgeDescriptor {\n'
        '  uintptr_t GuestUnpacker;\n'
        '  uintptr_t GuestTarget;\n'
        '  uint64_t BridgeToken;\n'
        '};\n\n'
        'struct TrampolineInstanceInfo {\n'
        '  void* HostPacker;\n'
        '  uintptr_t CallCallback;\n'
        '  CallbackBridgeDescriptor* Bridge;\n'
        '  uintptr_t Reserved;\n'
        '};\n'
        'static_assert(sizeof(TrampolineInstanceInfo) == 4 * sizeof(uintptr_t));\n',
    ),
    (
        '  static void CallCallback(void* callback, void* arg0, void* arg1) {\n',
        '  static void CallCallback(void* callback, void* arg0, void* arg1, uintptr_t BridgePtr) {\n',
    ),
    (
        '    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);\n'
        '    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());\n',
        '    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);\n'
        '    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());\n'
        '    auto* Bridge = reinterpret_cast<CallbackBridgeDescriptor*>(BridgePtr);\n'
        '    LOGMAN_THROW_A_FMT(Bridge != nullptr, "Callback bridge descriptor missing");\n'
        '    LogMan::Msg::DFmt("DIAG_CALLBACK_TOKEN token={} unpacker={} target={}", Bridge->BridgeToken, fmt::ptr(callback), fmt::ptr(arg0));\n',
    ),
    (
        '  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {\n'
        '    .HostPacker = HostPacker, .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallback, .GuestUnpacker = GuestUnpacker, .GuestTarget = GuestTarget};\n',
        '  const auto BridgeToken = NextCallbackBridgeToken.fetch_add(1, std::memory_order_relaxed);\n'
        '  LOGMAN_THROW_A_FMT(BridgeToken != 0, "Callback bridge token wrapped to zero");\n'
        '  auto* Bridge = new CallbackBridgeDescriptor {\n'
        '    .GuestUnpacker = GuestUnpacker,\n'
        '    .GuestTarget = GuestTarget,\n'
        '    .BridgeToken = BridgeToken};\n'
        '  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {\n'
        '    .HostPacker = HostPacker,\n'
        '    .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallback,\n'
        '    .Bridge = Bridge,\n'
        '    .Reserved = 0};\n',
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
        'struct GuestcallInfo {\n'
        '  uintptr_t HostPacker;\n'
        '  void (*CallCallback)(uintptr_t GuestUnpacker, uintptr_t GuestTarget, void* argsrv);\n'
        '  uintptr_t GuestUnpacker;\n'
        '  uintptr_t GuestTarget;\n'
        '};\n',
        'struct CallbackBridgeDescriptor {\n'
        '  uintptr_t GuestUnpacker;\n'
        '  uintptr_t GuestTarget;\n'
        '  uint64_t BridgeToken;\n'
        '};\n\n'
        'struct GuestcallInfo {\n'
        '  uintptr_t HostPacker;\n'
        '  void (*CallCallback)(uintptr_t GuestUnpacker, uintptr_t GuestTarget, void* argsrv, uintptr_t BridgePtr);\n'
        '  CallbackBridgeDescriptor* Bridge;\n'
        '  uintptr_t Reserved;\n'
        '};\n'
        'static_assert(sizeof(GuestcallInfo) == 4 * sizeof(uintptr_t));\n',
    ),
    (
        '    guestcall->CallCallback(guestcall->GuestUnpacker, guestcall->GuestTarget, &packed_args);\n',
        '    auto* bridge = guestcall->Bridge;\n'
        '    guestcall->CallCallback(bridge->GuestUnpacker, bridge->GuestTarget, &packed_args, reinterpret_cast<uintptr_t>(bridge));\n',
    ),
]
for old, new in replacements:
    count = s.count(old)
    if count != 1:
        raise SystemExit(f"Host.h anchor count {count}, expected 1:\n{old}")
    s = s.replace(old, new, 1)

host.write_text(s)
