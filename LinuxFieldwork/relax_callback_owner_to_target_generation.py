#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    p = Path(sys.argv[1]).resolve() / "Source/Tools/LinuxEmulation/Thunks.cpp"
    text = p.read_text()
    old = '''  LOGMAN_THROW_A_FMT(UnpackerOwnerID != 0 && TargetOwnerID != 0,
                     "Diagnostic callback owner IDs must be non-zero: unpacker={:#x} target={:#x}", UnpackerOwnerID, TargetOwnerID);
  LOGMAN_THROW_A_FMT(UnpackerOwnerID == TargetOwnerID,
                     "Diagnostic owner-generation prototype requires one owner: unpacker={} target={}", UnpackerOwnerID, TargetOwnerID);

  // Try first with shared_lock'''
    new = '''  LOGMAN_THROW_A_FMT(TargetOwnerID != 0,
                     "Diagnostic callback target OwnerID must be non-zero: unpacker={:#x} target={:#x}", UnpackerOwnerID, TargetOwnerID);
  fprintf(stderr,
          "DIAG_CALLBACK_OWNER_SPLIT unpacker_owner=%#lx target_owner=%#lx unpacker=%#lx target=%#lx\\n",
          UnpackerOwnerID, TargetOwnerID, GuestUnpacker, GuestTarget);

  // The generated unpacker may live in a process-resident companion with a
  // different OwnerID. The reclaimable execution lease is keyed to the
  // application callback target generation for this discriminator.
  // Try first with shared_lock'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"split-owner registration anchor: expected one, found {count}")
    p.write_text(text.replace(old, new, 1))
    print("Relaxed callback owner prototype to lease TargetOwnerID with independent resident unpacker owner")


if __name__ == "__main__":
    main()
