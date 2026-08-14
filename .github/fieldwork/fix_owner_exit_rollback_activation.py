#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    path = Path(sys.argv[1]).resolve() / "Source/Tools/LinuxEmulation/Thunks.cpp"
    text = path.read_text()
    old = '''    if (Host.Active) {
      CTX->ActivateThunkTrampolineIRHandler(Thread, Host.Host, Host.Active);
      fprintf(stderr, "DIAG_ROLLBACK_RESTORE H=%#lx T=%#lx claims=%zu\\n",
              Host.Host, Host.Active, Host.Claims.size());
    } else {'''
    new = '''    if (Host.Active) {
      uint64_t RestoredOwnerID {};
      for (const auto& Claim : Host.Claims) {
        if (Claim.Target == Host.Active) {
          RestoredOwnerID = Claim.OwnerID;
          break;
        }
      }
      LOGMAN_THROW_A_FMT(RestoredOwnerID, "Missing owner ID for restored H {:#x} -> T {:#x}", Host.Host, Host.Active);
      CTX->ActivateThunkTrampolineIRHandler(Thread, Host.Host, Host.Active, RestoredOwnerID);
      fprintf(stderr, "DIAG_ROLLBACK_RESTORE H=%#lx T=%#lx owner=%#lx claims=%zu\\n",
              Host.Host, Host.Active, RestoredOwnerID, Host.Claims.size());
    } else {'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"rollback activation: expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1))
    print('Carried restored claim owner ID into synthetic-H rollback activation')


if __name__ == '__main__':
    main()
