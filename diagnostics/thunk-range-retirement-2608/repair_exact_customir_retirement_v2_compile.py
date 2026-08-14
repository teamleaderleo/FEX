#!/usr/bin/env python3
"""Repair the v2 diagnostic's shared-map exact eviction call.

The v2 applicator originally placed InvalidateSharedEntry on LookupCache, but
CodeBuffer::LookupCache is a GuestToHostMap*. Keep the per-thread helper where
it is and erase the shared/L3 entry directly through GuestToHostMap under its
write lock.
"""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "FEXCore/Source/Interface/Core/Core.cpp"
OLD = """    if (auto Strong = it->lock()) {
      Strong->LookupCache->InvalidateSharedEntry(Address);
      ++it;
"""
NEW = """    if (auto Strong = it->lock()) {
      auto SharedLock = Strong->LookupCache->AcquireWriteLock();
      Strong->LookupCache->Erase(Address, SharedLock);
      ++it;
"""

text = PATH.read_text()
if NEW in text:
    print("shared-map eviction repair already applied")
elif text.count(OLD) == 1:
    PATH.write_text(text.replace(OLD, NEW, 1))
    print("shared-map eviction repair applied")
else:
    raise SystemExit(f"expected exactly one v2 shared-map call, found {text.count(OLD)}")

subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
