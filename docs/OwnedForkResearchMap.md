# Owned-fork FEX research map

Reviewed against owned-fork main
[`fb3f5325f`](https://github.com/teamleaderleo/FEX/commit/fb3f5325f9b87bd83709d968b6a6926fd38a8d3a)
on 2026-08-31. This is a reading and experiment router for `teamleaderleo/FEX`, not an upstream
submission plan and not a claim that every thunk or Vulkan lifetime problem is solved.

If the starting question is “which fork PRs should I learn, and in what order?” use the
[owned-fork learning path](OwnedForkLearningPath.md). This page remains the symptom-to-owner router.

## Start with the boundary, not the largest test

Several failures can all look like “a callback crashed,” but the pointer became unsafe at different
boundaries. Identify that boundary before changing code or choosing an oracle.

| Symptom or question | Owning boundary | Current owned-fork status | Start reading |
| --- | --- | --- | --- |
| Direct Vulkan call works, but GIPA/GDPA returns an unsafe or inconsistent function | native availability and custom-wrapper selection | merged routing plus exhaustive name inventory | [Vulkan proc-address routing](VulkanProcAddressRouting.md) |
| A guest/host struct has different layout, or `const` input is copied back | generated repacking and allocation/copyback ownership | merged const preservation, host-only cleanup and bounded Vulkan `pNext` copying | [Custom thunk repacking](ThunkRepacking.md) |
| The same native address H is registered for a newer guest invoker, but later dispatch reaches the old one | synthetic CustomIR registry plus exact shared/thread cache retirement | merged exact-identity rebind transaction | [Callback lifetime map](LinuxFieldworkLifetimeMap.md) |
| A native library retained a guest executable address after its ordinary wrapper unloaded | already-escaped executable ownership | measured GL and Vulkan companions; other libraries not adopted | [Resident thunk bridges](ResidentThunkBridge.md), [GL companion](GLResidentCompanion.md), and [Vulkan companion](VulkanResidentCompanion.md) |
| `MREMAP_FIXED` replaced code at D, but execution at D used the old translation | guest virtual-memory mutation versus translated-code cache | merged destination invalidation for the focused fixed-remap sequence | [Fixed-remap destination cache](MremapFixedDestinationCache.md) |
| A whole-file code cache was generated under different JIT settings or host features | cache namespace and offline-compiler configuration agreement | owned-fork configuration identity, canonical snapshot transport, fail-closed compiler reconstruction and a lightweight seven-case owner | [Whole-file code-cache identity](WholeFileCodeCacheIdentity.md) |
| A block-level Fossilize cache lookup is corrupt, slow or confused with whole-file caching | per-block bucket/key identity, bounded blob/index layout and live publication | format-3 blobs are bounded before spans, torn writable index suffixes resume at the valid prefix, the next writer reclaims only an unreferenced physical data tail and a read-only analyzer accounts physical extents; the real recovery owner is lightweight, while interior compaction and allocation/runtime measurement remain separate | [Block-level Fossilize disk cache](BlockDiskCache.md) |
| GLX string-return thunks fail to compile under Clang 21 | fixed-width guest layout versus signed host character pointer conversion | merged shared host-layout conversion and two focused generator guards | [PR #25](https://github.com/teamleaderleo/FEX/pull/25) |
| A new chat needs to configure, build, test or debug something | environment, exact source and smallest owner | `doctor`, stable external lanes, one target or one producer-owned CTest set, lightweight cache identity/recovery owners and one focused VS Code debugger launch are merged | [Owned-fork development loop](ResearchDevLoop.md) |

Do not begin with a broad FEX build or suite merely because the symptom is unfamiliar. Most rows
have a static inventory, generator case, one host target or one ARM profile that answers the first
decision-changing question more directly.

## The three pointer problems people most often conflate

```text
Vulkan name/scope
  -> choose native entry or FEX wrapper          (routing)
  -> associate native H with guest invoker G     (CustomIR rebind/cache retirement)
  -> native code retains G or callback unpacker  (executable lifetime ownership)
```

1. **Routing** decides which implementation a future call receives. The merged Vulkan path asks the
   native loader first and substitutes a FEX custom wrapper only after native availability succeeds.
2. **Exact rebinding** changes a future H → G association and retires the exact shared and per-thread
   cached entry. Range invalidation alone can miss a synthetic CustomIR block because it has no
   decoded guest `CodePages` membership.
3. **Resident ownership** keeps executable glue alive after its address escaped. It cannot revoke
   code another thread already selected, and cache invalidation cannot recreate a wrapper-local
   invoker after that wrapper unmapped.

A patch at one layer must not be reported as solving the next layer.

## What is merged now

### Vulkan selection and argument ownership

- [PR #1](https://github.com/teamleaderleo/FEX/pull/1), merge `77bebaf521`, makes GIPA/GDPA preserve
  native availability before substituting callback-sensitive custom wrappers.
- [PR #2](https://github.com/teamleaderleo/FEX/pull/2), merge `582d925062`, requires the generated
  `custom_host_impl` inventory and dynamic custom-route inventory to agree for both thunk widths.
- [PR #5](https://github.com/teamleaderleo/FEX/pull/5), merge `ed9507378c`, preserves pointee
  `const` in generated `repack_wrapper<T>` types.
- [PR #12](https://github.com/teamleaderleo/FEX/pull/12), merge `44e15cf5b6`, copies and relinks the
  legal instance-creation callback `pNext` chain instead of modifying caller-owned const pages.
- [PR #13](https://github.com/teamleaderleo/FEX/pull/13), merge `645ae26401`, refuses
  `VK_LUNARG_direct_driver_loading` at the guest boundary; it does not mediate an x86 driver inside
  native Vulkan.
- [PR #14](https://github.com/teamleaderleo/FEX/pull/14), merge `f1f1e6a36a`, separates mutable
  exit/copyback from const host-only cleanup for custom repackers.

The declaration authority is
[`libvulkan_interface.cpp`](../ThunkLibs/libvulkan/libvulkan_interface.cpp). Dynamic selection and
hand-written ownership live in [`Host.cpp`](../ThunkLibs/libvulkan/Host.cpp). Guest function-pointer
association lives in [`Guest.cpp`](../ThunkLibs/libvulkan/Guest.cpp). These files have different
jobs even when they mention the same Vulkan command.

### Exact CustomIR rebinding

[PR #15](https://github.com/teamleaderleo/FEX/pull/15), merge `1ab30cadba`, owns the future-dispatch
case where the same native H is rebound to a different guest G. The transaction is split across:

1. [`ThreadManager.h`](../Source/Tools/LinuxEmulation/LinuxSyscalls/ThreadManager.h), which holds
   thread-creation and code-invalidation ownership across replacement and retirement;
2. [`Core.cpp`](../FEXCore/Source/Interface/Core/Core.cpp), which replaces only a thunk-owned entry
   with the same H and a different G;
3. [`LookupCache.h`](../FEXCore/Source/Interface/Core/LookupCache.h), which erases exactly H and
   delinks inbound direct-block links even when range reverse indexes cannot see the entry; and
4. [`LookupCache.cpp`](../FEXCore/unittests/APITests/LookupCache.cpp), whose focused case proves the
   empty-`CodePages` distinction.

The merged result retires shared code-buffer entries, each live thread's exact lookup entry and its
call/return shadow cache. It does not stop a thread already executing or already holding an escaped
wrapper address.

### Resident generation and the measured GL/Vulkan adoptions

[PR #23](https://github.com/teamleaderleo/FEX/pull/23), merge `6442dc5af6`, lets one thunk-generator
analysis optionally emit ordinary guest output plus resident invoker definitions and typed
accessors. The primitive is library-neutral and opt-in.

[PR #24](https://github.com/teamleaderleo/FEX/pull/24), merge `acb9b7f66b`, makes GL build
`libfex-GL-bridge.so`, applies `DF_1_NODELETE` to that companion rather than the ordinary wrapper,
and moves the published GL/X11/malloc executable targets under its lifetime. The indexed dispatcher
keeps the ordinary GL wrapper at 39 dynamic relocations rather than the rejected 774-relocation
per-signature design.

[PR #31](https://github.com/teamleaderleo/FEX/pull/31) adopts the same per-library architecture for
Vulkan's 476 generated runtime signatures and three custom X11 publications. Focused ARM64 run
[`33293845677`](https://github.com/teamleaderleo/FEX/actions/runs/33293845677) proves physical wrapper
unload, retained ordinary and X11-sensitive calls, and a forced moved reload without a GPU or real
Vulkan driver. Read [Vulkan resident companion](VulkanResidentCompanion.md) for the exact boundary.

Read [`analysis.cpp`](../ThunkLibs/Generator/analysis.cpp),
[`gen.cpp`](../ThunkLibs/Generator/gen.cpp), and
[`GuestLibs/CMakeLists.txt`](../ThunkLibs/GuestLibs/CMakeLists.txt) before a library-specific guest
file. Generator output, build integration and product adoption are three separate layers.

### Adjacent bounded fixes

- [PR #6](https://github.com/teamleaderleo/FEX/pull/6), merge `5136f49839`, invalidates the replaced
  destination of a successful moved `MREMAP_FIXED` operation.
- [PR #25](https://github.com/teamleaderleo/FEX/pull/25), merge `3c0de317aa`, retains fixed-width
  guest character layout while accepting signed `int8_t*` host returns under Clang 21.

The whole-file code-cache configuration binding is separately explained in
[Whole-file code-cache identity](WholeFileCodeCacheIdentity.md). It namespaces cached host code by
the effective generated-code configuration, bitness and host features. The client transports a
bounded canonical app-specific snapshot and effective host features to the offline compiler; the
compiler reconstructs and independently checks the requested identity before compiling, and the
cache header stores that identity for the runtime consumer. Malformed, partial or mismatched
snapshots fail closed.

These are not callback-lifetime fixes. They are listed here because both can otherwise be mistaken
for a reason to widen a thunk/cache experiment.

## What remains a hypothesis or explicit non-goal

- Vulkan's own companion owns native-retained proc-address invokers and three X11 target/unpacker
  pairs. Real application callback mediation and guest-retained exported wrapper entrypoints remain
  separate work.
- The existing debug-report/debug-utils wrappers preserve the fork's current suppression/dummy
  policy; routing them safely is not the same as forwarding a real guest callback.
- Exact CustomIR retirement does not revoke an entry another thread already selected or entered.
- The GL and Vulkan companions are process-resident. Reclaiming either would require real
  accumulation evidence plus owner generations, execution leases/hazards and a grace period.
- Per-library bridge identity remains deliberate. Equal-looking C++ signatures are not proof that
  ABI annotations and lifetime policy can be globally deduplicated.
- Source inventories and x86-host builds do not establish 32-bit Vulkan runtime behavior.
- Direct-driver refusal is not whole guest-driver mediation.
- None of these owned-fork changes is an upstream-ready contribution merely because it is merged
  here.

## Pick the smallest proof

| Changed surface | First useful proof |
| --- | --- |
| tool/source/submodule readiness | `./Scripts/ResearchDevBuild.py doctor` |
| Vulkan custom-route names | `VulkanCustomRouteInventory.ThunkGen` through one `check` command |
| const/custom repack generation | `StructRepacking.ThunkGen` and the matching ownership inventory |
| exact synthetic cache retirement | focused `FEXCore_Tests_LookupCache`; ARM rebind only when runtime behavior changed |
| resident generator output | `ResidentBridgeGeneration.ThunkGen` |
| GL unload/reload lifetime | checked-in `gl-resident-companion-v1` ARM profile |
| Vulkan unload/reload lifetime | checked-in `vulkan-resident-companion-v1` ARM profile |
| fixed-remap destination semantics | exact `smc-2.64` ARM case |
| whole-file cache identity/snapshot | focused `FEXCore_Tests_CodeCacheConfig` check-set |
| block-cache blob/index layout | focused parser owner; use `FEXCore_Tests_DiskCacheIndexRecovery` only for file/lock/recovery behavior |
| CodeCacheConfig source-level debugging | checked-in **FEX: debug CodeCacheConfig owner** launch after its exact pre-launch task |

Use the command forms and evidence boundaries in [ResearchDevLoop.md](ResearchDevLoop.md). Reuse an
accepted exact-head result when source, toolchain, inputs and relevant environment are unchanged.
