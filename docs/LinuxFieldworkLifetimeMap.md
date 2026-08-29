# FEX thunk callback lifetime map

This is an owned-fork research map, not an upstream-ready design. It explains the callback failures
tracked in `teamleaderleo/linux-fieldwork` in terms of the current FEX code. The three failures share
a Vulkan/X11 symptom, but they occur at different boundaries and need different fixes.

## The minimum mental model

- **Guest** means the x86 application and its x86 code addresses.
- **Host** means the native library and code running on the ARM64 machine.
- A **thunk** translates an API call between those worlds.
- A **callback** travels in the reverse direction: a native host library later calls code associated
  with the guest.
- Vulkan's `vkGet*ProcAddr` APIs add another indirection: they return a function pointer that the
  application may save and call later.

There are therefore two different pointer bridges:

1. **native host function H → guest-side invoker G** lets x86 guest code call a function pointer
   returned by a native Vulkan driver;
2. **host trampoline T → guest callback/unpacker** lets a native library call back into guest code.

Invalidating bridge 1 does not make bridge 2 safe, and keeping bridge 2 resident does not repair a
wrong function selected for bridge 1.

## Failure A: the wrong Vulkan function pointer is selected

### Current-base path

The generated Vulkan interface declares callback-sensitive functions as custom implementations.
The host implementations replace guest callback fields with host-safe callbacks; for example,
[`Host.cpp`](../ThunkLibs/libvulkan/Host.cpp) wraps debug-report and debug-utils creation.

The base branch's `LookupCustomVulkanFunction`, however, does not list three of those custom
implementations:

- `vkCreateDebugReportCallbackEXT`
- `vkDestroyDebugReportCallbackEXT`
- `vkCreateDebugUtilsMessengerEXT`

That creates two answers for the same API:

- a direct exported call reaches FEX's wrapper;
- `vkGetInstanceProcAddr` can return the native driver's function instead.

If guest code calls that native callback-taking function, an x86 callback pointer can cross directly
into ARM64 code. Cache invalidation is irrelevant because the wrong route was chosen before a safe
bridge existed.

### Bounded fork fix

Draft fork PR #1 adds those names to the custom lookup and checks whether the native driver actually
exposes the function before substituting FEX's wrapper. The second rule matters: FEX must not make an
unavailable extension appear available merely because it has a wrapper symbol.

Draft PR #2 adds a 64/32-bit static invariant: every generated `custom_host_impl` declaration must be
represented in the lookup inventory. That catches future drift without running Vulkan.

## Failure B: a removed H → G route can remain compiled

### Registration and compilation

Guest Vulkan code calls `MakeGuestCallable` in
[`Guest.cpp`](../ThunkLibs/libvulkan/Guest.cpp). It associates a native function address **H** with a
guest invoker through `LinkAddressToFunction`.

LinuxEmulation receives that request in
[`Thunks.cpp`](../Source/Tools/LinuxEmulation/Thunks.cpp) and calls
`AddThunkTrampolineIRHandler(H, G)`. Core then creates a synthetic CustomIR entrypoint at H in
[`Core.cpp`](../FEXCore/Source/Interface/Core/Core.cpp). The generated block stores H in the implicit
register and exits to G.

This is not ordinary decoded guest code. During compilation, ordinary blocks contribute their guest
code pages to the lookup cache. A synthetic CustomIR block can have an empty `CodePages` list.

### Removal gap

`RemoveCustomIREntrypoint` erases the handler and calls `InvalidateGuestCodeRange(H, 1)`. The lookup
cache's range invalidation discovers compiled blocks through its page-to-entrypoint reverse indexes in
[`LookupCache.h`](../FEXCore/Source/Interface/Core/LookupCache.h). If H's synthetic block was stored
without a code page, range invalidation has no reverse-index entry with which to find it.

The result can be a stale compiled/cached H → old-G redirect even though the CustomIR handler is gone.
This is a **future dispatch** problem: a later lookup may reuse the old route.

### Candidate repairs demonstrated by fieldwork

Two bounded repair shapes remain plausible:

1. retire the exact shared/local lookup entry for H as part of removing its handler;
2. when a synthetic block has no decoded code pages, index its entrypoint page so the existing range
   invalidation path can find it.

The second is the smaller architectural repair because it restores the invariant expected by the
existing reverse index. It still needs an exact ARM64 test proving that register → compile → remove →
call cannot reach the retired guest invoker, including cached and concurrent variants.

## Failure C: a host trampoline already escaped into native state

Vulkan guest initialization publishes X11 targets and wrapper-local `CallbackUnpack` addresses in
[`Guest.cpp`](../ThunkLibs/libvulkan/Guest.cpp). `AllocateHostTrampolineForGuestFunction` in
[`Guest.h`](../ThunkLibs/include/common/Guest.h) passes the guest unpacker and target to
`MakeHostTrampolineForGuestFunction` in
[`Thunks.cpp`](../Source/Tools/LinuxEmulation/Thunks.cpp).

FEX allocates executable host trampoline T and embeds both guest addresses in its instance data. A
native manager may retain T after the ordinary guest thunk DSO unloads. T itself can still be mapped
while its embedded `GuestUnpacker` or `GuestTarget` now points at unmapped/reused code.

This is an **already-escaped executable capability** problem. Removing H from a lookup cache cannot
revoke T from native code, and a different thread may already have selected or entered it.

### Containment choices

Fieldwork evidence currently ranks the choices as follows:

1. **Keep the shared wrapper DSO resident (`DF_1_NODELETE`)** when unload is not a product
   requirement. This is the smallest containment, with a measured page-rounded mapped-size cost
   around 1.76 MiB for the investigated wrapper, not an RSS guarantee. A base-namespace-only version
   was falsified under `dlmopen`/new namespaces.
2. **Move escaped bridge code into a process-resident per-library sidecar** while allowing the
   ordinary wrapper to unload. This gives the persistent native owner a persistent bridge owner.
   Per-library identity is safer than global signature deduplication because ABI annotations are part
   of the contract even when C++ signatures look alike.
3. **Add owner generations plus execution leases/hazards and a grace period** only if the bridge code
   itself must eventually be reclaimed. This is the most general and most complex option.

A cache-only patch must not be presented as fixing this lifetime. It can fix Failure B while Failure
C remains.

## Decision gates

| Question | Evidence needed | Decision |
| --- | --- | --- |
| Are all callback-sensitive proc-address routes wrapped? | generated-interface versus lookup invariant, both thunk widths | merge the bounded routing fix only when exhaustive |
| Can a removed synthetic H route execute again? | exact ARM64 register/compile/remove/call test, cached and concurrent | choose exact retirement or reverse-index repair |
| Must ordinary callback wrapper DSOs unload? | product requirement plus namespace unload reproducer | no: use resident containment; yes: use a resident sidecar |
| Must escaped bridges themselves be reclaimed? | measured accumulation and a real lifecycle boundary | no: keep sidecar resident; yes: design leases/grace period |

Do not use a broad FEX build as the first oracle for these questions. Static inventory, one named
thunk target, and focused lifecycle reproducers answer them more directly; ARM64 runtime acceptance
belongs on an exact, receipt-producing execution lane.
