/*
$info$
tags: thunklibs|Vulkan
$end_info$
*/

#define VK_USE_64_BIT_PTR_DEFINES 0

#define VK_USE_PLATFORM_XLIB_XRANDR_EXT
#define VK_USE_PLATFORM_XLIB_KHR
#define VK_USE_PLATFORM_XCB_KHR
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>

#include "common/Guest.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <functional>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "thunkgen_guest_libvulkan_bridge_accessors.inl"

#define AllocateHostTrampolineForGuestFunction FEXAllocateResidentHostTrampolineForGuestFunction
#include "thunkgen_guest_libvulkan.inl"
#undef AllocateHostTrampolineForGuestFunction

extern "C" int FEXVulkanBridgeXSync(Display*, Bool);
extern "C" XVisualInfo* FEXVulkanBridgeXGetVisualInfo(Display*, long, XVisualInfo*, int*);
extern "C" char* FEXVulkanBridgeXDisplayString(Display*);
extern "C" uintptr_t FEXVulkanBridgeXSyncUnpacker();
extern "C" uintptr_t FEXVulkanBridgeXGetVisualInfoUnpacker();
extern "C" uintptr_t FEXVulkanBridgeXDisplayStringUnpacker();

extern "C" {

// Maps Vulkan API function names to process-resident guest bridge functions
// linked to the corresponding host function pointer.
const std::unordered_map<std::string_view, uintptr_t /* guest function address */> HostPtrInvokers = std::invoke([]() {
#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(FEXGetResidentCallerForHostFunction(name));
  std::unordered_map<std::string_view, uintptr_t> Ret;
  FOREACH_internal_SYMBOL(PAIR);
  return Ret;
#undef PAIR
});

// This variable controls the behavior of vkGetDevice/InstanceProcAddr for functions we don't know the signature of:
// - if false (default), we return a nullptr (since the application might have a fallback code path)
// - if true, we return a stub function that fatally errors upon being called
constexpr bool stub_unknown_functions = false;

// Fatally erroring function with a thunk-like interface. This is used as a placeholder for unknown Vulkan functions
[[noreturn]]
static void FatalError(void* raw_args) {
  auto called_function = reinterpret_cast<PackedArguments<void, uintptr_t>*>(raw_args)->a0;
  fprintf(stderr, "FATAL: Called unknown Vulkan function at address %p\n", reinterpret_cast<void*>(called_function));
  __builtin_trap();
}

static PFN_vkVoidFunction MakeGuestCallable(const char* origin, PFN_vkVoidFunction func, const char* name) {
  auto It = HostPtrInvokers.find(name);
  if (It == HostPtrInvokers.end()) {
    fprintf(stderr, "%s: Unknown Vulkan function at address %p: %s\n", origin, func, name);
    if (stub_unknown_functions) {
      const auto StubHostPtrInvoker = CallHostFunction<FatalError, void>;
      LinkAddressToFunction((uintptr_t)func, reinterpret_cast<uintptr_t>(StubHostPtrInvoker));
      return func;
    }
    return nullptr;
  }
  fprintf(stderr, "Linking address %p to host invoker %#zx\n", func, It->second);
  LinkAddressToFunction((uintptr_t)func, It->second);
  return func;
}

VkResult vkEnumerateInstanceExtensionProperties(const char* layer_name, uint32_t* property_count, VkExtensionProperties* properties_out) {
  constexpr unsigned MAX_ENUMERATION_ATTEMPTS = 4;
  std::vector<VkExtensionProperties> properties;
  VkResult result = VK_INCOMPLETE;

  for (unsigned attempt = 0; attempt < MAX_ENUMERATION_ATTEMPTS; ++attempt) {
    uint32_t count = 0;
    result = fexfn_pack_vkEnumerateInstanceExtensionProperties(layer_name, &count, nullptr);
    if (result != VK_SUCCESS) {
      return result;
    }

    properties.resize(count);
    result = fexfn_pack_vkEnumerateInstanceExtensionProperties(layer_name, &count, properties.data());
    properties.resize(count);
    if (result != VK_INCOMPLETE) {
      break;
    }
  }

  if (result != VK_SUCCESS) {
    return result;
  }

  std::erase_if(properties, [](const VkExtensionProperties& property) {
    return std::strcmp(property.extensionName, VK_LUNARG_DIRECT_DRIVER_LOADING_EXTENSION_NAME) == 0;
  });

  if (!properties_out) {
    *property_count = static_cast<uint32_t>(properties.size());
    return VK_SUCCESS;
  }

  const uint32_t capacity = *property_count;
  const uint32_t available = static_cast<uint32_t>(properties.size());
  const uint32_t written = std::min(capacity, available);
  std::copy_n(properties.begin(), written, properties_out);
  *property_count = written;
  return written < available ? VK_INCOMPLETE : VK_SUCCESS;
}

PFN_vkVoidFunction vkGetDeviceProcAddr(VkDevice a_0, const char* a_1) {
  auto Ret = fexfn_pack_vkGetDeviceProcAddr(a_0, a_1);
  if (!Ret) {
    return nullptr;
  }

  if (a_1 == std::string_view {"vkGetDeviceProcAddr"}) {
    return (PFN_vkVoidFunction)vkGetDeviceProcAddr;
  }

  return MakeGuestCallable(__FUNCTION__, Ret, a_1);
}

PFN_vkVoidFunction vkGetInstanceProcAddr(VkInstance a_0, const char* a_1) {
  auto Ret = fexfn_pack_vkGetInstanceProcAddr(a_0, a_1);
  if (!Ret) {
    return nullptr;
  }

  if (a_1 == std::string_view {"vkGetInstanceProcAddr"}) {
    return (PFN_vkVoidFunction)vkGetInstanceProcAddr;
  }
  if (a_1 == std::string_view {"vkGetDeviceProcAddr"}) {
    return (PFN_vkVoidFunction)vkGetDeviceProcAddr;
  }
  if (a_1 == std::string_view {"vkEnumerateInstanceExtensionProperties"}) {
    return (PFN_vkVoidFunction)vkEnumerateInstanceExtensionProperties;
  }

  return MakeGuestCallable(__FUNCTION__, Ret, a_1);
}
}

void OnInit() {
  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)FEXVulkanBridgeXSync, FEXVulkanBridgeXSyncUnpacker());
  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)FEXVulkanBridgeXGetVisualInfo, FEXVulkanBridgeXGetVisualInfoUnpacker());
  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)FEXVulkanBridgeXDisplayString, FEXVulkanBridgeXDisplayStringUnpacker());
}

LOAD_LIB_INIT(libvulkan, OnInit)
