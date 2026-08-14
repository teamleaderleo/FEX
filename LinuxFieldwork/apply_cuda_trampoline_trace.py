#!/usr/bin/env python3
from pathlib import Path

p = Path('Source/Tools/LinuxEmulation/Thunks.cpp')
s = p.read_text()
old = '''  LogMan::Msg::DFmt("Thunks: Adding host trampoline for guest function {:#x} via unpacker {:#x}", GuestTarget, GuestUnpacker);\n\n  const auto HostToGuestTrampolineSize = __stop_HostToGuestTrampolineTemplate - __start_HostToGuestTrampolineTemplate;\n'''
new = '''  LogMan::Msg::DFmt("Thunks: Adding host trampoline for guest function {:#x} via unpacker {:#x}", GuestTarget, GuestUnpacker);\n  fprintf(stderr, "FEX_TRAMP_CREATE unpacker=%#lx target=%#lx\\n",\n          static_cast<unsigned long>(GuestUnpacker), static_cast<unsigned long>(GuestTarget));\n  fflush(stderr);\n\n  const auto HostToGuestTrampolineSize = __stop_HostToGuestTrampolineTemplate - __start_HostToGuestTrampolineTemplate;\n'''
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new, 1)
old = '''  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {\n    .HostPacker = HostPacker, .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallback, .GuestUnpacker = GuestUnpacker, .GuestTarget = GuestTarget};\n\n  ThunkHandler->GuestcallToHostTrampoline[gci] = HostTrampoline;\n'''
new = '''  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {\n    .HostPacker = HostPacker, .CallCallback = (uintptr_t)&ThunkHandler_impl::CallCallback, .GuestUnpacker = GuestUnpacker, .GuestTarget = GuestTarget};\n  fprintf(stderr, "FEX_TRAMP_CREATED trampoline=%p unpacker=%#lx target=%#lx\\n",\n          static_cast<void*>(HostTrampoline), static_cast<unsigned long>(GuestUnpacker), static_cast<unsigned long>(GuestTarget));\n  fflush(stderr);\n\n  ThunkHandler->GuestcallToHostTrampoline[gci] = HostTrampoline;\n'''
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new, 1)
p.write_text(s)
print('CUDA trampoline trace instrumentation applied')
