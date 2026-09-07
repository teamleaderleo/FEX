# Reading Vulkan contracts beside FEX

Source checkpoint: owned-fork [`72872bac83`](https://github.com/teamleaderleo/FEX/tree/72872bac83ed714ff9f79143beb4d85926186c32). Khronos pages consulted 2026-09-07. The `latest` URLs evolve; this date records the reading, while the linked source commit fixes the FEX implementation.

Use this beside the [worked C++ companion](FEXCodeReadingCompanion.md) and the [existing routing guide](VulkanProcAddressRouting.md). Each section connects a public API rule to a concrete source decision and a question worth investigating. The [exercises](FEXReadingExercises.md) provide answer checks.

## How to use a reference page

Start with its signature, then its parameter descriptions, behavior table and footnotes, valid-usage conditions, and lifetime or synchronization rules. Keep three statements separate in your notes:

| Statement | Where its evidence comes from |
| --- | --- |
| Vulkan requires or permits a behavior. | The relevant Khronos contract, with its scope and version conditions. |
| This FEX revision implements a behavior. | A named symbol in the pinned source. |
| This behavior was exercised successfully. | The exact test or runtime record, with its source and environment. |

A spec citation gives you a rule to compare with code. The comparison and the execution result still need their own evidence. For a runtime experiment, record the Vulkan implementation, requested API version, enabled extensions/features, guest width, FEX revision, and test inputs. The newest online specification and the headers compiled into a historical experiment can describe different inventories.

## 1. Proc-address lookup is a contract table

Read Khronos's [`vkGetInstanceProcAddr`](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetInstanceProcAddr.html) and [`vkGetDeviceProcAddr`](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetDeviceProcAddr.html) pages side by side. GIPA's scope is an instance and its children; GDPA resolves device-level commands for a particular device and its children. Physical-device-level commands sit outside GDPA's device-level category.

These selected cases are useful for reading the repair. They summarize the cited tables; their preconditions are part of the question.

| Query | Contract result |
| --- | --- |
| GIPA with a null instance, name `vkCreateInstance` | A function pointer: this is a global command. |
| GIPA with a null instance, name `vkCreateDebugReportCallbackEXT` | Null. |
| GIPA with a null instance, name `vkCreateShaderModule` | Null. |
| GIPA with a valid instance, querying GIPA itself | A function pointer. |
| GIPA with a null instance, querying itself | Self-resolution is specified starting with Vulkan 1.2. |
| GDPA with a valid device, name `vkCreateInstance` | Null: instance creation falls outside its device-command scope. |
| GDPA with a null or invalid device | Undefined by the table; this is an invalid-input category, not a null-result requirement. |

The GIPA page also permits queries for available device-extension commands in its specified instance scope. Availability there differs from enabling an extension on a particular device. Read the table's definition of an available extension before inventing a lookup test.

**FEX connection:** in [`libvulkan/Host.cpp`](../ThunkLibs/libvulkan/Host.cpp), find the host GIPA/GDPA implementations. They ask native Vulkan first, preserve its null result, and then select an existing custom wrapper where needed. Custom C++ function existence alone supplies no Vulkan availability decision.

**Version detail worth keeping:** GDPA's footnote discusses commands beyond the application's requested core version. `maintenance5` changes the null-return requirement; without it, a returned pointer for such a command can still be unusable. A non-null result alone is insufficient evidence of permission to call every command. This is a useful example of why footnotes belong in code review.

**Reading question:** which statements in FEX preserve the native answer, and which statements apply FEX-specific coverage policy afterward?

## 2. A generic function pointer carries an incomplete calling description

Khronos defines [`PFN_vkVoidFunction`](https://docs.vulkan.org/refpages/latest/refpages/source/PFN_vkVoidFunction.html) as a placeholder returned by command queries. Before use, the application casts it to the command's actual function-pointer type. That recovered type supplies the argument and return-value description.

**FEX connection:** read `MakeGuestCallable` and `HostPtrInvokers` in [`libvulkan/Guest.cpp`](../ThunkLibs/libvulkan/Guest.cpp). The lookup name finds the guest adapter, and `LinkAddressToFunction` associates the returned address with it. The current default rejects an unknown signature with `nullptr`. The source therefore contains both a native availability decision and an adapter-coverage decision.

A C++ cast changes the program's type-level interpretation. The cross-ISA call requires FEX's actual generated adapters and execution routing. Keep those steps visible in your drawing.

**Reading question:** which operation associates an address with the adapter, and which operation merely expresses a type?

## 3. Debug-callback routing and callback delivery have different outcomes

Read [`VkDebugUtilsMessengerCreateInfoEXT`](https://docs.vulkan.org/refpages/latest/refpages/source/VkDebugUtilsMessengerCreateInfoEXT.html), then [`vkCreateDebugUtilsMessengerEXT`](https://docs.vulkan.org/refpages/latest/refpages/source/vkCreateDebugUtilsMessengerEXT.html). The creation information carries the application callback, its user-data pointer, and message-selection settings. The create command produces a messenger that triggers callbacks for relevant events. The callback runs on the thread of the provoking call, and concurrent Vulkan calls can produce concurrent callbacks.

**FEX connection:** in [`libvulkan/Host.cpp`](../ThunkLibs/libvulkan/Host.cpp), read the custom debug-report and debug-utils creation functions. Each obtains a host representation, replaces the callback with a host dummy, and calls native Vulkan using that copy. The dummy returns `VK_FALSE`. The custom paths pass a null allocator.

This is the fork's current callback-suppression policy. The routing repair makes the dynamic path use that existing policy. Full application callback delivery would require additional cross-ISA marshalling, retained callback/user-data ownership, and destruction handling. The resident Vulkan companion currently owns generated invokers and its named X11 publications; its existence alone establishes none of those additional application-callback behaviors. See [VulkanResidentCompanion.md](VulkanResidentCompanion.md).

**Reading question:** after creation succeeds, which exact callback will native Vulkan call at this checkpoint? Answer from the assignment in the custom wrapper.

## 4. Read `pNext` through both its type and its lifetime

Start with [`VkInstanceCreateInfo`](https://docs.vulkan.org/refpages/latest/refpages/source/VkInstanceCreateInfo.html). `sType` identifies a node's kind, and `pNext` links an extension node. Its current valid-usage list specifies the allowed node types and the exceptions to the ordinary uniqueness rule.

The same page distinguishes two callback routes. A debug create-info node in the instance-creation chain provides callbacks during **instance creation and instance destruction**. Explicit debug callback/messenger creation produces a persistent callback object. Write those lifetimes separately; the temporary input node and its callback target have different responsibilities.

**FEX connection:** read `CopyInstanceCreatePNextChain` and the x86-64 branch of `vkCreateInstance` in [`Host.cpp`](../ThunkLibs/libvulkan/Host.cpp). Supported nodes are copied into local host-owned storage, callback fields are replaced in those copies, and links are assigned after the vector finishes growing. The native call receives the copied root when mediation is needed. Nested pointer fields can remain borrowed according to the individual node's contract.

Khronos's [fundamentals chapter](https://docs.vulkan.org/spec/latest/chapters/fundamentals.html), under object lifetime and pointer-chain valid usage, describes command-time access, explicitly retained references, and how components skip extension nodes they do not support. Use those rules to examine each borrowed field and retained callback independently.

**Compatibility question to retain:** FEX's callback-copy helper has a finite typed inventory and returns failure for an unrecognized node on that path. Treat that as a bounded implementation policy to review when the supported header/spec inventory changes. A generic requirement to skip unsupported extension nodes supplies a comparison point; it does not teach a cross-ISA copier the size and ownership of a new node.

**Reading question:** identify which exact input fields FEX changes in its private copies and which nested fields remain borrowed. Then identify the preprocessor branch to which that answer applies.

## 5. Creation, destruction, and allocation callbacks form a pair

Read [`vkDestroyDebugUtilsMessengerEXT`](https://docs.vulkan.org/refpages/latest/refpages/source/vkDestroyDebugUtilsMessengerEXT.html). Its allocator conditions depend on how the messenger was created. Its synchronization requirements protect the messenger and exclude destruction while relevant calls or callbacks are active. Review the whole synchronization section when designing a lifecycle test.

Then read the [host-memory allocation rules](https://docs.vulkan.org/spec/latest/chapters/memory.html). When an implementation retains allocator information for later calls, it copies that information before creation returns. Callback functions and the data they rely on remain valid for the associated object's lifetime. Creation and destruction use compatible allocators; a null creation allocator requires a null destruction allocator.

**FEX connection:** the custom wrappers' `nullptr` allocator arguments select native default allocation in those paths. Preserving an application's allocator callbacks would be a separate feature with a paired lifetime contract. Read both sides of an allocation or creation API before changing either argument.

For a particularly small pair, inspect custom `vkCreateDebugReportCallbackEXT` and `vkDestroyDebugReportCallbackEXT` in `Host.cpp`. For custom repacker temporaries, use [ThunkRepacking.md](ThunkRepacking.md): those are FEX's own host-side allocations, distinct from a Vulkan application's `VkAllocationCallbacks`.

**Reading question:** who allocated the memory being released: the application's allocator, native Vulkan's default allocator, or a FEX repacker?

## 6. Direct driver loading exposes another callback boundary

Read [`VkDirectDriverLoadingInfoLUNARG`](https://docs.vulkan.org/refpages/latest/refpages/source/VkDirectDriverLoadingInfoLUNARG.html). Its `pfnGetInstanceProcAddr` member points to a driver's lookup function.

**FEX connection:** host `vkCreateInstance` checks for the direct-driver-loading node and returns `VK_ERROR_EXTENSION_NOT_PRESENT`. Guest `vkEnumerateInstanceExtensionProperties` filters the extension from its returned list; guest GIPA preserves that guest enumeration entrypoint. Read both files together.

The source comment explains the boundary: a guest x86 driver's callback and its returned code addresses need mediation before native host code can use them. The current fork chooses refusal and filtered advertisement. That choice is FEX's implementation limit, separate from what native Vulkan's extension permits.

**Reading question:** why does changing only the advertised extension list leave another entry path to examine?

## Turning a reference into a useful question

Keep a note this small:

```text
Contract: exact command, table row or valid-usage condition, and preconditions.
Source: exact FEX revision, file, symbol, and selected ABI branch.
Prediction: the observable result expected from those inputs.
Evidence: source inspection, named test, or exact runtime record.
Remaining question: one uncertainty that could change the conclusion.
```

For example, the custom-routing inventory compares declarations with runtime name coverage. Its result says whether those inventories agree. Native-versus-FEX lookup behavior requires a different observation. Callback delivery and unload safety require their own lifetime checks. The [exercise sheet](FEXReadingExercises.md) keeps these activities separate.

This reader adds explanations and reference links. It reports no new Vulkan conformance, ARM execution, driver coverage, or benchmark result.
