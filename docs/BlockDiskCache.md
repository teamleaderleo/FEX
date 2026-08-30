# Block-level Fossilize disk cache

FEX has more than one persistent translated-code mechanism. This page covers the online,
block-level Fossilize database implemented by `DiskCache.cpp`. It is not the offline whole-file
cache described in [Whole-file code-cache identity](WholeFileCodeCacheIdentity.md), the live
guest-to-host lookup tables, or compiler `ccache` used by developers.

## The shortest useful model

```text
online CompileBlock miss
  -> compile one guest block
  -> serialize host bytes + entrypoints + touched pages + relocations
  -> append one blob and one index record to the writable Fossilize database

later lookup of the same module offset
  -> index by bucket identity + module offset
  -> read one blob
  -> validate its bounded layout and live guest-byte hash
  -> relocate/copy host bytes into the live JIT code buffer
  -> publish its entrypoints and page ownership to live lookup tables
```

The cache avoids recompiling an individual block. It does not map host code directly into the JIT,
and it does not make the live lookup cache persistent.

## Identity and files

The bucket identity hashes block-cache format version 3, guest bitness, the effective host-feature
hash and `Config::SerializeForCache()`. Its default directory name is that 128-bit bucket hash. A
blob key hashes the module-relative guest entrypoint with the same bucket identity. A hit still
hashes the live guest bytes and compares them with the blob header, so the module-offset key is not
treated as content authority.

One writable database uses `RWCacheDB.foz` plus `RWCacheDB_idx.foz`. Optional named read-only
databases share the same in-memory index. Writes append the data blob first and the index record
second under file locks, then publish the new in-process index entry. The worker thread makes store
latency asynchronous; it does not make publication transactional across both files after a crash.
A data-only append is therefore an unreferenced orphan rather than a readable entry.

The Fossilize stream format permits its final record to be truncated. On writable open, FEX keeps
the byte offset after the last complete index record. Before a later write reuses that offset, it
rechecks the index under the existing data/index file locks; another process changing the physical
index size also forces that recheck. The next complete fixed-size record overwrites the torn suffix
instead of being appended behind it. Read-only databases stop at the same valid prefix but are never
modified.

This matches the append boundary in
[ValveSoftware/Fossilize's stream archive](https://github.com/ValveSoftware/Fossilize/blob/d91d5228dfc9316ad7eda24ece9d02836eae31d8/fossilize_db.cpp):
the primary format documentation says a truncated final entry is acceptable, and append mode seeks
back to the first invalid record before its next write. FEX retains no resynchronization guess past
corrupt bytes. Records already appended behind that boundary were never discoverable; recovery may
overwrite their index bytes, while their data blobs remain unreferenced orphans.

The index field named `last_access_time` is currently written as zero. It is not last-use evidence
and grants no eviction authority. The implementation has no prune, compaction, quota or deletion
policy.

## Format-3 blob shape

The required prefix is, in order:

1. fixed counts, guest/host sizes and the live-guest hash;
2. generated host-code bytes;
3. touched guest-page offsets relative to the requested guest RIP;
4. guest entrypoint offsets relative to that RIP;
5. matching host-code offsets;
6. compact ordinary relocations; and
7. named-thunk relocations.

Store also appends the original guest bytes. Lookup does not consume that tail, so a read-only
database may omit it without changing the required lookup layout.

Upstream `b3f902166` changed lookup from a temporary `BlobEntryPoint` map plus copied guest-page
vector to spans over one owning blob. Guest-page and entrypoint offsets are relocated in that blob
in place. This removes those intermediate allocations, but it is not zero-copy: database reads
still copy into the owning vector, host code is copied into the live JIT buffer, and each live
entrypoint mapping owns its code-page list.

## Corruption boundary

All counts and sizes originate on disk. `DiskCacheFile::Validate` performs checked `size_t`
consumption in exact format order before `Lookup` constructs any span. It also requires 16-byte host
code sizing, a zero-relative primary guest entrypoint and host offsets within the code buffer.
Trailing stored guest bytes remain allowed.

The pure `FEXCore_Tests_DiskCacheFile` target covers the exact offsets, every strict required-prefix
truncation, count overflow, host-code alignment, missing primary entrypoint and out-of-range host
offsets. It compiles only the parser plus Catch2; it does not initialize a context, open a database,
compile guest code, load ARM64 host code or prove a performance benefit.

Relocation value/type semantics, fsync ordering, orphan reclamation, real lookup allocation counts
and cache retention remain separate questions. The pure `FEXCore_Tests_DiskCacheIndexFile` target
covers all 83 non-empty strict record prefixes, ordinary semantic skips, first-write recovery and
monotonic salvage of an already-poisoned suffix. It proves an append boundary, not power-loss
durability or transactionality. Do not turn either green parser into those claims.

`FEXCore_Tests_DiskCacheIndexRecovery` is the more expensive acceptance owner. It links FEXCore and
uses temporary real data/index files to prove read-only non-mutation, replacement of a 12-byte torn
suffix and size revalidation when a second handle appends first. Use the pure parser target for the
ordinary edit loop; rerun the integration owner only when file/lock/recovery wiring changes.

## Compare with the whole-file cache

| Boundary | Block DiskCache | Whole-file cache |
| --- | --- | --- |
| Producer | online `CompileBlock` | offline compiler from a captured whole-file code map |
| Reuse unit | one module-relative guest block | one executable file's translated code set |
| Storage | append-only Fossilize data + index files | one format-3 whole-file cache file |
| Lookup validation | bucket identity, live guest hash, bounded block layout | FEX/config identity plus complete structural file validation |
| Host-code use | copied into the current JIT buffer | mapped/loaded as a whole-file code buffer |
| Current retention | no trustworthy last-use or eviction owner | no observed Big Red corpus and no eviction authority |

Start in `DiskCache::Init`, `Lookup` and `Store`, then read `ContextImpl::CompileBlock` for the live
consumer. Read `CodeCacheFile::Validate` only when the question concerns the different whole-file
format.
