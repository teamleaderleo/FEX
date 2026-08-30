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

This 64-bit ID is a disposable cache namespace, not source or evidence authority. Cache loading
still requires the exact FEX Git identity, the expected format, a matching ID inside the file
header and structurally usable contents.

## Fail-closed generation flow

```text
FEX client computes requested ID C
  -> FEXServer receives program fd + C
  -> server checks <binary>-<file-id>-C
  -> server runs FEXOfflineCompiler --config-id C
  -> compiler loads its own effective configuration and computes C'
       C' != C: refuse before InitCore/compilation; emit no cache
       C' == C: compile and write filename C + header C
  -> runtime opens filename C and independently requires header C
```

The compiler never trusts a client-supplied ID as a label for code generated under different
settings. Manual `generate` without `--config-id` remains possible and names output with the
compiler's actual ID. A supplied ID must be exactly 16 hexadecimal digits.

`process-all` computes the same identity for each executable bitness. Windows cache loading uses
the context identity too, although the current owned-fork evidence below is Linux/x86-host build
evidence rather than Windows or ARM64 runtime acceptance.

## Current evidence and limits

At candidate parent `5d7bcec8f568b40af82401b06180ae5efe0ae4a0`:

- four focused identity cases passed 17 assertions: stable identical input; separation by bitness,
  host features, `MaxInst`, `Multiblock`, TSO, x87 and extended volatile metadata; and no split for
  a runtime-only lazy-load setting;
- exact focused targets `FEXCore_Tests_CodeCacheConfig`, `FEX`, `FEXServer` and
  `FEXOfflineCompiler` compiled locally;
- a valid one-entry `/bin/true` codemap with requested ID zero was refused before compilation and
  emitted no file;
- the same fixture using the independently reported ID produced one 8,224-byte cache with that ID
  in its filename and format-3 header.

The important remaining limitation is configuration transport. `FEXOfflineCompiler` currently
reloads global/default configuration rather than receiving the client's final app-specific config
snapshot. If those differ, the new handshake refuses generation. That is the correct failure mode,
but it means configuration-specific caching is not yet universally available.

A later transport must carry a bounded, schema-validated final codegen snapshot (or an equally
exact reconstruction identity), bind it to the request and compiler receipt, and retain the
independent recomputation check. Do not solve the limitation by accepting the client's hash,
falling back to suffix zero, or silently compiling with global defaults.

No broad suite, x86 guest, ARM64 product runtime, Windows runtime, cache eviction, compression,
code-map redesign or upstream submission is implied by this evidence.
