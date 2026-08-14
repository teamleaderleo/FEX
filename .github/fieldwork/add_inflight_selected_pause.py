#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
    path = root / 'FEXCore/Source/Interface/Core/JIT/JIT.cpp'
    s = path.read_text()

    inc = '#include <cstdio>\n#include <cstring>\n#include <unistd.h>\n'
    repl_inc = '#include <atomic>\n#include <cstdio>\n#include <cstdlib>\n#include <cstring>\n#include <unistd.h>\n'
    if inc not in s:
        raise SystemExit('JIT include anchor missing')
    s = s.replace(inc, repl_inc, 1)

    function_anchor = '''uint64_t Arm64JITCore::ExitFunctionLink(FEXCore::Core::CpuStateFrame* Frame, FEXCore::Context::ExitFunctionLinkData* Record) {\n'''
    helper = r'''static constexpr uintptr_t DiagnosticInflightSyntheticH = 0x0000700000020000ULL;
static constexpr const char* DiagnosticInflightArm = "/tmp/fex-thunk-inflight-arm";
static constexpr const char* DiagnosticInflightSelected = "/tmp/fex-thunk-inflight-selected";
static constexpr const char* DiagnosticInflightResume = "/tmp/fex-thunk-inflight-resume";

static void DiagnosticPauseSelectedThunk(uintptr_t GuestRip, uintptr_t HostCode) {
  if (GuestRip != DiagnosticInflightSyntheticH || ::access(DiagnosticInflightArm, F_OK) != 0) {
    return;
  }

  static std::atomic_flag Claimed = ATOMIC_FLAG_INIT;
  if (Claimed.test_and_set(std::memory_order_acq_rel)) {
    return;
  }

  FILE* Selected = std::fopen(DiagnosticInflightSelected, "w");
  if (!Selected) {
    std::fprintf(stderr, "DIAG_INFLIGHT_MARKER_FAIL H=%#lx\n", GuestRip);
    std::_Exit(123);
  }
  std::fprintf(Selected, "%lx %lx\n", GuestRip, HostCode);
  std::fclose(Selected);

  std::fprintf(stderr, "DIAG_INFLIGHT_SELECTED H=%#lx block=%#lx\n", GuestRip, HostCode);
  std::fflush(stderr);
  while (::access(DiagnosticInflightResume, F_OK) != 0) {
    ::usleep(1000);
  }
  std::fprintf(stderr, "DIAG_INFLIGHT_RESUME H=%#lx block=%#lx\n", GuestRip, HostCode);
  std::fflush(stderr);
}

'''
    if function_anchor not in s:
        raise SystemExit('ExitFunctionLink anchor missing')
    s = s.replace(function_anchor, helper + function_anchor, 1)

    lock_anchor = '''  // Guard the LookupCache lock with the code invalidation mutex, to avoid issues with forking\n  auto lk_inval = GuardSignalDeferringSection<std::shared_lock>(static_cast<Context::ContextImpl*>(Thread->CTX)->CodeInvalidationMutex, Thread);\n\n  // Lock here is necessary to prevent simultaneous linking and delinking\n'''
    lock_repl = '''  {\n    // Keep selection identical to the product path. The diagnostic pause starts\n    // after lookup/code-invalidation guards are released so another guest thread\n    // can retire H while this thread retains the selected HostCode pointer.\n    auto lk_inval = GuardSignalDeferringSection<std::shared_lock>(static_cast<Context::ContextImpl*>(Thread->CTX)->CodeInvalidationMutex, Thread);\n\n    // Lock here is necessary to prevent simultaneous linking and delinking\n'''
    if lock_anchor not in s:
        raise SystemExit('ExitFunctionLink invalidation-lock anchor missing')
    s = s.replace(lock_anchor, lock_repl, 1)

    tail_anchor = '''    Thread->LookupCache->AddBlockLink(GuestRip, Record, IndirectBlockDelinker, lk);\n  }\n\n  return HostCode;\n}\n'''
    tail_repl = '''    Thread->LookupCache->AddBlockLink(GuestRip, Record, IndirectBlockDelinker, lk);\n    }\n  } // release lookup and code-invalidation guards before forcing the race\n\n  if (std::getenv("FEX_DIAG_INFLIGHT_SELECT")) {\n    DiagnosticPauseSelectedThunk(GuestRip, HostCode);\n  }\n  return HostCode;\n}\n'''
    if tail_anchor not in s:
        raise SystemExit('ExitFunctionLink tail anchor missing')
    s = s.replace(tail_anchor, tail_repl, 1)

    path.write_text(s)
    print(path)


if __name__ == '__main__':
    main()
