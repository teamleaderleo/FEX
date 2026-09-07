# Vulkan proc-address routing in the owned fork

This note explains the routing repair merged from owned-fork PRs #1 and #2. Source reading refreshed
against [`72872bac83`](https://github.com/teamleaderleo/FEX/tree/72872bac83ed714ff9f79143beb4d85926186c32)
on 2026-09-07. It is an internal research guide; upstream submission and broader callback-lifetime
acceptance remain separate decisions.

For the C++ close-up, start with [Reading FEX through its small fixes](FEXCodeReadingCompanion.md).
Use [Reading Vulkan contracts beside FEX](VulkanContractReading.md) for API tables, version
footnotes, callback lifetimes, and allocator rules. The [reading exercises](FEXReadingExercises.md)
provide source targets and answer checks. The existing [learning path](OwnedForkLearningPath.md)
continues to own the PR-history curriculum.

## The routing repair

Vulkan programs can reach a command in two ways:

1. call an exported function such as `vkCreateDebugReportCallbackEXT` directly;
2. ask `vkGetInstanceProcAddr` (GIPA) or `vkGetDeviceProcAddr` (GDPA) for a function pointer and call
   that pointer.

FEX already had custom host wrappers for several commands whose arguments require mediation across
the x86-guest/ARM64-host boundary. Before PR #1, dynamic lookup omitted three existing
callback-sensitive wrappers:

- `vkCreateDebugReportCallbackEXT`;
- `vkDestroyDebugReportCallbackEXT`;
- `vkCreateDebugUtilsMessengerEXT`.

The direct and dynamic paths could therefore select different implementations for the same Vulkan
name. The repaired dynamic path first asks the native Vulkan loader whether the name is available
for the supplied instance or device. It returns null when native Vulkan returns null. After a
successful native lookup, FEX substitutes a matching custom wrapper.

The Vulkan specification gives GIPA and GDPA different command/scope tables. Read the Khronos pages
for [`vkGetInstanceProcAddr`](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetInstanceProcAddr.html)
and [`vkGetDeviceProcAddr`](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetDeviceProcAddr.html),
including the footnotes. They distinguish specified null results, valid pointers, and undefined
input cases. GIPA self-resolution with a null instance has a Vulkan 1.2 qualification. GDPA also
has requested-core-version and `maintenance5` conditions under which a non-null result alone is
insufficient permission to call a command. The [contract reader](VulkanContractReading.md) turns
these details into specific reading questions.

Native lookup success is a prerequisite to custom substitution. FEX adapter coverage and the
application's API usage conditions remain separate requirements.

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
       -> approved extension-enumeration query: preserve the guest filtering entrypoint
       -> other known command: link the host address to its generated guest caller
       -> unknown signature: return null under the current default policy
```

The guest special cases are host-approved. The packed lookup runs before the guest returns its
own entrypoint. Current guest GIPA also preserves `vkEnumerateInstanceExtensionProperties`, whose
guest implementation filters the unsupported direct-driver-loading extension. Read that behavior
in [`Guest.cpp`](../ThunkLibs/libvulkan/Guest.cpp); its API context is covered in the
[direct-driver section](VulkanContractReading.md#6-direct-driver-loading-exposes-another-callback-boundary).

## Where the two inventories come from

`ThunkLibs/libvulkan/libvulkan_interface.cpp` is the generator-side declaration. A command tagged
with `fexgen::custom_host_impl` requires FEX-owned host behavior.

`LookupCustomVulkanFunction()` in `ThunkLibs/libvulkan/Host.cpp` is the dynamic-lookup inventory.
It maps a Vulkan command name to the corresponding `fexfn_impl_libvulkan_*` function.

PR #2 added `unittests/ThunkLibs/vulkan_custom_route_inventory.py`. For both guest ABIs it selects
the applicable preprocessor branch and requires exact equality between the internal Vulkan
`custom_host_impl` names and the lookup names. The retained repaired inventories are:

```text
x86-64: 12 generator declarations / 12 dynamic routes
x86-32: 21 generator declarations / 21 dynamic routes
```

On the pre-repair tree the same checker reports 12/9 and 21/18, naming the three missing callback
routes. This is a name-inventory invariant. Wrapper semantics, actual Vulkan execution, and escaped
executable lifetime each require additional evidence. This documentation revision performed no
new inventory or runtime execution.

Run the source inventory or the exact registered check, as appropriate:

```sh
python3 unittests/ThunkLibs/vulkan_custom_route_inventory.py "$PWD"

./Scripts/ResearchDevBuild.py --lane vulkan check \
  thunkgentest VulkanCustomRouteInventory.ThunkGen
```

Before local C++ work, follow [ResearchDevLoop.md](ResearchDevLoop.md), beginning with the read-only
`doctor` preflight. For a composition question, build the affected host thunk:

```sh
./Scripts/ResearchDevBuild.py --lane vulkan build vulkan-host-64
```

That build supplies x86-host compilation and thunk-generation evidence. Actual x86-on-ARM Vulkan
runtime acceptance is a different result. The historical hosted ARM64 negative/positive receipts
remain on [owned-fork PR #1](https://github.com/teamleaderleo/FEX/pull/1).

## What the callback wrappers currently mean

The existing debug-report and debug-utils creation wrappers replace the application callback with a
host-side dummy, matching the pre-existing direct-symbol behavior. They also preserve the existing
policy of passing a null allocation-callback pointer in these paths. Read the assignments and native
calls in [`Host.cpp`](../ThunkLibs/libvulkan/Host.cpp).

The routing repair makes these direct and dynamic paths agree. Forwarding actual application
callbacks would require additional marshalling, retained ownership, and destruction handling. Use
the [contract reader](VulkanContractReading.md#3-debug-callback-routing-and-callback-delivery-have-different-outcomes)
to compare the current suppression policy with the Vulkan callback API.

## Follow-on questions by owner

- Application callback delivery: guest/host marshalling, callback and user-data lifetime, and paired destruction.
- Wrapper unload: executable ownership after an address has escaped, covered by the resident-companion guides.
- Future dispatch after re-registration: exact CustomIR rebinding and cache retirement, including synthetic entries with empty `CodePages` membership.
- Already-selected code: a separate lifetime requirement beyond changes to future lookup.
- Guest width and hardware: the x86-32 inventory is source evidence; Apple M5/Venus behavior and 32-bit runtime execution need their own records.

Choose an experiment only when the current question needs execution. The [exercise sheet](FEXReadingExercises.md)
starts with read-only explanations and then identifies the existing focused checks.
