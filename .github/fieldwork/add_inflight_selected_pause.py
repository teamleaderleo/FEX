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
static constexpr const char* DiagnosticInflightTarget = "/tmp/fex-thunk-inflight-target";
static constexpr const char* DiagnosticInflightSelected = "/tmp/fex-thunk-inflight-selected";
static constexpr const char* DiagnosticInflightResume = "/tmp/fex-thunk-inflight-resume";

static void DiagnosticPauseBeforeTargetSelection(uintptr_t GuestRip) {
  if (::access(DiagnosticInflightArm, F_OK) != 0) return;
  FILE* TargetFile = std::fopen(DiagnosticInflightTarget, "r");
  if (!TargetFile) return;
  unsigned long long Target {};
  const int Parsed = std::fscanf(TargetFile, "%llx", &Target);
  std::fclose(TargetFile);
  if (Parsed != 1 || GuestRip != static_cast<uintptr_t>(Target)) return;

  static std::atomic_flag Claimed = ATOMIC_FLAG_INIT;
  if (Claimed.test_and_set(std::memory_order_acq_rel)) return;

  FILE* Selected = std::fopen(DiagnosticInflightSelected, "w");
  if (!Selected) {
    std::fprintf(stderr, "DIAG_INFLIGHT_MARKER_FAIL H=%#lx T=%#lx\n", DiagnosticInflightSyntheticH, GuestRip);
    std::_Exit(123);
  }
  std::fprintf(Selected, "%lx\n", GuestRip);
  std::fclose(Selected);
  std::fprintf(stderr, "DIAG_INFLIGHT_SELECTED H=%#lx T=%#lx stage=before-target-selection\n",
               DiagnosticInflightSyntheticH, GuestRip);
  std::fflush(stderr);
  while (::access(DiagnosticInflightResume, F_OK) != 0) ::usleep(1000);
  std::fprintf(stderr, "DIAG_INFLIGHT_RESUME H=%#lx T=%#lx stage=before-target-selection\n",
               DiagnosticInflightSyntheticH, GuestRip);
  std::fflush(stderr);
}

'''
    if function_anchor not in s: raise SystemExit('ExitFunctionLink anchor missing')
    s = s.replace(function_anchor, helper + function_anchor, 1)

    entry_anchor = '''  uintptr_t HostCode {};\n  auto GuestRip = Record->GuestRIP;\n\n  if (TFSet) {\n'''
    entry_repl = '''  uintptr_t HostCode {};\n  auto GuestRip = Record->GuestRIP;\n\n  if (std::getenv("FEX_DIAG_INFLIGHT_SELECT")) {\n    DiagnosticPauseBeforeTargetSelection(GuestRip);\n  }\n\n  if (TFSet) {\n'''
    if entry_anchor not in s: raise SystemExit('ExitFunctionLink entry anchor missing')
    s = s.replace(entry_anchor, entry_repl, 1)

    path.write_text(s)
    print(path)


if __name__ == '__main__':
    main()
