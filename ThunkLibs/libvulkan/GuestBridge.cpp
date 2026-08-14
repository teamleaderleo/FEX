#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_XRANDR_EXT
#define VK_USE_PLATFORM_XLIB_KHR
#define VK_USE_PLATFORM_XCB_KHR
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>

#include "common/Guest.h"

#include <cstdint>
#include <cstdio>
#include <string_view>
#include <unordered_map>

#include "vulkan_bridge_thunks.inl"

// The wrapper still owns library-specific state and host-pack calls. This DSO
// owns only addresses that may escape wrapper lifetime into FEX/host state.
#define VULKAN_BRIDGE_SYMBOL(name) \
  static const uintptr_t resident_##name = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));
#include "vulkan_bridge_symbols.inl"
#undef VULKAN_BRIDGE_SYMBOL

[[noreturn]]
static void ResidentFatalError(void* raw_args) {
  auto called_function = reinterpret_cast<PackedArguments<void, uintptr_t>*>(raw_args)->a0;
  fprintf(stderr, "FATAL: Called unknown Vulkan function at address %p\n", reinterpret_cast<void*>(called_function));
  __builtin_trap();
}

extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_find_host_invoker(const char* name) {
  static const std::unordered_map<std::string_view, uintptr_t> Invokers = [] {
    std::unordered_map<std::string_view, uintptr_t> Ret;
#define VULKAN_BRIDGE_SYMBOL(symbol) Ret[#symbol] = resident_##symbol;
#include "vulkan_bridge_symbols.inl"
#undef VULKAN_BRIDGE_SYMBOL
    return Ret;
  }();
  auto It = Invokers.find(name);
  return It == Invokers.end() ? 0 : It->second;
}

extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_fatal_invoker() {
  const auto StubHostPtrInvoker = CallHostFunction<ResidentFatalError, void>;
  return reinterpret_cast<uintptr_t>(StubHostPtrInvoker);
}

extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_xsync_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XSync)>::Unpack);
}
extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_xgetvisualinfo_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}
extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_xdisplaystring_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
