# FEX reading exercises: explain one mechanism at a time

For owned-fork source [`72872bac83`](https://github.com/teamleaderleo/FEX/tree/72872bac83ed714ff9f79143beb4d85926186c32), inspected 2026-09-07. Begin with [Reading FEX through its small fixes](FEXCodeReadingCompanion.md); keep [Reading Vulkan contracts beside FEX](VulkanContractReading.md) available for the API questions.

These are reading sessions you can choose independently. The first four form a useful starting sequence. Each has something concrete to produce and an answer check. A session is complete when you can explain the mechanism in your own words and point to the source that supports it.

## 1. Explain the six-line repair

**Open:** the original [routing commit](https://github.com/teamleaderleo/FEX/commit/28a3a5bfbd31662bfc4bd316ada39037aebf4165), then current [`libvulkan/Host.cpp`](../ThunkLibs/libvulkan/Host.cpp).

**Find:** `LookupCustomVulkanFunction`, `FEXFN_IMPL`, `LDR_PTR`, and the custom debug-report creation function.

Write four sentences: what string enters the lookup; what function address it selects; what that function changes in its host-side copy; and what native function it finally calls. Explain the `sv` suffix and the two macros aloud. Language details are in the [worked companion](FEXCodeReadingCompanion.md#1-read-the-little-routing-branch-literally).

<details>
<summary>Answer check</summary>

The new branch recognizes `vkCreateDebugReportCallbackEXT` and returns the existing custom host implementation. That implementation forms a host representation, substitutes `DummyVkDebugReportCallback`, refreshes its native loader pointer, and calls native Vulkan with the local copy and a null allocator. The lookup selects a function; the selected function performs callback creation later. The initial commit adds three name routes. The remainder of PR #1 handles separate lookup semantics.

</details>

## 2. Explain why branch order is observable

**Open:** the host and guest `vkGetInstanceProcAddr` implementations in [`Host.cpp`](../ThunkLibs/libvulkan/Host.cpp) and [`Guest.cpp`](../ThunkLibs/libvulkan/Guest.cpp), plus the [contract reader's lookup table](VulkanContractReading.md#1-proc-address-lookup-is-a-contract-table).

On paper, trace a null-instance query for `vkCreateDebugReportCallbackEXT`. Then trace GIPA querying itself with a valid instance. Mark every return and distinguish a native result from a guest entrypoint. Keep these as source-reading cases; this exercise runs no Vulkan calls.

<details>
<summary>Answer check</summary>

The first query preserves the native null result before custom lookup can manufacture a pointer. The valid-instance self-query reaches the guest self-entrypoint only after host lookup succeeds. Reordering those checks changes observable availability. Use Khronos's table and its preconditions to choose expected results; invalid handles occupy a separate undefined category. For null-instance GIPA self-resolution, retain the Vulkan 1.2 qualification.

</details>

## 3. Connect an annotation to generated behavior

**Open:** [`libvulkan_interface.cpp`](../ThunkLibs/libvulkan/libvulkan_interface.cpp), [`gen.cpp`](../ThunkLibs/Generator/gen.cpp), and [`vulkan_custom_route_inventory.py`](../unittests/ThunkLibs/vulkan_custom_route_inventory.py).

Find one `fex_gen_config` specialization with `custom_host_impl`. In the generator, locate `function_to_call`. In the inventory checker, find the two name sets it compares. Draw three boxes: interface declaration, generated custom call, dynamic lookup table.

Explain what a passing inventory check establishes and choose one behavior that needs another kind of test.

<details>
<summary>Answer check</summary>

`custom_host_impl` directs generated calls toward `fexfn_impl_...`. The runtime lookup table separately resolves names to those functions. The checker protects agreement between applicable declaration and route inventories for each guest width. Executing the selected wrapper, delivering an application callback, and surviving wrapper unload each require additional evidence. The exact test's source is the authority for its coverage.

</details>

## 4. Follow the const object's complete lifetime

**Open:** `repack_wrapper` in [`common/Host.h`](../ThunkLibs/include/common/Host.h), the `make_repack_wrapper` emission in [`gen.cpp`](../ThunkLibs/Generator/gen.cpp), and [ThunkRepacking.md](ThunkRepacking.md).

For `T = const A*` and `T = A*`, write the resulting `PointeeT`, the original guest access policy, and the selected destructor branch. Mark which object holds the temporary host data. Find the distinction between host-only cleanup and mutable exit/copyback.

<details>
<summary>Answer check</summary>

Both cases use mutable `A` for private host-side storage. The original `T` retains the distinction: const pointees select `fex_apply_custom_repacking_cleanup`; mutable pointees select `fex_apply_custom_repacking_exit` and eligible automatic copyback. Apply the header's outer conditions too. Entry-side temporary allocations still need release for const inputs. PR #5 preserves the qualifier during generation; PR #14 separates the cleanup hook from the exit hook.

</details>

## 5. Explain the two-pass chain copy

**Open:** `CopyInstanceCreatePNextChain` and `vkCreateInstance` in [`Host.cpp`](../ThunkLibs/libvulkan/Host.cpp).

Find the vector growth loop, the later link loop, and the native call. Draw the original chain and copied chain on separate lines. Label a copied node, a borrowed nested pointer, the local vector owner, and a dummy callback target. Identify the guest-width preprocessor branch.

<details>
<summary>Answer check</summary>

The x86-64 helper first finishes copying supported nodes, then links their final vector addresses. Vector growth can invalidate earlier element pointers. `pnext_copies` and the copied root stay alive across the native call. Ordinary node copying retains nested pointer values, so assess those borrowed values using their individual API lifetimes. The 32-bit path is separate. The [contract reader](VulkanContractReading.md#4-read-pnext-through-both-its-type-and-its-lifetime) explains why a temporary input node and a retained callback target require separate lifetime notes.

</details>

## 6. Classify three problems that involve function pointers

**Open:** the [research map](OwnedForkResearchMap.md), then [`libvulkan_bridge/Guest.cpp`](../ThunkLibs/libvulkan_bridge/Guest.cpp).

Assign each situation to a mechanism: a command name selects the wrong implementation; a re-registered native address still dispatches through an old cached mapping; a retained executable address belongs to a wrapper that has unloaded. Then find one X11 forwarding target and its unpacker in the companion.

<details>
<summary>Answer check</summary>

The mechanisms are custom route selection, exact CustomIR rebinding/cache retirement, and executable lifetime ownership. A future lookup repair leaves already-held executable addresses with their own lifetime requirement. In the companion source, an X11 forwarding target and its `CallbackUnpack` entrypoint share the companion's lifetime. Follow the existing research map for the complete cross-file implementation and evidence limits.

</details>

## 7. Read a cache fix as a writer/reader agreement

**Open:** [BlockDiskCache.md](BlockDiskCache.md) and [PR #51](https://github.com/teamleaderleo/FEX/pull/51). Follow their links to the current writer and parser.

Make two inventories: bytes the writer stores and bytes the reader consumes. Mark where `GuestSize` and `GuestHash` are used. Explain why removing a redundant payload tail can preserve validation of current guest code. Keep the PR's historical byte measurements separate from your current-source reading.

<details>
<summary>Answer check</summary>

The removed tail held original guest-code bytes that lookup did not consume. The reader's current-guest validation uses the retained size/hash fields and current guest mapping. Older tailed blobs remain readable under the documented parser rule. The change reduces each new payload by its omitted guest-code length. A cache-population saving or application-speed result requires measurements beyond that byte accounting.

</details>

## 8. Choose one modest research question

Pick a question whose first answer is available through reading:

| Question | First thing to inspect | A useful stopping point |
| --- | --- | --- |
| Could custom route coverage be generated from the interface inventory? | Current generator metadata, manual table, and checker exclusions. | A design note that preserves availability order and special entrypoint handling. |
| Which borrowed fields survive a `pNext` copy? | One supported node type, its host copy, and its Khronos contract. | An ownership/lifetime table for that node. |
| Where are native lookup results stored by instance or device? | `DoSetupWithInstance`, custom wrappers, and existing multi-instance/device comments. | A source map and a clearly labeled question; a comment alone proves no new defect. |
| What exactly changes a whole-file cache identity? | The implementation and tests linked from [WholeFileCodeCacheIdentity.md](WholeFileCodeCacheIdentity.md). | An input-to-identity table with the corresponding test cases. |

Keep full callback forwarding, bridge reclamation, and broad 32-bit runtime support as larger follow-on topics. Start by answering one ownership or API question precisely. A new issue or implementation is a later decision.

## Optional execution after reading

For code browsing, the browser and your editor are enough. To compile or run a focused check, first use the existing [development guide](ResearchDevLoop.md) and inspect readiness from the repository root:

```sh
./Scripts/ResearchDevBuild.py doctor
```

Resolve its reported prerequisites through that guide before choosing one relevant command. The following are alternatives, each with its own scope:

```sh
# Source inventory only; uses the existing checker.
python3 unittests/ThunkLibs/vulkan_custom_route_inventory.py "$PWD"

# Build the owning test executable and run one exact registered check.
./Scripts/ResearchDevBuild.py --lane vulkan check \
  thunkgentest VulkanCustomRouteInventory.ThunkGen

# A different question: generated repacking behavior.
./Scripts/ResearchDevBuild.py --lane repack check \
  thunkgentest StructRepacking.ThunkGen
```

These commands are taken from the repository's existing guides; this documentation pass did not run them. A source-inventory result, a compiler result, and x86 guest execution through FEX on ARM answer different questions. Record which one you actually obtained. For cleanup, retain the development guide's linked-worktree rule: avoid `git submodule deinit`, which changes shared registration.

## Keep a small notebook

```text
Question:
Source revision and selected ABI branch:
Files and symbols read:
API rule, where relevant:
My explanation of the behavior:
Owner of each borrowed or retained value:
What the existing test proves:
Next uncertainty, if any:
```

Five clear sentences supported by source are a useful result. You can finish a reading session with understanding, even when the code needs no change.
