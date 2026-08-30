# Whole-file code-cache identity

FEX has more than one thing called a cache. This page concerns the whole-file translated-code
cache written by `FEXOfflineCompiler` and loaded by Linux syscall mapping or Windows
`ImageTracker`. It is not the block-level Fossilize database in `DiskCache.cpp`, the live lookup
cache, ccache, or the owned-fork submodule pack/origin caches.

## The old collision

Whole-file cache names ended in a 64-bit configuration field, but every producer and consumer used
zero:

```text
cache/<binary-name>-<file-id>-0000000000000000
```

The file header checked its `FXCC` magic, exact FEX Git identity and non-empty block list. It did
not bind the generated host code to guest bitness, effective host features, or settings such as
`MaxInst`, `Multiblock`, TSO policy and x87 precision. Two configurations could therefore ask for
the same pathname, and an exact FEX build alone did not distinguish their generated code.

## The identity boundary

`CodeCacheConfig::ComputeId` hashes one versioned, domain-separated byte sequence:

1. the whole-file identity format version;
2. 32- versus 64-bit guest mode;
3. `HostFeatures::HashForCaching()` after host-feature overrides; and
4. `Config::SerializeForCache()`, which is generated from options marked `AffectsCodeGen`.

The generated config inventory is preferable to a hand-maintained list: adding or reclassifying an
ordinary scalar/string option changes the serialized input automatically. `HostFeatures` is a
string-enum and is intentionally represented by its effective result rather than duplicated as an
override spelling. Runtime-only settings marked as not affecting code generation do not fragment
the namespace.

The public identity API lives in the lightweight `CodeCacheConfig.h` header and its implementation
lives in `CodeCacheConfig.cpp`. The focused `FEXCore_Tests_CodeCacheConfig` executable compiles that
production implementation directly with the configuration base and allocator hooks. It therefore
exercises the real identity and snapshot owner without linking the runtime/JIT `FEXCore` archive.
`CodeCache.h` includes the lightweight header for source compatibility with existing consumers.

This 64-bit ID is a disposable cache namespace, not source or evidence authority. Cache loading
still requires the exact FEX Git identity, the expected format, a matching ID inside the file
header and structurally usable contents.

## The format is one bounded layout

Format 3 is not a bag of independently trusted counts. It is one contiguous file:

1. a fixed 72-byte header;
2. `NumBlocks` variable records containing guest offset, host-code offset and guest code pages;
3. `NumRelocations` fixed 48-byte relocation records;
4. zero padding to a 4 KiB boundary;
5. one page-sized-multiple host-code region; and
6. `NumCodePages` variable guest-page-to-entrypoint records ending at logical EOF.

The runtime now parses that layout with checked file-relative offsets before allocating executable
state. It rejects a truncated header or region, an unsupported identity, unsorted producer indexes,
host-code offsets outside the code region, unknown/unsorted/out-of-range relocations, a zero or
unaligned code region, malformed page records and nonzero/oversized trailing data. A Windows mapped
view may include the zero-filled remainder of its final page; that bounded padding is accepted but
never enters a parsed span. This matches the platform mapping contracts: Windows rounds a section
view size up to `PAGE_SIZE`, while POSIX zero-fills the partial page at an object's end
([Microsoft `ZwMapViewOfSection`](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-zwmapviewofsection),
[POSIX `mmap`](https://man7.org/linux/man-pages/man3/mmap.3p.html)). Every accepted span used by
`LoadCache` therefore comes from the one validated layout instead of fresh header-controlled pointer
arithmetic. A stale interrupted `.new` file is truncated before regeneration so old tail bytes
cannot be published behind a fresh header.

This is structural validation, not authentication. A same-user process can still replace a cache
with a different structurally valid file, and changing or truncating an already mapped file remains
a separate concurrent-mutation problem. The loader skips invalid files; it does not delete them or
choose a retention/eviction policy.

## Fail-closed generation flow

```text
FEX client freezes canonical snapshot S and effective host-feature bits H
  -> client computes requested ID C from S + H + guest bitness
  -> FEXServer receives program fd + C + H + bounded S
  -> server checks <binary>-<file-id>-C
  -> server gives S to FEXOfflineCompiler through an inherited pipe
  -> compiler schema-validates and atomically applies S, reconstructs H, and computes C'
       C' != C: refuse before InitCore/compilation; emit no cache
       C' == C: compile and write filename C + header C
  -> runtime opens filename C and independently requires header C
```

The compiler never trusts a client-supplied ID as a label for code generated under different
settings. The local request is a fixed 32-byte header followed by a length-delimited snapshot.
The snapshot is capped at 64 KiB, is neither placed in argv nor persisted to a temporary file, and
must contain the complete generated `AffectsCodeGen` scalar/string inventory in canonical order.
Unknown, missing, duplicated, truncated, extra and non-canonical fields are rejected before any
snapshot value is applied. The effective `HostFeatures` result remains a separate fixed 64-bit
input because the source option is a string-enum rather than a canonical scalar.

Manual `generate` without `--config-id` remains possible and names output with the compiler's
actual ID. A transported snapshot requires the exact ID and host-feature inputs together; partial
snapshot arguments are rejected. A supplied ID must be exactly 16 hexadecimal digits.

`process-all` computes the same identity for each executable bitness. Windows cache loading uses
the context identity too, although the current owned-fork evidence below is Linux/x86-host build
evidence rather than Windows or ARM64 runtime acceptance.

## Current evidence and limits

At the owned-fork candidate based on
`2ae75f1f74c96f5ad6747763fdd2780d2e02012f`:

- the three new exact cases pass: canonical snapshot round-trip; atomic rejection of truncated,
  unknown, wrong-option, non-canonical, duplicated, missing, extra and oversized forms; and exact
  reconstruction of effective host-feature bits;
- exact focused targets `FEXCore_Tests_CodeCacheConfig`, `FEX`, `FEXServer` and
  `FEXOfflineCompiler` compiled locally;
- the current default canonical snapshot is 416 bytes, versus the explicit 65,536-byte protocol
  ceiling;
- a valid one-entry `/bin/true` codemap plus a transported snapshot and zeroed effective-host bits
  recomputed ID `b6392f4f1b85c4c4`; requested ID zero was refused and emitted no file;
- the same inputs with that independently reported ID produced one 8,216-byte format-3 cache with
  the ID in its filename and header;
- an isolated FEXServer probe split the 32-byte header into 1/1/2/5/8/15-byte writes and the
  416-byte snapshot into 37-byte writes, with the program descriptor attached only to byte one.
  The server reconstructed the frame, invoked the compiler through its inherited pipe, and
  produced the same 8,216-byte cache.

The 64 KiB ceiling is a protocol limit, not evidence that legitimate app configurations approach
it. Raise it only from an observed valid snapshot distribution and retain a finite cap. The
snapshot currently supports generated scalar/string codegen options; adding a codegen-affecting
string-array or a second string-enum intentionally fails at compile time until it receives an
explicit canonical representation.

No broad suite, x86 guest, ARM64 product runtime, Windows runtime, cache eviction, compression,
code-map redesign or upstream submission is implied by this evidence.
