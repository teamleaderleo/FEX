# Internal Vulkan proc-address experiment

This branch is an owned-fork Linux Fieldwork research surface. It is not an upstream contribution candidate.

Base source: `71afe476751deac24adabd1adb575fd2337b6e0a`.

The experiment to test is deliberately split into three independent changes:

1. Add the three missing internal custom-routing names to `LookupCustomVulkanFunction()`:
   - `vkCreateDebugReportCallbackEXT`
   - `vkDestroyDebugReportCallbackEXT`
   - `vkCreateDebugUtilsMessengerEXT`
2. In host `vkGetInstanceProcAddr` / `vkGetDeviceProcAddr`, perform native lookup first. Preserve a native null result. Only substitute a FEX custom implementation after native lookup succeeds.
3. In guest `vkGetInstanceProcAddr`, perform the packed host query before substituting the guest `vkGetDeviceProcAddr` entrypoint, and explicitly substitute the guest `vkGetInstanceProcAddr` entrypoint for successful self-queries.

The experiment should be accepted only if the owned Fieldwork test matrix demonstrates all of these at once:

- direct and dynamic debug-report calls use the required FEX mediation;
- debug-utils changes only when its routing is added;
- disabled debug extensions still resolve to null;
- null-instance `vkCreateDevice` and `vkGetDeviceProcAddr` queries resolve to null;
- a valid-instance GIPA self-query is non-null;
- ordinary global-command lookup remains non-null.

A deterministic fake native Vulkan provider remains the preferred follow-up for exact scope/availability control. A separate non-null allocation-callback fixture is required before treating the destroy-debug-report omission as independently demonstrated.

Any upstream implementation must be independently human-derived in accordance with FEX contribution policy.
