# Owned-fork FEX learning path

Reviewed against owned-fork main
[`66f88dd3a`](https://github.com/teamleaderleo/FEX/commit/66f88dd3ac323ceb7890cae92b27c09b7338a0fb)
on 2026-08-30. This page answers “which fork PRs do I actually need to understand?” for a C++
newcomer. It is a curriculum and history index, not an upstream contribution plan.

Use [the research map](OwnedForkResearchMap.md) when starting from a symptom. Use this page when
starting from the fork's history. Current source and owning tests are authoritative; a PR diff is
useful for learning why a boundary exists, not proof that its old snapshot is still the current
implementation.

## The short answer

Do not read 56 PRs in number order. Learn these six arcs:

1. **Guest/host thunk ownership:** PRs #5, #12 and #14 explain why guest pointers, host copies and
   copyback/cleanup are different ownership decisions.
2. **Vulkan selection and future dispatch:** PRs #1/#2 choose the safe route; PR #15 replaces one
   synthetic native-H to guest-G route and retires its exact live lookup entries.
3. **Already-escaped executable lifetime:** PR #23 creates the generator primitive; PRs #24 and #31
   give GL and Vulkan their own process-resident bridge owners.
4. **Whole-file cache identity:** PRs #36/#37 bind offline code generation to complete effective
   configuration; PR #43 validates the mapped file before constructing views.
5. **Online block cache layout:** PRs #49/#50/#51 bound the blob, recover a torn final index suffix
   and stop writing a guest-byte tail no reader consumes.
6. **Fast experiment loop:** PRs #4/#39/#40 make one stable C++ object and its compiler-cache result
   easy to request; PRs #46/#47 select one target's authoritative CTest set; PR #57 only plans
   retirement of a reconstructible dead lane.

Read the remaining PRs when you need their tooling or historical discriminator. In particular,
PRs #52-#56 are the bounded ARM disk-cache/ccache experiment and its carrier corrections, not five
new cache semantics.

## Thirty-second C++/FEX vocabulary

| Term | Meaning in these fixes | Common mistake |
| --- | --- | --- |
| guest | x86/x86-64 application code and addresses | treating a guest pointer as a native ARM64 pointer |
| host | native library/FEX code on the ARM64 machine | assuming equal C++ spelling means equal ABI layout |
| thunk | generated plus hand-written bridge for an API call | looking only at `Guest.cpp` or only at `Host.cpp` |
| H → G | native function address H dispatched through guest invoker G | confusing future route selection with callback lifetime |
| T | host trampoline retained by a native library to call guest-associated code | assuming cache invalidation can revoke an already-escaped T |
| owner | object whose lifetime keeps bytes/code valid, such as a vector or resident DSO | treating `std::span`, a raw pointer or an address as ownership |
| identity | inputs that must match before reuse, not merely a filename/path | calling a content-addressed filename sufficient validation |
| live lookup cache | in-process guest/native address-to-compiled-code tables | confusing it with either persistent disk-cache format |
| whole-file cache | offline-produced cache for one executable's translated code set | confusing it with per-block Fossilize storage |
| block DiskCache | online append-only Fossilize blobs/index for individual blocks | assuming its zero `last_access_time` field is eviction evidence |

Headers normally declare a type or callable contract; `.cpp` files own behavior. Generated thunk
code starts with declarations/policies, flows through generator analysis/emission, and ends in a
library-specific guest/host integration. A `reinterpret_cast` can change the compiler's view of an
address; it does not change the pointee ABI or establish ownership.

## Read current code in this order

### 1. Repacking and route selection

Start with [Thunk repacking](ThunkRepacking.md), then read:

1. [`libvulkan_interface.cpp`](../ThunkLibs/libvulkan/libvulkan_interface.cpp) for generated
   declarations and annotations;
2. [`Host.cpp`](../ThunkLibs/libvulkan/Host.cpp) for native lookup and host-side ownership;
3. [`Guest.cpp`](../ThunkLibs/libvulkan/Guest.cpp) for guest-visible proc-address invokers; and
4. [`generator.cpp`](../unittests/ThunkLibs/generator.cpp) for small output/ownership invariants.

The key split is route selection versus argument ownership. PRs #1/#2 answer “which function will a
later guest call?” PRs #5/#12/#14 answer “whose memory may the host modify, copy back or free?”

### 2. Synthetic routes and live cache retirement

Read [the callback lifetime map](LinuxFieldworkLifetimeMap.md), then follow one H → G replacement:

1. [`Thunks.cpp`](../Source/Tools/LinuxEmulation/Thunks.cpp) receives the link request;
2. [`Core.cpp`](../FEXCore/Source/Interface/Core/Core.cpp) replaces the exact thunk-owned CustomIR
   mapping;
3. [`ThreadManager.h`](../Source/Tools/LinuxEmulation/LinuxSyscalls/ThreadManager.h) owns the lock
   ordering across replacement and thread-local retirement;
4. [`LookupCache.h`](../FEXCore/Source/Interface/Core/LookupCache.h) removes exact shared/local
   entries; and
5. [`LookupCache.cpp`](../FEXCore/unittests/APITests/LookupCache.cpp) constructs the otherwise
   invisible empty-`CodePages` case.

PR #15 repairs future lookup after rebind. It does not revoke code a thread already selected or a
host trampoline already retained.

### 3. Resident bridge ownership

Read [Resident thunk bridges](ResidentThunkBridge.md), then separate generator, build and product:

1. [`analysis.cpp`](../ThunkLibs/Generator/analysis.cpp) decides what bridge output is requested;
2. [`gen.cpp`](../ThunkLibs/Generator/gen.cpp) emits definitions/accessors;
3. [`GuestLibs/CMakeLists.txt`](../ThunkLibs/GuestLibs/CMakeLists.txt) creates a companion DSO; and
4. [`libvulkan_bridge/Guest.cpp`](../ThunkLibs/libvulkan_bridge/Guest.cpp) is one product adoption.

PR #23 is the opt-in generator primitive. PR #24 proves the GL architecture. PR #31 applies the
same ownership shape to Vulkan's distinct signature/publication inventory. Per-library identity is
intentional; equal-looking signatures do not prove equal ABI and lifetime policy.

### 4. Whole-file code-cache identity and layout

Read [Whole-file code-cache identity](WholeFileCodeCacheIdentity.md), then:

1. [`CodeCache.h`](../FEXCore/include/FEXCore/Core/CodeCache.h) for the identity/header contract;
2. [`CodeCache.cpp`](../FEXCore/Source/Interface/Core/CodeCache.cpp) for producer/consumer flow;
3. [`CodeCacheFile.cpp`](../FEXCore/Source/Interface/Core/CodeCacheFile.cpp) for bounded parsing;
4. [`CodeCacheConfig.cpp`](../FEXCore/unittests/APITests/CodeCacheConfig.cpp) for configuration
   identity; and
5. [`CodeCacheFile.cpp`](../FEXCore/unittests/APITests/CodeCacheFile.cpp) for structural prefixes.

PR #36 creates the identity. PR #37 transports the exact app-specific snapshot to the offline
compiler. PR #43 makes the mapped disk layout total before any span/view is constructed. None is an
eviction policy.

### 5. Online block-level Fossilize storage

Read [Block-level Fossilize disk cache](BlockDiskCache.md), then:

1. [`DiskCache.cpp`](../FEXCore/Source/Interface/Core/DiskCache.cpp) for Init/Lookup/Store;
2. [`DiskCacheFile.cpp`](../FEXCore/Source/Interface/Core/DiskCacheFile.cpp) for blob validation;
3. [`DiskCacheIndexFile.cpp`](../FEXCore/Source/Interface/Core/DiskCacheIndexFile.cpp) for the valid
   index prefix; and
4. the corresponding [`DiskCacheFile`](../FEXCore/unittests/APITests/DiskCacheFile.cpp),
   [`DiskCacheIndexFile`](../FEXCore/unittests/APITests/DiskCacheIndexFile.cpp) and
   [`DiskCacheIndexRecovery`](../FEXCore/unittests/APITests/DiskCacheIndexRecovery.cpp) owners.

PRs #49/#50/#51 are format/safety/storage changes. PR #52 is the real ARM producer/analyzer profile
that measured their tiny current corpus; it is evidence infrastructure, not another writer format.

### 6. Developer and experiment machinery

Read [the development loop](ResearchDevLoop.md). The two central owners are:

- [`ResearchDevBuild.py`](../Scripts/ResearchDevBuild.py): local stable lanes, submodule caches,
  exact source/object/target/test selection, receipts and read-only retirement planning;
- [`ResearchProfileCarrier.py`](../Scripts/ResearchProfileCarrier.py): checked-in immutable profile
  dispatch and strict outcome framing for longer hosted work.

Use `doctor`, then compile one current file or check one exact owner. Do not infer ARM runtime from
an x86 host build, and do not run a broad suite to learn a static ownership boundary.

## Complete merged-PR atlas through #57

Every merged owned-fork PR from #1 through #57 appears exactly once below. [PR
#3](https://github.com/teamleaderleo/FEX/pull/3) was a closed, unmerged diagnostic carrier and is not
current source.

<!-- merged-pr-atlas-start -->
- **ABI, routing and repacking:** [#1](https://github.com/teamleaderleo/FEX/pull/1),
  [#2](https://github.com/teamleaderleo/FEX/pull/2),
  [#5](https://github.com/teamleaderleo/FEX/pull/5),
  [#8](https://github.com/teamleaderleo/FEX/pull/8),
  [#11](https://github.com/teamleaderleo/FEX/pull/11),
  [#12](https://github.com/teamleaderleo/FEX/pull/12),
  [#13](https://github.com/teamleaderleo/FEX/pull/13),
  [#14](https://github.com/teamleaderleo/FEX/pull/14), and
  [#25](https://github.com/teamleaderleo/FEX/pull/25).
- **Live translation ownership and invalidation:** [#6](https://github.com/teamleaderleo/FEX/pull/6),
  [#15](https://github.com/teamleaderleo/FEX/pull/15), and
  [#16](https://github.com/teamleaderleo/FEX/pull/16).
- **Resident bridge generation/adoption:** [#23](https://github.com/teamleaderleo/FEX/pull/23),
  [#24](https://github.com/teamleaderleo/FEX/pull/24), and
  [#31](https://github.com/teamleaderleo/FEX/pull/31).
- **Whole-file code-cache identity/layout:** [#36](https://github.com/teamleaderleo/FEX/pull/36),
  [#37](https://github.com/teamleaderleo/FEX/pull/37), and
  [#43](https://github.com/teamleaderleo/FEX/pull/43).
- **Online block-cache product semantics:** [#49](https://github.com/teamleaderleo/FEX/pull/49),
  [#50](https://github.com/teamleaderleo/FEX/pull/50), and
  [#51](https://github.com/teamleaderleo/FEX/pull/51).
- **Local development, dependency storage and receipts:**
  [#4](https://github.com/teamleaderleo/FEX/pull/4),
  [#9](https://github.com/teamleaderleo/FEX/pull/9),
  [#17](https://github.com/teamleaderleo/FEX/pull/17),
  [#18](https://github.com/teamleaderleo/FEX/pull/18),
  [#21](https://github.com/teamleaderleo/FEX/pull/21),
  [#27](https://github.com/teamleaderleo/FEX/pull/27),
  [#28](https://github.com/teamleaderleo/FEX/pull/28),
  [#29](https://github.com/teamleaderleo/FEX/pull/29),
  [#30](https://github.com/teamleaderleo/FEX/pull/30),
  [#32](https://github.com/teamleaderleo/FEX/pull/32),
  [#33](https://github.com/teamleaderleo/FEX/pull/33),
  [#34](https://github.com/teamleaderleo/FEX/pull/34),
  [#35](https://github.com/teamleaderleo/FEX/pull/35),
  [#39](https://github.com/teamleaderleo/FEX/pull/39),
  [#40](https://github.com/teamleaderleo/FEX/pull/40),
  [#41](https://github.com/teamleaderleo/FEX/pull/41), and
  [#57](https://github.com/teamleaderleo/FEX/pull/57).
- **Focused verification, CI carriers and result documentation:**
  [#7](https://github.com/teamleaderleo/FEX/pull/7),
  [#10](https://github.com/teamleaderleo/FEX/pull/10),
  [#19](https://github.com/teamleaderleo/FEX/pull/19),
  [#20](https://github.com/teamleaderleo/FEX/pull/20),
  [#22](https://github.com/teamleaderleo/FEX/pull/22),
  [#26](https://github.com/teamleaderleo/FEX/pull/26),
  [#38](https://github.com/teamleaderleo/FEX/pull/38),
  [#42](https://github.com/teamleaderleo/FEX/pull/42),
  [#44](https://github.com/teamleaderleo/FEX/pull/44),
  [#45](https://github.com/teamleaderleo/FEX/pull/45),
  [#46](https://github.com/teamleaderleo/FEX/pull/46),
  [#47](https://github.com/teamleaderleo/FEX/pull/47),
  [#52](https://github.com/teamleaderleo/FEX/pull/52),
  [#53](https://github.com/teamleaderleo/FEX/pull/53),
  [#54](https://github.com/teamleaderleo/FEX/pull/54),
  [#55](https://github.com/teamleaderleo/FEX/pull/55), and
  [#56](https://github.com/teamleaderleo/FEX/pull/56).
- **Upstream product sync and overlap review:**
  [#48](https://github.com/teamleaderleo/FEX/pull/48).
<!-- merged-pr-atlas-end -->

## How to inspect a PR without getting lost

Start from the merge commit recorded by GitHub, compare it with its first parent, and narrow to the
owner you are learning:

```sh
git show --first-parent --stat MERGE_COMMIT
git diff MERGE_COMMIT^1 MERGE_COMMIT -- path/to/owner.cpp path/to/owner_test.cpp
```

Then return to current main and find the smallest owner:

```sh
./Scripts/ResearchDevBuild.py doctor
./Scripts/ResearchDevBuild.py --lane editor discover LITERAL
./Scripts/ResearchDevBuild.py --lane editor compile path/to/current.cpp
```

Use `check` or `check-set` only when the current question reaches a registered host-side test.
Use a checked-in ARM profile only when cross-ISA runtime behavior is the unresolved oracle. Reuse
an accepted exact result when source, inputs, toolchain and relevant environment are unchanged.

## Boundaries to remember

- This fork is authorized internal research, not an upstream-ready contribution series.
- A green generator/parser/host test proves only its stated boundary.
- Cache identity does not grant authenticity, last-use, eviction or deletion authority.
- Future route repair does not revoke already-selected or escaped executable code.
- Persistent file validation does not make a live in-process lookup table persistent.
- Developer ccache and submodule pack/origin caches are build machinery, not FEX guest-code caches.
- PR history explains causality; current main plus current owning tests define behavior now.
