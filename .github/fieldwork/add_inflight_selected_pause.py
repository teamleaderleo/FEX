#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
path = root / 'FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp'
s = path.read_text()

include_anchor = '#include <cstring>\n'
if '#include <cstdlib>\n' not in s:
    if include_anchor not in s:
        raise SystemExit('missing cstring include anchor')
    s = s.replace(include_anchor, include_anchor + '#include <cstdlib>\n', 1)

sleep_anchor = '''static void SleepThread(FEXCore::Context::ContextImpl* CTX, FEXCore::Core::CpuStateFrame* Frame) {
  CTX->SyscallHandler->SleepThread(CTX, Frame);
}

'''
helper = r'''static constexpr uintptr_t DiagnosticInflightSyntheticH = 0x0000700000020000ULL;
static constexpr const char* DiagnosticInflightArm = "/tmp/fex-thunk-inflight-arm";
static constexpr const char* DiagnosticInflightSelected = "/tmp/fex-thunk-inflight-selected";
static constexpr const char* DiagnosticInflightResume = "/tmp/fex-thunk-inflight-resume";

static uintptr_t DiagnosticPauseSelectedThunk(uintptr_t HostBlock) {
  if (::access(DiagnosticInflightArm, F_OK) != 0) {
    return HostBlock;
  }

  const int FD = ::open(DiagnosticInflightSelected, O_CREAT | O_WRONLY | O_TRUNC, 0600);
  if (FD >= 0) {
    static constexpr char Marker[] = "selected\n";
    (void)::write(FD, Marker, sizeof(Marker) - 1);
    (void)::close(FD);
  }

  fprintf(stderr, "DIAG_INFLIGHT_SELECTED H=%#lx block=%#lx\n", DiagnosticInflightSyntheticH, HostBlock);
  for (size_t I = 0; I < 30000; ++I) {
    if (::access(DiagnosticInflightResume, F_OK) == 0) {
      fprintf(stderr, "DIAG_INFLIGHT_RESUME H=%#lx block=%#lx\n", DiagnosticInflightSyntheticH, HostBlock);
      return HostBlock;
    }
    ::usleep(1000);
  }

  fprintf(stderr, "DIAG_INFLIGHT_TIMEOUT H=%#lx block=%#lx\n", DiagnosticInflightSyntheticH, HostBlock);
  _exit(124);
}

'''
if 'DiagnosticPauseSelectedThunk' not in s:
    if sleep_anchor not in s:
        raise SystemExit('missing SleepThread anchor')
    s = s.replace(sleep_anchor, sleep_anchor + helper, 1)

jump_anchor = '''        // Jump to the block
        br(TMP4);
'''
injection = r'''        // Diagnostic-only deterministic race point. The lookup cache has already
        // yielded a compiled block for H, but the branch has not happened yet.
        // A second guest thread can now retire H and replace its guest owner.
        if (std::getenv("FEX_DIAG_INFLIGHT_SELECT")) {
          ARMEmitter::ForwardLabel l_NotDiagnosticInflightH;
          LoadConstant(ARMEmitter::Size::i64Bit, TMP1, DiagnosticInflightSyntheticH);
          sub(ARMEmitter::Size::i64Bit, TMP1, RipReg, TMP1);
          (void)cbnz(ARMEmitter::Size::i64Bit, TMP1, &l_NotDiagnosticInflightH);

          SpillStaticRegs(TMP1);
          mov(ARMEmitter::XReg::x0, TMP4);
          LoadConstant(ARMEmitter::Size::i64Bit, ARMEmitter::Reg::r1,
                       reinterpret_cast<uintptr_t>(&DiagnosticPauseSelectedThunk));
          if (!CTX->Config.DisableVixlIndirectCalls) [[unlikely]] {
            GenerateIndirectRuntimeCall<uintptr_t, uintptr_t>(ARMEmitter::Reg::r1);
          } else {
            blr(ARMEmitter::Reg::r1);
          }

          if (!TMP_ABIARGS) {
            mov(TMP1, ARMEmitter::XReg::x0);
          }
          FillStaticRegs();
          br(TMP1);

          (void)Bind(&l_NotDiagnosticInflightH);
        }

        // Jump to the block
        br(TMP4);
'''
if 'l_NotDiagnosticInflightH' not in s:
    if jump_anchor not in s:
        raise SystemExit('missing dispatcher jump anchor')
    s = s.replace(jump_anchor, injection, 1)

path.write_text(s)
print(path)
