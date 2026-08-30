# FEX thunk callback lifetime map

This is an owned-fork research map, not an upstream-ready design. It explains the callback failures
tracked in `teamleaderleo/linux-fieldwork` in terms of the current FEX code. The three failures share
a Vulkan/X11 symptom, but they occur at different boundaries and need different fixes.

For current merge/adoption status across these fixes, start with
[the owned-fork research map](OwnedForkResearchMap.md). This note retains the deeper causal history.

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

Owned-fork PR #1, merged as `77bebaf521`, adds those names to the custom lookup and checks whether
the native driver actually exposes the function before substituting FEX's wrapper. The second rule
matters: FEX must not make an unavailable extension appear available merely because it has a wrapper
symbol.

PR #2, merged as `582d925062`, adds a 64/32-bit static invariant: every generated
`custom_host_impl` declaration must be represented in the lookup inventory. That catches future
drift without running Vulkan.

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

### Current-base re-registration gap

Current base does not remove or retire an existing route when the same H is registered with a new G.
`AddThunkTrampolineIRHandler` logs that the CustomIR entry already exists and leaves both the handler
and any compiled lookup result unchanged. The unused private `RemoveCustomIREntrypoint` method is not
called by the Linux thunk lifetime path. It would not have been sufficient on its own: lookup-cache
range invalidation discovers compiled blocks through page-to-entrypoint reverse indexes in
[`LookupCache.h`](../FEXCore/Source/Interface/Core/LookupCache.h), while a synthetic CustomIR block can
have an empty `CodePages` list.

The result is a stale H → old-G redirect after H is re-registered for a different guest generation.
This is a **future dispatch** problem: a later lookup may reuse the old route.

### Chosen candidate: exact-identity replacement and retirement

The retained ARM64 fieldwork fixture demonstrated both directions on the older investigated source:

- registry replacement without exact cache retirement kept the old route and crashed;
- registry replacement plus exact shared/local retirement reached the new guest generation and exited
  successfully.

Those receipts came from run `31817790502` at source `1e8b042e`: the registry-only child received
`SIGSEGV`, while the exact-retirement child returned `1001035` and exited zero. They demonstrate the
cache mechanism, but they are not a current-head acceptance result.

Owned-fork PR #15, merged as `1ab30cadba`, therefore treats re-registration as one transaction.
Under the existing thread-creation boundary it takes the exclusive code-invalidation lock, replaces
only an identical thunk handler's H → G mapping, erases the exact shared H entry while delinking
inbound direct-block links, and clears every live thread's exact L1/L2 entry and call/return shadow
cache. First
registration and identical re-registration remain no-ops for invalidation. The lock order is
`ThreadCreationMutex` → `CodeInvalidationMutex` → `CustomIRMutex`.

An entrypoint-page reverse-index repair also made the retained fixture pass, but it broadens eviction
to host pages and can add duplicate reverse-index membership for synthetic entries. Exact identity is
the narrower match for “this H changed owner.” A focused unit test constructs the otherwise-invisible
empty-`CodePages` case: range invalidation cannot find it, while exact invalidation removes it.

This does not revoke a block another thread already selected before the transaction. Concurrent
in-flight execution and already-escaped host trampolines remain separate lifetime questions.

Two earlier hosted `ubuntu-24.04-arm` attempts at base `f1f1e6a` and candidate `93fe514dc` trapped at
the same unaligned-TSO environment boundary before the first linked host call; they remain invalid
A/B evidence. A later composed exact-head run `33276996655` first passed an ordinary x86 control,
moved the guest invoker while retaining the native H identity, and changed the post-registration
link from the stale failure to `rv=1001035`, exit zero. The current callback also exited zero. The
pre-registration link/callback still faulted, preserving the explicit non-goal for code selected
before registration. The merged source tree was byte-identical to the tested composition.

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
   The owned fork now applies and measures this design for GL only; it is not a Vulkan result.
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
