from pathlib import Path

path = Path("ThunkLibs/libvulkan/Host.cpp")
text = path.read_text()

anchor = '''static VkBool32
DummyVkDebugReportCallback(VkDebugReportFlagsEXT, VkDebugReportObjectTypeEXT, uint64_t, size_t, int32_t, const char*, const char*, void*) {
  return VK_FALSE;
}

static VkResult FEXFN_IMPL(vkCreateInstance)(const VkInstanceCreateInfo* a_0, const VkAllocationCallbacks* a_1, guest_layout<VkInstance*> a_2) {
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
'''

replacement = '''static VkBool32
DummyVkDebugReportCallback(VkDebugReportFlagsEXT, VkDebugReportObjectTypeEXT, uint64_t, size_t, int32_t, const char*, const char*, void*) {
  return VK_FALSE;
}

extern "C" VkBool32 DummyVkDebugUtilsMessengerCallback(VkDebugUtilsMessageSeverityFlagBitsEXT, VkDebugUtilsMessageTypeFlagsEXT,
                                                       const VkDebugUtilsMessengerCallbackDataEXT*, void*);

static VkResult FEXFN_IMPL(vkCreateInstance)(const VkInstanceCreateInfo* a_0, const VkAllocationCallbacks* a_1, guest_layout<VkInstance*> a_2) {
  const VkInstanceCreateInfo* vk_struct_base = a_0;
  for (const VkBaseInStructure* vk_struct = reinterpret_cast<const VkBaseInStructure*>(vk_struct_base); vk_struct->pNext;) {
    const auto* next = reinterpret_cast<const VkBaseInStructure*>(vk_struct->pNext);

    if (next->sType == VK_STRUCTURE_TYPE_DEBUG_REPORT_CREATE_INFO_EXT) {
      // Generic guest callbacks are not supported here. Match the existing
      // debug-report workaround by removing this callback-bearing record.
      const_cast<VkBaseInStructure*>(vk_struct)->pNext = next->pNext;
      // Re-examine the replacement node so adjacent callback records are not skipped.
      continue;
    }

    if (next->sType == VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT) {
      // Preserve the debug-utils record, but replace the guest callback with
      // the same host-side dummy used by vkCreateDebugUtilsMessengerEXT.
      auto* debug_utils = const_cast<VkDebugUtilsMessengerCreateInfoEXT*>(
        reinterpret_cast<const VkDebugUtilsMessengerCreateInfoEXT*>(next));
      debug_utils->pfnUserCallback = DummyVkDebugUtilsMessengerCallback;
    }

    vk_struct = next;
  }

  VkInstance out;
'''

count = text.count(anchor)
if count != 1:
    raise SystemExit(f"expected one vkCreateInstance callback-chain anchor, found {count}")
text = text.replace(anchor, replacement, 1)
path.write_text(text)
