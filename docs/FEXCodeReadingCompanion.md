# Reading FEX through its small fixes

Source checkpoint: owned-fork commit [`72872bac83`](https://github.com/teamleaderleo/FEX/tree/72872bac83ed714ff9f79143beb4d85926186c32), inspected 2026-09-07. This is a learning companion to the [PR learning path](OwnedForkLearningPath.md). The [research map](OwnedForkResearchMap.md) remains the guide to symptoms, merged mechanisms, and open questions.

Start here for a close reading of the C++. Keep the [Vulkan contract reader](VulkanContractReading.md) beside the source, and use the [reading exercises](FEXReadingExercises.md) to check what you can explain yourself. Relative source links follow the branch you are viewing; the checkpoint above fixes the implementation discussed here.

## Begin with something you can hold in your head

The original routing commit, [`28a3a5bfbd`](https://github.com/teamleaderleo/FEX/commit/28a3a5bfbd31662bfc4bd316ada39037aebf4165), added six lines to `LookupCustomVulkanFunction`. Three existing functions gained entries in a name-to-implementation table. The complete [PR #1](https://github.com/teamleaderleo/FEX/pull/1) also repaired availability and self-lookup behavior. These are useful separate reading units.

Your first goal is to explain one lookup from its input string to the returned address. Templates, code generation, memory ownership, and executable lifetime become relevant at specific points along that route. Learn each concept when the route reaches it.

### The five files to keep open

| File | Its job in this reading |
| --- | --- |
| [`libvulkan_interface.cpp`](../ThunkLibs/libvulkan/libvulkan_interface.cpp) | Declares which Vulkan commands need custom guest or host handling. |
| [`libvulkan/Guest.cpp`](../ThunkLibs/libvulkan/Guest.cpp) | Implements guest-visible lookup and associates returned addresses with guest invokers. |
| [`libvulkan/Host.cpp`](../ThunkLibs/libvulkan/Host.cpp) | Queries native Vulkan, selects custom implementations, and prepares host-side arguments. |
| [`Generator/gen.cpp`](../ThunkLibs/Generator/gen.cpp) | Writes generated C++ from the analyzed interface. |
| [`common/Host.h`](../ThunkLibs/include/common/Host.h) | Defines guest/host representations and the temporary repacking object's lifetime. |

A generated `.inl` include is an output of the generator. When an identifier seems to have appeared from nowhere, follow the naming convention back to `gen.cpp`. You can read the generator's emission statements before configuring a build.

## 1. Read the little routing branch literally

In `Host.cpp`, find `LookupCustomVulkanFunction` and this branch:

```cpp
} else if (a_1 == "vkCreateDebugReportCallbackEXT"sv) {
  return (PFN_vkVoidFunction)fexfn_impl_libvulkan_vkCreateDebugReportCallbackEXT;
```

The parameter `a_1` is the command name. The `sv` suffix creates a `std::string_view`, and the comparison uses string contents. The final identifier names an existing function. Returning its address selects the implementation; the callback-creation operation happens when the application later calls it. `PFN_vkVoidFunction` is Vulkan's generic function-pointer result type. A caller uses the actual command type before invocation. See [C++ string-view comparison](https://eel.is/c++draft/string.view.comparison) and [Khronos's pointer-type definition](https://docs.vulkan.org/refpages/latest/refpages/source/PFN_vkVoidFunction.html).

Two macros in the same file shorten the spelling:

```cpp
#define LDR_PTR(fn) fexldr_ptr_libvulkan_##fn
#define FEXFN_IMPL(fn) fexfn_impl_libvulkan_##fn
```

`##` joins preprocessing tokens. Thus `FEXFN_IMPL(vkCreateInstance)` names `fexfn_impl_libvulkan_vkCreateInstance`. `LDR_PTR(vkCreateInstance)` names the stored native function pointer. Translate these names on paper once; the surrounding code becomes ordinary function calls and assignments. See [C++ token concatenation](https://eel.is/c++draft/cpp.concat).

**Checkpoint:** explain the difference between selecting the custom function and calling the native function inside that custom implementation.

## 2. Follow lookup out and back

Start at guest `vkGetInstanceProcAddr`, then follow the packed call to the host implementation of the same command. The central host decision is:

```text
native lookup returns null -> return null
native lookup succeeds    -> use a custom wrapper when the name has one
                           -> otherwise return the native result
```

In current `Host.cpp`, GIPA also performs instance setup and populates selected extension loader slots. Read the entire function after understanding this central decision. The existing [routing guide](VulkanProcAddressRouting.md) describes those details.

Back in guest `Guest.cpp`, a successful host result takes one of two routes. Special names return guest entrypoints, including GIPA, GDPA, and the guest-filtered extension enumeration function. Other names go through `MakeGuestCallable`.

Inside `MakeGuestCallable`, follow three statements: find the name in `HostPtrInvokers`; obtain its guest invoker address; call `LinkAddressToFunction` with the returned address and that invoker. The function then returns `func`. FEX's association gives later execution of that address a guest-callable route. Reading the cast alone would miss this essential step.

The current default for an unknown signature is `nullptr`, selected by `stub_unknown_functions = false`. That is a FEX coverage policy. Native availability and FEX's ability to marshal a signature are separate checks. Vulkan's version, extension, and object-scope conditions add further requirements; [the contract reader](VulkanContractReading.md#1-proc-address-lookup-is-a-contract-table) explains them.

**Checkpoint:** identify where the native result can be rejected, where a custom implementation can replace it, and where guest-callable behavior is attached to it.

## 3. Read template annotations as declarations of intent

Near the top of `libvulkan_interface.cpp`:

```cpp
template<>
struct fex_gen_config<vkGetInstanceProcAddr>
  : fexgen::custom_host_impl,
    fexgen::custom_guest_entrypoint,
    fexgen::returns_guest_pointer {};
```

This is an explicit template specialization for one function. The inherited marker types describe how FEX should generate its bridge. Read the declaration as: this command has a custom host implementation, a custom guest entrypoint, and the stated return-pointer policy. The specialization is metadata for generator analysis; its body supplies no runtime lookup algorithm. For the language mechanism, see [C++ explicit specialization](https://eel.is/c++draft/temp.expl.spec).

Now find `function_to_call` in `gen.cpp`. The emitter begins with the native loader-pointer name and substitutes `fexfn_impl_...` when `thunk.custom_host_impl` is set. This is the concrete connection between a declaration and generated behavior.

The manual dynamic lookup table has a second job: choosing custom implementations when Vulkan supplies a name at runtime. [PR #2](https://github.com/teamleaderleo/FEX/pull/2) added an inventory test because those two descriptions had diverged. An annotation only affects paths whose implementation consults it.

**Checkpoint:** point to the declaration, the generator decision, and the runtime name table for the same command.

## 4. Learn `const` from a real ownership decision

In `common/Host.h`, find `repack_wrapper`. It stores an optional host-side value and a reference to the original guest argument. Its constructor prepares the temporary value. Its destructor chooses the cleanup or copyback path.

The type alias is:

```cpp
using PointeeT = std::remove_cv_t<std::remove_pointer_t<T>>;
```

Read the nested transformations from the inside outward. For `T = const A*`, removing the pointer yields `const A`; removing the qualifiers yields `A`. FEX uses that mutable `A` for private host-side storage while retaining the original `T` for its guest-writeback decision. The storage type and the API input type have different jobs.

The original [PR #5](https://github.com/teamleaderleo/FEX/pull/5) preserves `const` when `gen.cpp` emits `make_repack_wrapper<...>`. [PR #14](https://github.com/teamleaderleo/FEX/pull/14) completes the separation between host-only cleanup and mutable exit processing. At the checkpoint, the destructor's relevant branch is:

```cpp
if constexpr (std::is_const_v<std::remove_pointer_t<T>>) {
  fex_apply_custom_repacking_cleanup(*data);
} else {
  // Mutable exit handling, followed by eligible automatic copyback.
}
```

This excerpt omits the outer eligibility checks and mutable branch body; read both in the real header. `if constexpr` selects according to compile-time information. During the relevant template instantiation, the discarded branch stays uninstantiated. See [C++ constexpr if](https://eel.is/c++draft/stmt.if).

`const A*` gives a read-only access path to `A`. `A* const` fixes the pointer variable while permitting access to mutable `A`. A private copy can be changed through its own mutable type. Casting a pointer supplies neither a copy nor an ownership transfer. See [C++ cv-qualification](https://eel.is/c++draft/dcl.type.cv).

The destructor is what makes the lifetime easy to follow: ordinary scope exit performs the selected cleanup. This is the resource-management pattern usually called RAII. It covers C++ lifetime rules; process termination and external resource protocols have their own behavior. See [C++ destructors](https://eel.is/c++draft/class.dtor).

**Checkpoint:** explain why a const input can still require cleanup. Trace one allocation through [the repacking guide](ThunkRepacking.md), which owns the complete three-hook contract.

## 5. Find the point where addresses become stable

In host `CopyInstanceCreatePNextChain`, the x86-64 path accumulates copied nodes in a `std::vector<InstanceCreatePNextCopy>`. The variant holds one of the supported node types. After all `emplace_back` operations finish, a separate loop links the copied nodes together.

That ordering is worth noticing. Vector growth can relocate its elements and invalidate pointers into them. Creating the links after growth gives the links their final addresses for the native call. See [C++ vector insertion and invalidation](https://eel.is/c++draft/vector.modifiers).

Then find `create_info_copy`, `pnext_copies`, and `vk_struct_base` in `vkCreateInstance`. Follow the chosen pointer to the native call. Both local owners remain alive across that call. The copied nodes retain borrowed nested fields where the code performs ordinary shallow copying; this is a bounded node-copy operation, not a universal deep-copy facility.

Read the preprocessor branches explicitly. This copy helper is under `#ifndef IS_32BIT_THUNK`; the other branch uses a different path. The [repacking guide](ThunkRepacking.md) explains the separate 32-bit layout machinery and runtime limits. A test covering both declaration inventories establishes a different fact from executing both runtime paths.

**Checkpoint:** explain why copying and linking happen in two phases, and name the object that keeps the copied nodes alive.

## 6. Separate future lookup from already-held code

The [learning path](OwnedForkLearningPath.md) gives exact source trails for CustomIR rebinding and resident companions. Use two questions when you reach them:

- Which mapping will the next lookup use?
- Which loaded object keeps an address already held by another component executable?

[PR #15](https://github.com/teamleaderleo/FEX/pull/15) changes the exact registered route and retires its cached entries. The later companion work supplies persistent executable owners. These mechanisms answer different lifetime questions.

For a small current example, read [`libvulkan_bridge/Guest.cpp`](../ThunkLibs/libvulkan_bridge/Guest.cpp). The X11 forwarding functions and their `CallbackUnpack` entrypoints live together. Return to `libvulkan/Guest.cpp` and find `FEXGetResidentCallerForHostFunction` and the three `Vulkan_SetGuestX...` publications. The ordinary wrapper publishes addresses whose executable owners live in the companion. The [Vulkan companion guide](VulkanResidentCompanion.md) covers build flags, dependency retention, and the recorded runtime evidence.

Keep application callbacks, generated invokers, and custom X11 unpackers distinct when drawing this. Their signatures and owners differ even though all involve function pointers.

## Where to go next

Choose the next source by what you want to understand. For compact C++ with less thunk machinery, use the cache identity owner and its tests from [WholeFileCodeCacheIdentity.md](WholeFileCodeCacheIdentity.md). For byte layouts and persistence, use [BlockDiskCache.md](BlockDiskCache.md). For the complete inventory of fork work, return to [OwnedForkLearningPath.md](OwnedForkLearningPath.md).

A good reading note ends with an input, a branch condition, an owner, a visible result, and one test that would distinguish the old behavior. The [exercises](FEXReadingExercises.md) make those notes concrete.

This documentation pass read source and references. It establishes no new build, guest-execution, platform, performance, or upstream-acceptance result. Language references point to the evolving C++ working draft; the small concepts used here should be read in the context of the fork's actual compiler and language settings.
