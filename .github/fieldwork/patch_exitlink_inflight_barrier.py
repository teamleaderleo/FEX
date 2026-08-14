#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    p = root / "FEXCore/Source/Interface/Core/JIT/JIT.cpp"
    s = p.read_text()

    inc = '#include <cstdio>\n#include <cstring>\n#include <unistd.h>\n'
    repl_inc = '#include <atomic>\n#include <cstdio>\n#include <cstdlib>\n#include <cstring>\n#include <limits.h>\n#include <unistd.h>\n'
    if inc not in s:
        raise SystemExit("JIT include anchor missing")
    s = s.replace(inc, repl_inc, 1)

    anchor = '''uint64_t Arm64JITCore::ExitFunctionLink(FEXCore::Core::CpuStateFrame* Frame, FEXCore::Context::ExitFunctionLinkData* Record) {\n'''
    helper = r'''static void FieldworkPauseAfterExitLinkSelection(uintptr_t GuestRip, uintptr_t HostCode) {
  const char* Dir = std::getenv("FEX_FIELDWORK_EXITLINK_BARRIER_DIR");
  if (!Dir || !*Dir) return;

  char TargetPath[PATH_MAX];
  char SelectedPath[PATH_MAX];
  char ResumePath[PATH_MAX];
  if (std::snprintf(TargetPath, sizeof(TargetPath), "%s/target", Dir) >= (int)sizeof(TargetPath) ||
      std::snprintf(SelectedPath, sizeof(SelectedPath), "%s/selected", Dir) >= (int)sizeof(SelectedPath) ||
      std::snprintf(ResumePath, sizeof(ResumePath), "%s/resume", Dir) >= (int)sizeof(ResumePath)) {
    return;
  }

  FILE* TargetFile = std::fopen(TargetPath, "r");
  if (!TargetFile) return;
  unsigned long long Target {};
  const int Parsed = std::fscanf(TargetFile, "%llx", &Target);
  std::fclose(TargetFile);
  if (Parsed != 1 || static_cast<uintptr_t>(Target) != GuestRip) return;

  static std::atomic_flag Claimed = ATOMIC_FLAG_INIT;
  if (Claimed.test_and_set(std::memory_order_acq_rel)) return;

  std::fprintf(stderr, "DIAG_INFLIGHT_SELECTED guest=%#lx host=%#lx\n", GuestRip, HostCode);
  std::fflush(stderr);
  if (FILE* Selected = std::fopen(SelectedPath, "w")) {
    std::fprintf(Selected, "%lx %lx\n", GuestRip, HostCode);
    std::fclose(Selected);
  }

  while (::access(ResumePath, F_OK) != 0) {
    ::usleep(1000);
  }
  std::fprintf(stderr, "DIAG_INFLIGHT_RESUME guest=%#lx host=%#lx\n", GuestRip, HostCode);
  std::fflush(stderr);
}

'''
    if anchor not in s:
        raise SystemExit("ExitFunctionLink anchor missing")
    s = s.replace(anchor, helper + anchor, 1)

    lock_anchor = '''  // Guard the LookupCache lock with the code invalidation mutex, to avoid issues with forking\n  auto lk_inval = GuardSignalDeferringSection<std::shared_lock>(static_cast<Context::ContextImpl*>(Thread->CTX)->CodeInvalidationMutex, Thread);\n\n  // Lock here is necessary to prevent simultaneous linking and delinking\n'''
    lock_repl = '''  {\n    // Guard the LookupCache lock with the code invalidation mutex, to avoid issues with forking.\n    // The diagnostic barrier is deliberately outside this scope so unload is not serialized by instrumentation.\n    auto lk_inval = GuardSignalDeferringSection<std::shared_lock>(static_cast<Context::ContextImpl*>(Thread->CTX)->CodeInvalidationMutex, Thread);\n\n    // Lock here is necessary to prevent simultaneous linking and delinking\n'''
    if lock_anchor not in s:
        raise SystemExit("second invalidation-lock anchor missing")
    s = s.replace(lock_anchor, lock_repl, 1)

    tail_anchor = '''    Thread->LookupCache->AddBlockLink(GuestRip, Record, IndirectBlockDelinker, lk);\n  }\n\n  return HostCode;\n}\n'''
    tail_repl = '''    Thread->LookupCache->AddBlockLink(GuestRip, Record, IndirectBlockDelinker, lk);\n    }\n  } // release lookup and code-invalidation guards before forcing the race\n\n  FieldworkPauseAfterExitLinkSelection(GuestRip, HostCode);\n  return HostCode;\n}\n'''
    if tail_anchor not in s:
        raise SystemExit("ExitFunctionLink tail anchor missing")
    s = s.replace(tail_anchor, tail_repl, 1)

    p.write_text(s)
    print("Patched ExitFunctionLink with env-gated post-selection barrier")


if __name__ == "__main__":
    main()
