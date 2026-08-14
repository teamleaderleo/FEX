from pathlib import Path

root = Path('src') if Path('src/Source/Tools/LinuxEmulation/Thunks.cpp').exists() else Path('.')

h = root / 'Source/Tools/LinuxEmulation/Thunks.h'
s = h.read_text()
anchor = 'fextl::unique_ptr<ThunkHandler> CreateThunkHandler();\n'
replacement = '''void ReleaseInflightCallbackAfterUnmap(uintptr_t Base, uintptr_t Length);\nfextl::unique_ptr<ThunkHandler> CreateThunkHandler();\n'''
if s.count(anchor) != 1:
    raise SystemExit(f'Thunks.h release declaration anchor count={s.count(anchor)}')
h.write_text(s.replace(anchor, replacement, 1))

p = root / 'Source/Tools/LinuxEmulation/Thunks.cpp'
s = p.read_text()
inc = '#include <cstdint>\n'
rep = '#include <atomic>\n#include <cstdint>\n#include <unistd.h>\n'
if s.count(inc) != 1:
    raise SystemExit(f'include anchor count={s.count(inc)}')
s = s.replace(inc, rep, 1)

ns_anchor = 'namespace FEX::HLE {\n\nstatic thread_local FEX::HLE::ThreadStateObject* ThreadObject {};\n'
ns_repl = '''namespace FEX::HLE {\n\nstatic thread_local FEX::HLE::ThreadStateObject* ThreadObject {};\n\nstatic std::atomic<uint64_t> DiagnosticCallbackEntryCount {};\nstatic std::atomic<bool> DiagnosticInflightSelected {};\nstatic std::atomic<bool> DiagnosticInflightRelease {};\nstatic std::atomic<uintptr_t> DiagnosticInflightUnpacker {};\nstatic std::atomic<uintptr_t> DiagnosticInflightTarget {};\n\nvoid ReleaseInflightCallbackAfterUnmap(uintptr_t Base, uintptr_t Length) {\n  if (!Length || !DiagnosticInflightSelected.load()) {\n    return;\n  }\n\n  const auto Unpacker = DiagnosticInflightUnpacker.load();\n  const auto Target = DiagnosticInflightTarget.load();\n  const bool UnpackerInRange = Unpacker >= Base && (Unpacker - Base) < Length;\n  const bool TargetInRange = Target >= Base && (Target - Base) < Length;\n  if (!UnpackerInRange && !TargetInRange) {\n    return;\n  }\n\n  fprintf(stderr,\n          "DIAG_CALLBACK_POST_UNMAP_RELEASE unpacker=%#lx target=%#lx range=%#lx+%#lx\\n",\n          Unpacker, Target, Base, Length);\n  fflush(stderr);\n  DiagnosticInflightRelease.store(true);\n}\n'''
if s.count(ns_anchor) != 1:
    raise SystemExit(f'namespace diagnostic anchor count={s.count(ns_anchor)}')
s = s.replace(ns_anchor, ns_repl, 1)

anchor = '''    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);\n    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());\n\n'''
block = '''    auto CTX = static_cast<FEXCore::Context::Context*>(ThreadObject->Thread->CTX);\n    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());\n\n    const auto DiagnosticEntry = DiagnosticCallbackEntryCount.fetch_add(1) + 1;\n    if (DiagnosticEntry == 2) {\n      DiagnosticInflightUnpacker.store(reinterpret_cast<uintptr_t>(callback));\n      DiagnosticInflightTarget.store(reinterpret_cast<uintptr_t>(arg0));\n      DiagnosticInflightRelease.store(false);\n      DiagnosticInflightSelected.store(true);\n      fprintf(stderr, "DIAG_CALLBACK_INFLIGHT_SELECTED entry=%lu unpacker=%p target=%p\\n", DiagnosticEntry, callback, arg0);\n      fflush(stderr);\n\n      bool ReleasedByUnmap = false;\n      for (unsigned i = 0; i < 1500; ++i) {\n        if (DiagnosticInflightRelease.load()) {\n          ReleasedByUnmap = true;\n          break;\n        }\n        ::usleep(1000);\n      }\n\n      if (ReleasedByUnmap) {\n        fprintf(stderr, "DIAG_CALLBACK_INFLIGHT_RESUME unpacker=%p target=%p\\n", callback, arg0);\n      } else {\n        fprintf(stderr, "DIAG_CALLBACK_INFLIGHT_PIN_TIMEOUT_RESUME unpacker=%p target=%p\\n", callback, arg0);\n      }\n      fflush(stderr);\n      DiagnosticInflightSelected.store(false);\n    }\n\n'''
if s.count(anchor) != 1:
    raise SystemExit(f'CallCallback anchor count={s.count(anchor)}')
p.write_text(s.replace(anchor, block, 1))

smc = root / 'Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp'
s = smc.read_text()
anchor = '''  }\n  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);\n'''
replacement = '''  }\n\n  // Diagnostic only: the host mapping and VMA tracking update have both\n  // completed at this point. Release an already-entered callback whose raw\n  // guest target/unpacker intersects the range that just disappeared.\n  ReleaseInflightCallbackAfterUnmap(reinterpret_cast<uintptr_t>(addr), Size);\n\n  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);\n'''
if s.count(anchor) != 1:
    raise SystemExit(f'post-unmap release anchor count={s.count(anchor)}')
smc.write_text(s.replace(anchor, replacement, 1))
