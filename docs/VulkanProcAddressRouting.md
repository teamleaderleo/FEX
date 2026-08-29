# Vulkan proc-address routing in the owned fork

This note explains the small routing repair merged from owned-fork PRs #1 and #2. It is an
internal research guide, not an upstream submission or a claim that every Vulkan callback lifetime
problem is solved.

## The short version

Vulkan programs can reach a command in two ways:

1. call an exported function such as `vkCreateDebugReportCallbackEXT` directly;
2. ask `vkGetInstanceProcAddr` (GIPA) or `vkGetDeviceProcAddr` (GDPA) for a function pointer and call
   that pointer.

FEX already had custom host wrappers for several commands whose arguments cannot safely cross the
x86-guest/ARM64-host boundary unchanged. Before PR #1, dynamic lookup did not route three existing
callback-sensitive wrappers:

- `vkCreateDebugReportCallbackEXT`;
- `vkDestroyDebugReportCallbackEXT`;
- `vkCreateDebugUtilsMessengerEXT`.

The direct and dynamic paths could therefore select different implementations for the same Vulkan
name. The repaired dynamic path first asks the native Vulkan loader whether the name is available
for the supplied instance or device. It returns null when native Vulkan returns null. Only after a
successful native lookup does FEX substitute a matching custom wrapper.

That order matters. The Vulkan specification gives GIPA and GDPA different command/scope tables:
an instance, device, enabled extension, and command level determine whether the result is a function
pointer or null. See the Khronos reference pages for
[`vkGetInstanceProcAddr`](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetInstanceProcAddr.html)
and
[`vkGetDeviceProcAddr`](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetDeviceProcAddr.html).
FEX must preserve that native availability decision; the existence of a custom C++ function is not
permission to manufacture a guest-visible Vulkan command.

## The call path

```text
x86 application
  -> ThunkLibs/libvulkan/Guest.cpp: guest GIPA/GDPA entrypoint
  -> generated packed call
  -> ThunkLibs/libvulkan/Host.cpp: host GIPA/GDPA implementation
       -> query native Vulkan GIPA/GDPA
       -> null: return null
       -> non-null: check LookupCustomVulkanFunction()
            -> custom name: return FEX custom host wrapper
            -> ordinary name: return native function pointer
  -> Guest.cpp
       -> approved GIPA/GDPA self-query: return the guest self-entrypoint
       -> other known command: link the host address to its generated guest caller
       -> unknown signature: return null under the current policy
```

The self-entrypoint cases are deliberately host-approved. For example, the guest does not return
its own `vkGetDeviceProcAddr` merely because the queried string matches; the packed native lookup
must first say that the name is valid for the supplied scope.

## Where the two inventories come from

`ThunkLibs/libvulkan/libvulkan_interface.cpp` is the generator-side declaration. A command tagged
with `fexgen::custom_host_impl` requires FEX-owned host behavior rather than an ordinary generated
passthrough.

`LookupCustomVulkanFunction()` in `ThunkLibs/libvulkan/Host.cpp` is the dynamic-lookup inventory.
It maps a Vulkan command name to the corresponding `fexfn_impl_libvulkan_*` function.

PR #2 added `unittests/ThunkLibs/vulkan_custom_route_inventory.py`. For both guest ABIs it selects
the applicable preprocessor branch and requires exact equality between the internal Vulkan
`custom_host_impl` names and the lookup names. The current counts are:

```text
x86-64: 12 generator declarations / 12 dynamic routes
x86-32: 21 generator declarations / 21 dynamic routes
```

On the pre-repair tree the same checker reports 12/9 and 21/18, naming the three missing callback
routes. This is a name-inventory invariant. It does not execute Vulkan, prove that each wrapper's
policy is correct, or prove that a callback remains valid after its owning wrapper unloads.

Run only this check with:

```sh
python3 unittests/ThunkLibs/vulkan_custom_route_inventory.py "$PWD"

ctest --test-dir PATH_TO_CONFIGURED_BUILD \
  --output-on-failure \
  -R '^VulkanCustomRouteInventory\.ThunkGen$'
```

For a composition build, the owned-fork focused lane can build only the affected host thunk:

```sh
./Scripts/ResearchDevBuild.py --lane vulkan build vulkan-host-64
```

That build is x86-host compilation and thunk-generation evidence, not x86-on-ARM Vulkan runtime
acceptance. The exact hosted ARM64 negative/positive receipts remain on owned-fork PR #1.

## What the callback wrappers currently mean

The three newly reachable functions are not a complete callback mediation architecture. The
existing debug-report and debug-utils creation wrappers replace the application callback with a
host-side dummy callback, matching the pre-existing direct-symbol behavior. The custom wrappers
also preserve the repository's current policy of passing a null allocation-callback pointer in
these paths.

The routing repair makes direct and dynamic lookup agree and avoids exposing a native entrypoint
that would call an x86 guest address as host code. It does not change the suppression policy into a
real guest-callback bridge.

## Separate problems that remain separate

- Application callback mediation needs an owned, lifetime-safe guest-to-host trampoline policy.
- A wrapper unload problem concerns executable helper ownership after an address has escaped; GIPA
  route selection alone cannot solve it.
- Future CustomIR rebinding needs the right reverse index; ordinary page-range invalidation can miss
  synthetic entries with no decoded guest page.
- Cache invalidation cannot revoke executable code another thread already selected.
- The x86-32 result above is a source inventory check, not a demonstrated 32-bit Vulkan runtime.
- Apple M5/Venus behavior was not replayed by this merge.

Treat each item as its own smallest experiment. Do not rerun the callback-routing matrix unless a
relevant product source, Vulkan/runtime input, toolchain, or named risk changes.
