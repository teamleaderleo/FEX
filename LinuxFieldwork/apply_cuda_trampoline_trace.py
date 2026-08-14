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

probe = Path('LinuxFieldwork/cuda_hostnode_moved_reload_probe.c')
q = probe.read_text()
old = '''  fprintf(stderr, "MARK add1-return rc=%d node=%p callbacks=%u\\n", rc, node, callback_count); fflush(stderr);\n  if (rc != 0 || callback_count != 0) return 20;\n\n  uintptr_t old_add = (uintptr_t)add1;\n'''
new = '''  fprintf(stderr, "MARK add1-return rc=%d node=%p callbacks=%u\\n", rc, node, callback_count); fflush(stderr);\n  if (rc != 0 || callback_count != 0) return 20;\n\n  /* Trace-only control: prove this exact generated callback works before unload. */\n  fprintf(stderr, "MARK launch1-enter pre-close-control\\n"); fflush(stderr);\n  rc = launch1((CUgraphExec)(uintptr_t)0x2222, (CUstream)(uintptr_t)0x3333);\n  fprintf(stderr, "MARK launch1-return rc=%d callbacks=%u\\n", rc, callback_count); fflush(stderr);\n  if (rc != 0 || callback_count != 1) return 21;\n\n  uintptr_t old_add = (uintptr_t)add1;\n'''
assert q.count(old) == 1, q.count(old)
q = q.replace(old, new, 1)
old = '''  fprintf(stderr, "MARK launch2-return rc=%d callbacks=%u\\n", rc, callback_count); fflush(stderr);\n  if (rc != 0 || callback_count != 1) return 22;\n'''
new = '''  fprintf(stderr, "MARK launch2-return rc=%d callbacks=%u\\n", rc, callback_count); fflush(stderr);\n  if (rc != 0 || callback_count != 2) return 22;\n'''
assert q.count(old) == 1, q.count(old)
probe.write_text(q.replace(old, new, 1))

print('CUDA trampoline trace instrumentation and pre-close callback control applied')
