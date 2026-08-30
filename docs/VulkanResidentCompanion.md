# Vulkan resident thunk companion

This owned-fork integration gives Vulkan executable adapters retained by native state a matching
process-lived owner. It deliberately leaves the ordinary guest `libvulkan` wrapper unloadable.

## Why Vulkan needs two objects

An x86 Vulkan program asks `vkGetInstanceProcAddr` or `vkGetDeviceProcAddr` for a command. The
native loader returns a host function address, but x86 code cannot call that address directly.
FEX associates it with an x86-callable invoker:

```text
native Vulkan function H
  -> guest GIPA/GDPA
  -> generated typed resident accessor
  -> Vulkan companion invoker T
  -> LinkAddressToFunction(H, T)
```

Vulkan's interface currently contains 714 proc-address symbols with 476 unique runtime
signatures. The ordinary generated include still defines the wrapper's direct entrypoints, but
`HostPtrInvokers` publishes only the companion-owned invokers.

Vulkan also gives its native host thunk three guest-callable X11 helpers. Their target functions
and callback unpackers now live together in `ThunkLibs/libvulkan_bridge/Guest.cpp`. The companion
has an explicit `libX11.so.6` dependency, so it does not rely on a leaked `dlopen` handle to keep
the called implementation alive.

## Build ownership

`ThunkLibs/GuestLibs/CMakeLists.txt` enables `RESIDENT_BRIDGE` for Vulkan and creates
`libfex-vulkan-bridge.so`. The ordinary wrapper needs the companion but has no ELF `NODELETE` flag.
Only the companion is `NODELETE`.

The generated ABI uses one indexed invoker lookup rather than importing one function per
signature. Typed template specializations still prove each signature-to-index relationship at
compile time. Current Vulkan analysis finds no generator-recognized host-to-guest callback
parameters; the three X11 unpackers are explicit custom publications.

## Small local checks

Run the source ownership invariant directly:

```sh
python3 unittests/ThunkLibs/vulkan_resident_bridge_inventory.py "$PWD"
```

Or run its one registered CTest after configuring a focused lane:

```sh
ctest --test-dir /path/to/focused/build --output-on-failure \
  -R '^VulkanResidentBridgeInventory[.]ThunkGen$' --no-tests=error
```

The ordinary wrapper and companion objects have separate focused targets:

```sh
./Scripts/ResearchDevBuild.py --lane vulkan-resident build vulkan-guest
./Scripts/ResearchDevBuild.py --lane vulkan-resident build vulkan_bridge-guest
```

That development profile compiles guest code as host-toolchain object targets. To inspect the real
shared-object contract without building FEX, configure `ThunkLibs/GuestLibs` standalone and build
only `vulkan-guest` and `vulkan_bridge-guest`, then inspect both with `readelf -dW` and
`readelf -rW`.

## Cross-ISA decision profile

`Scripts/ResearchProfiles/vulkan-resident-companion-v1` is the bounded ARM64 runtime oracle. It
uses a tiny native Vulkan stub, so it requires no GPU or real Vulkan driver. After an ordinary x86
control, it proves:

- the ordinary guest Vulkan wrapper physically unmaps after `dlclose`;
- the companion and its X11 dependency remain mapped;
- a retained ordinary proc-address call still executes;
- a retained Xlib presentation call traverses companion-owned `XSync` and `XDisplayString`
  unpackers after close;
- reserving every old wrapper range forces a moved reload; and
- both the reloaded and originally retained ordinary proc address still execute.

The profile goes through the reusable ARM carrier in `docs/OwnedForkCI.md`. A local x86 compile is
not a substitute for that runtime receipt.

## Boundaries and non-goals

This integration owns executable adapters that the native thunk side may retain. It does not
change the contract for guest code that manually keeps exported wrapper entrypoints such as
`vkGetInstanceProcAddr` after unloading the library.

It also does not forward Vulkan debug callbacks, implement `VkAllocationCallbacks`, enable direct
guest-driver loading, reclaim the companion, define stale-handle policy, deduplicate signatures
with GL, or establish 32-bit Vulkan behavior. Those have different owners and require different
oracles.
