from pathlib import Path

path = Path("ThunkLibs/libvulkan/Host.cpp")
text = path.read_text()

old_include = "#include <cassert>\n#include <cstring>"
new_include = "#include <cassert>\n#include <cstdio>\n#include <cstring>"
if text.count(old_include) != 1:
    raise SystemExit(f"expected one include anchor, found {text.count(old_include)}")
text = text.replace(old_include, new_include, 1)

old = '''static VkResult FEXFN_IMPL(vkCreateInstance)(const VkInstanceCreateInfo* a_0, const VkAllocationCallbacks* a_1, guest_layout<VkInstance*> a_2) {
  const VkInstanceCreateInfo* vk_struct_base = a_0;
  for (const VkBaseInStructure* vk_struct = reinterpret_cast<const VkBaseInStructure*>(vk_struct_base); vk_struct->pNext;
       vk_struct = vk_struct->pNext) {
    // Override guest callbacks used for VK_EXT_debug_report
    if (reinterpret_cast<const VkBaseInStructure*>(vk_struct->pNext)->sType == VK_STRUCTURE_TYPE_DEBUG_REPORT_CREATE_INFO_EXT) {
      // Overwrite the pNext pointer, ignoring its const-qualifier
      const_cast<VkBaseInStructure*>(vk_struct)->pNext = vk_struct->pNext->pNext;

      // If we copied over a nullptr for pNext then early exit
      if (!vk_struct->pNext) {
        break;
      }
    }
  }

  VkInstance out;
  auto ret = LDR_PTR(vkCreateInstance)(vk_struct_base, nullptr, &out);
  *a_2.get_pointer() = to_guest(to_host_layout(out));
  return ret;
}
'''

new = '''static VkBool32 DummyVkDebugUtilsMessengerPNextCallback(VkDebugUtilsMessageSeverityFlagBitsEXT,
                                                        VkDebugUtilsMessageTypeFlagsEXT,
                                                        const VkDebugUtilsMessengerCallbackDataEXT*, void*) {
  std::fputs("FEX_DEBUG_UTILS_PNEXT_DUMMY_HIT\\n", stderr);
  return VK_FALSE;
}

static VkResult FEXFN_IMPL(vkCreateInstance)(const VkInstanceCreateInfo* a_0, const VkAllocationCallbacks* a_1, guest_layout<VkInstance*> a_2) {
  const VkInstanceCreateInfo* vk_struct_base = a_0;
  for (const VkBaseInStructure* vk_struct = reinterpret_cast<const VkBaseInStructure*>(vk_struct_base); vk_struct->pNext;
       vk_struct = vk_struct->pNext) {
    // Override guest callbacks used for debug-report and debug-utils extensions.
    const auto next_type = reinterpret_cast<const VkBaseInStructure*>(vk_struct->pNext)->sType;
    if (next_type == VK_STRUCTURE_TYPE_DEBUG_REPORT_CREATE_INFO_EXT) {
      // Overwrite the pNext pointer, ignoring its const-qualifier
      const_cast<VkBaseInStructure*>(vk_struct)->pNext = vk_struct->pNext->pNext;

      // If we copied over a nullptr for pNext then early exit
      if (!vk_struct->pNext) {
        break;
      }
    } else if (next_type == VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT) {
      auto* debug_utils = const_cast<VkDebugUtilsMessengerCreateInfoEXT*>(
        reinterpret_cast<const VkDebugUtilsMessengerCreateInfoEXT*>(vk_struct->pNext));
      debug_utils->pfnUserCallback = DummyVkDebugUtilsMessengerPNextCallback;
    }
  }

  VkInstance out;
  auto ret = LDR_PTR(vkCreateInstance)(vk_struct_base, nullptr, &out);
  *a_2.get_pointer() = to_guest(to_host_layout(out));
  return ret;
}
'''

if text.count(old) != 1:
    raise SystemExit(f"expected one vkCreateInstance anchor, found {text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text)
