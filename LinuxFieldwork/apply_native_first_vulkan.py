from pathlib import Path

host = Path('ThunkLibs/libvulkan/Host.cpp')
s = host.read_text()
anchor = '''  } else if (a_1 == "vkFreeMemory"sv) {\n    return (PFN_vkVoidFunction)fexfn_impl_libvulkan_vkFreeMemory;\n'''
insert = anchor + '''  } else if (a_1 == "vkCreateDebugReportCallbackEXT"sv) {\n    return (PFN_vkVoidFunction)fexfn_impl_libvulkan_vkCreateDebugReportCallbackEXT;\n  } else if (a_1 == "vkDestroyDebugReportCallbackEXT"sv) {\n    return (PFN_vkVoidFunction)fexfn_impl_libvulkan_vkDestroyDebugReportCallbackEXT;\n  } else if (a_1 == "vkCreateDebugUtilsMessengerEXT"sv) {\n    return (PFN_vkVoidFunction)fexfn_impl_libvulkan_vkCreateDebugUtilsMessengerEXT;\n'''
assert s.count(anchor) == 1, s.count(anchor)
s = s.replace(anchor, insert, 1)

old = '''static PFN_vkVoidFunction FEXFN_IMPL(vkGetDeviceProcAddr)(VkDevice a_0, const char* a_1) {\n  // Just return the host facing function pointer\n  // The guest will handle mapping if this exists\n\n  // Check for functions with custom implementations first\n  if (auto ptr = LookupCustomVulkanFunction(a_1)) {\n    return ptr;\n  }\n\n  return LDR_PTR(vkGetDeviceProcAddr)(a_0, a_1);\n}\n'''
new = '''static PFN_vkVoidFunction FEXFN_IMPL(vkGetDeviceProcAddr)(VkDevice a_0, const char* a_1) {\n  auto NativePtr = LDR_PTR(vkGetDeviceProcAddr)(a_0, a_1);\n  if (!NativePtr) {\n    return nullptr;\n  }\n  if (auto ptr = LookupCustomVulkanFunction(a_1)) {\n    return ptr;\n  }\n  return NativePtr;\n}\n'''
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new, 1)

setup = '''  if (!SetupInstance && a_0) {\n    DoSetupWithInstance(a_0);\n  }\n\n  // Check for functions with custom implementations first\n'''
repl = '''  if (!SetupInstance && a_0) {\n    DoSetupWithInstance(a_0);\n  }\n\n  auto NativePtr = LDR_PTR(vkGetInstanceProcAddr)(a_0, a_1);\n  if (!NativePtr) {\n    return nullptr;\n  }\n\n  // Substitute custom implementations only after native validity is established.\n'''
assert s.count(setup) == 1, s.count(setup)
s = s.replace(setup, repl, 1)
tail = '''  return LDR_PTR(vkGetInstanceProcAddr)(a_0, a_1);\n}\n\n#ifdef IS_32BIT_THUNK\n'''
assert s.count(tail) == 1, s.count(tail)
s = s.replace(tail, '''  return NativePtr;\n}\n\n#ifdef IS_32BIT_THUNK\n''', 1)
host.write_text(s)

guest = Path('ThunkLibs/libvulkan/Guest.cpp')
s = guest.read_text()
old = '''PFN_vkVoidFunction vkGetInstanceProcAddr(VkInstance a_0, const char* a_1) {\n  if (a_1 == std::string_view {"vkGetDeviceProcAddr"}) {\n    return (PFN_vkVoidFunction)vkGetDeviceProcAddr;\n  } else {\n    auto Ret = fexfn_pack_vkGetInstanceProcAddr(a_0, a_1);\n    if (!Ret) {\n      return nullptr;\n    }\n    return MakeGuestCallable(__FUNCTION__, Ret, a_1);\n  }\n}\n'''
new = '''PFN_vkVoidFunction vkGetInstanceProcAddr(VkInstance a_0, const char* a_1) {\n  auto Ret = fexfn_pack_vkGetInstanceProcAddr(a_0, a_1);\n  if (!Ret) {\n    return nullptr;\n  }\n  if (a_1 == std::string_view {"vkGetDeviceProcAddr"}) {\n    return (PFN_vkVoidFunction)vkGetDeviceProcAddr;\n  }\n  if (a_1 == std::string_view {"vkGetInstanceProcAddr"}) {\n    return (PFN_vkVoidFunction)vkGetInstanceProcAddr;\n  }\n  return MakeGuestCallable(__FUNCTION__, Ret, a_1);\n}\n'''
assert s.count(old) == 1, s.count(old)
guest.write_text(s.replace(old, new, 1))
