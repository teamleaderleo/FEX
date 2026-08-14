#!/usr/bin/env python3
from pathlib import Path
import sys


def repl(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"

    repl(
        smc,
        '''  uint64_t SourceRetirementToken {};
  uint64_t DestinationRetirementToken {};

  const bool FixedMove = (flags & MREMAP_FIXED) && new_address;
  const bool DontUnmapMove = (flags & MREMAP_DONTUNMAP) && old_size;
  const bool SourceContentMoves = FixedMove || DontUnmapMove;
  if (Thread && SourceContentMoves) {
    if (auto* Thunks = GetThunkHandler()) {
      const auto OldLength = FEXCore::AlignUp(old_size, FEXCore::Utils::FEX_PAGE_SIZE);
      fprintf(stderr, "DIAG_MREMAP_PREPARE_SOURCE range=%#lx+%#lx dontunmap=%d fixed=%d\\n",
              reinterpret_cast<uintptr_t>(old_address), OldLength, DontUnmapMove ? 1 : 0, FixedMove ? 1 : 0);
      SourceRetirementToken =
        Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(old_address), OldLength);

      if (FixedMove) {
        const auto NewLength = FEXCore::AlignUp(new_size, FEXCore::Utils::FEX_PAGE_SIZE);
        fprintf(stderr, "DIAG_MREMAP_PREPARE_DEST range=%#lx+%#lx\\n",
                reinterpret_cast<uintptr_t>(new_address), NewLength);
        DestinationRetirementToken =
          Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(new_address), NewLength);
      }
    }
  }
''',
        '''  uint64_t SourceRetirementToken {};
  uint64_t RetainedPrefixRetirementToken {};
  uint64_t TruncatedTailRetirementToken {};
  uint64_t DestinationRetirementToken {};

  const bool FixedMove = (flags & MREMAP_FIXED) && new_address;
  const bool DontUnmapMove = (flags & MREMAP_DONTUNMAP) && old_size;
  const bool MayMove = flags & MREMAP_MAYMOVE;
  const bool HasSource = old_size != 0;
  const auto OldLength = FEXCore::AlignUp(old_size, FEXCore::Utils::FEX_PAGE_SIZE);
  const auto NewLength = FEXCore::AlignUp(new_size, FEXCore::Utils::FEX_PAGE_SIZE);
  const bool Shrink = HasSource && NewLength < OldLength;
  const bool WholeSourcePreRetire = HasSource && (FixedMove || DontUnmapMove || (MayMove && !Shrink));

  if (Thread) {
    if (auto* Thunks = GetThunkHandler()) {
      if (WholeSourcePreRetire) {
        fprintf(stderr,
                "DIAG_MREMAP_PREPARE_SOURCE range=%#lx+%#lx dontunmap=%d fixed=%d maymove=%d shrink=%d\\n",
                reinterpret_cast<uintptr_t>(old_address), OldLength, DontUnmapMove ? 1 : 0, FixedMove ? 1 : 0,
                MayMove ? 1 : 0, Shrink ? 1 : 0);
        SourceRetirementToken =
          Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(old_address), OldLength);
      } else if (HasSource && Shrink) {
        const auto PrefixLength = NewLength;
        const auto TailBase = reinterpret_cast<uintptr_t>(old_address) + NewLength;
        const auto TailLength = OldLength - NewLength;

        if (MayMove && PrefixLength) {
          fprintf(stderr, "DIAG_MREMAP_PREPARE_PREFIX range=%#lx+%#lx maymove=1\\n",
                  reinterpret_cast<uintptr_t>(old_address), PrefixLength);
          RetainedPrefixRetirementToken =
            Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(old_address), PrefixLength);
        }
        if (TailLength) {
          fprintf(stderr, "DIAG_MREMAP_PREPARE_TAIL range=%#lx+%#lx maymove=%d\\n",
                  TailBase, TailLength, MayMove ? 1 : 0);
          TruncatedTailRetirementToken = Thunks->PrepareGuestRangeRetirement(Thread, TailBase, TailLength);
        }
      }

      if (FixedMove) {
        fprintf(stderr, "DIAG_MREMAP_PREPARE_DEST range=%#lx+%#lx\\n",
                reinterpret_cast<uintptr_t>(new_address), NewLength);
        DestinationRetirementToken =
          Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(new_address), NewLength);
      }
    }
  }
''',
        'general source/prefix/tail preparation',
    )

    repl(
        smc,
        '''      if (DestinationRetirementToken) {
        Thunks->RollbackGuestRangeRetirement(Thread, DestinationRetirementToken);
      }
      if (SourceRetirementToken) {
        Thunks->RollbackGuestRangeRetirement(Thread, SourceRetirementToken);
      }
    }
    fprintf(stderr, "DIAG_MREMAP_ROLLBACK result=%#lx source-token=%#lx dest-token=%#lx\\n",
            *MremapFailureResult, SourceRetirementToken, DestinationRetirementToken);''',
        '''      if (DestinationRetirementToken) {
        Thunks->RollbackGuestRangeRetirement(Thread, DestinationRetirementToken);
      }
      if (TruncatedTailRetirementToken) {
        Thunks->RollbackGuestRangeRetirement(Thread, TruncatedTailRetirementToken);
      }
      if (RetainedPrefixRetirementToken) {
        Thunks->RollbackGuestRangeRetirement(Thread, RetainedPrefixRetirementToken);
      }
      if (SourceRetirementToken) {
        Thunks->RollbackGuestRangeRetirement(Thread, SourceRetirementToken);
      }
    }
    fprintf(stderr,
            "DIAG_MREMAP_ROLLBACK result=%#lx source-token=%#lx prefix-token=%#lx tail-token=%#lx dest-token=%#lx\\n",
            *MremapFailureResult, SourceRetirementToken, RetainedPrefixRetirementToken,
            TruncatedTailRetirementToken, DestinationRetirementToken);''',
        'general remap failure rollback',
    )

    repl(
        smc,
        '''  if (auto* Thunks = GetThunkHandler()) {
    if (SourceRetirementToken) {
      Thunks->CommitGuestRangeRetirement(SourceRetirementToken);
    }
    if (DestinationRetirementToken) {
      Thunks->CommitGuestRangeRetirement(DestinationRetirementToken);
    }
  }

  InvalidateCodeRangeIfNecessaryOnRemap''',
        '''  if (auto* Thunks = GetThunkHandler()) {
    const bool ResultMoved = Result != reinterpret_cast<uint64_t>(old_address);
    const bool SourceDefinitelyMoved = FixedMove || DontUnmapMove;

    if (SourceRetirementToken) {
      if (SourceDefinitelyMoved || ResultMoved) {
        fprintf(stderr, "DIAG_MREMAP_COMMIT_SOURCE token=%#lx moved=%d definite=%d\\n",
                SourceRetirementToken, ResultMoved ? 1 : 0, SourceDefinitelyMoved ? 1 : 0);
        Thunks->CommitGuestRangeRetirement(SourceRetirementToken);
      } else {
        fprintf(stderr, "DIAG_MREMAP_ROLLBACK_SOURCE_INPLACE token=%#lx\\n", SourceRetirementToken);
        Thunks->RollbackGuestRangeRetirement(Thread, SourceRetirementToken);
      }
    }

    if (RetainedPrefixRetirementToken) {
      if (ResultMoved) {
        fprintf(stderr, "DIAG_MREMAP_COMMIT_PREFIX_MOVED token=%#lx\\n", RetainedPrefixRetirementToken);
        Thunks->CommitGuestRangeRetirement(RetainedPrefixRetirementToken);
      } else {
        fprintf(stderr, "DIAG_MREMAP_ROLLBACK_PREFIX_INPLACE token=%#lx\\n", RetainedPrefixRetirementToken);
        Thunks->RollbackGuestRangeRetirement(Thread, RetainedPrefixRetirementToken);
      }
    }

    if (TruncatedTailRetirementToken) {
      fprintf(stderr, "DIAG_MREMAP_COMMIT_TAIL token=%#lx moved=%d\\n",
              TruncatedTailRetirementToken, ResultMoved ? 1 : 0);
      Thunks->CommitGuestRangeRetirement(TruncatedTailRetirementToken);
    }

    if (DestinationRetirementToken) {
      Thunks->CommitGuestRangeRetirement(DestinationRetirementToken);
    }
  }

  InvalidateCodeRangeIfNecessaryOnRemap''',
        'general remap success commit or rollback',
    )

    print('Generalized mremap lifetime transaction for move/grow/shrink outcomes')


if __name__ == '__main__':
    main()
