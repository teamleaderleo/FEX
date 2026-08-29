# Custom thunk repacking: ownership and const safety

This is an internal guide to FEX's custom struct-repacking lifecycle. It describes the owned-fork
contract and the focused checks that protect it; it is not a claim that 32-bit Vulkan is currently
enabled or runtime-complete.

## Why there are three hooks

A thunk sometimes cannot convert a guest struct member with the generated field-by-field rules.
Annotating one of its members with `fexgen::custom_repack` gives the owning struct three hooks:

| hook | direction | job |
| --- | --- | --- |
| `fex_custom_repack_entry` | guest to host | build any host-side mirrors and allocate temporary storage |
| `fex_custom_repack_exit` | host to guest | copy mutable results back and release entry-side storage |
| `fex_custom_repack_cleanup` | host only | release entry-side storage for a const pointee without writing guest memory |

The split between exit and cleanup is deliberate. `repack_wrapper<T>` in
`ThunkLibs/include/common/Host.h` knows whether `T` points to const data. Its destructor calls exit
for mutable pointees and cleanup for const pointees. A cleanup implementation must never recover a
guest pointer or copy host fields back to the guest merely to free host storage.

The generator emits a no-op cleanup adapter when a struct has no custom-repacked members. When it
does have custom members, the library that owns its entry and exit hooks must also define cleanup.
The current hand-written implementations live in:

- `ThunkLibs/libvulkan/Host.cpp`;
- `ThunkLibs/libwayland-client/Host.cpp`;
- `ThunkLibs/libfex_thunk_test/Host.cpp`.

## Vulkan `pNext` ownership

On an x86-32 guest, a Vulkan `pNext` chain cannot be borrowed as if it had the host layout.
`default_fex_custom_repack_entry` allocates an aligned host node for each guest node and follows the
chain through `next_handlers`.

The matching mutable hook traverses the guest and host chains, invokes the current reverse handler,
and frees those host nodes. The const path walks only the host chain and frees it; it does not touch
the guest chain. Non-default Vulkan repackers must additionally release their own arrays or nested
structs. Three direct array wrappers use the same host-only cleanup rule after the native Vulkan
call because their parameters are const.

The non-default Vulkan exits still return `false`, which asks `repack_wrapper` to perform the
repository's existing automatic repacking of non-custom fields afterward. This change proves that
the entry-side allocations have matching mutable and const owners. It does not claim that the
historical final guest values of every custom pointer field are correct, and 32-bit Vulkan runtime
forwarding remains disabled.

At the time of this note, the source inventory finds 18 non-default Vulkan entry owners that call
the generic `pNext` entry helper. Every one must call the chain-only mutable reverse helper from its
exit and the host-only chain cleanup helper from its cleanup function. The inventory test fails
closed if an owner is added without either side.

## The smallest checks

The source invariant and its negative control are cheap:

```sh
python3 unittests/ThunkLibs/vulkan_repack_cleanup_inventory.py "$PWD"
```

The negative control removes the `VkInstanceCreateInfo` cleanup call in memory and requires the
checker to identify exactly that missing owner. It does not modify the worktree.

Run the four focused generator/inventory checks in an already configured development lane:

```sh
ctest --test-dir PATH_TO_CONFIGURED_BUILD \
  --output-on-failure \
  -R '^(StructRepacking|VulkanCustomRouteInventory|VulkanInstancePNextCopyInventory|VulkanRepackCleanupInventory)\.ThunkGen$'
```

Compile only the affected composition surfaces locally:

```sh
./Scripts/ResearchDevBuild.py --lane repack build vulkan-host-32
./Scripts/ResearchDevBuild.py --lane repack build vulkan-host-64

./Scripts/ResearchDevBuild.py --profile linux-tests --lane repack-test build fex_thunk_test-host-32
./Scripts/ResearchDevBuild.py --profile linux-tests --lane repack-test build fex_thunk_test-host-64
./Scripts/ResearchDevBuild.py --profile linux-tests --lane repack-test linux-test-build thunk_testlib
```

The `thunk_testlib` assisted-repacking case is the causal runtime oracle. For a const pointer it
requires one allocation, one deallocation, one cleanup, zero exits, and no change to the guest
fields. For a mutable pointer it requires one allocation, one deallocation, one exit, zero
cleanups, and the expected copyback. Building that guest binary on x86 does not execute it through
FEX; actual thunk runtime evidence requires the one exact test on ARM64.

## Review questions

For any new custom repacker, answer these before widening test scope:

1. Which exact entry allocation or borrowed host resource needs a matching owner?
2. Does the argument type permit host-to-guest copyback?
3. Can cleanup be expressed using host state alone?
4. Does a nested array element have its own custom cleanup?
5. What bounded negative control proves the ownership inventory would catch an omitted hook?

Do not use a full FEX or Vulkan matrix as the first answer to these source-lifecycle questions.
