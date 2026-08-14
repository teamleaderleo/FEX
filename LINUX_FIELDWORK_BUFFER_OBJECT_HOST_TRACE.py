#!/usr/bin/env python3
from pathlib import Path

iface = Path('ThunkLibs/libvulkan/libvulkan_interface.cpp')
host = Path('ThunkLibs/libvulkan/Host.cpp')

s = iface.read_text()
needle = 'template<>\nstruct fex_gen_config<vkGetInstanceProcAddr> : fexgen::custom_host_impl, fexgen::custom_guest_entrypoint, fexgen::returns_guest_pointer {};\n'
insert = needle + '''template<>\nstruct fex_gen_config<vkCreateBuffer> : fexgen::custom_host_impl {};\ntemplate<>\nstruct fex_gen_config<vkDestroyBuffer> : fexgen::custom_host_impl {};\n'''
if 'struct fex_gen_config<vkCreateBuffer> : fexgen::custom_host_impl' not in s:
    if needle not in s:
        raise SystemExit('interface insertion point not found')
    s = s.replace(needle, insert, 1)
iface.write_text(s)

s = host.read_text()
if '#include <cstdio>\n' not in s:
    s = s.replace('#include <cstring>\n', '#include <cstring>\n#include <cstdio>\n', 1)

needle = '''static VkResult\nFEXFN_IMPL(vkCreateShaderModule)(VkDevice a_0, const VkShaderModuleCreateInfo* a_1, const VkAllocationCallbacks* a_2, VkShaderModule* a_3) {\n  (void*&)LDR_PTR(vkCreateShaderModule) = (void*)LDR_PTR(vkGetDeviceProcAddr)(a_0, "vkCreateShaderModule");\n  return LDR_PTR(vkCreateShaderModule)(a_0, a_1, nullptr, a_3);\n}\n'''
block = needle + r'''
static VkBuffer TracedBuffer {};
static unsigned char TracedBufferBytes[64] {};
static bool HaveTracedBuffer {};

static void DumpBufferWords(const char* tag, VkBuffer buffer) {
  auto ptr = reinterpret_cast<const uint64_t*>(static_cast<uintptr_t>(buffer));
  fprintf(stderr, "%s buffer=0x%llx words=%016llx,%016llx,%016llx,%016llx\n", tag,
          static_cast<unsigned long long>(buffer),
          static_cast<unsigned long long>(ptr[0]), static_cast<unsigned long long>(ptr[1]),
          static_cast<unsigned long long>(ptr[2]), static_cast<unsigned long long>(ptr[3]));
  fflush(stderr);
}

static VkResult FEXFN_IMPL(vkCreateBuffer)(VkDevice device, const VkBufferCreateInfo* info,
                                           const VkAllocationCallbacks* allocator, VkBuffer* out) {
  (void*&)LDR_PTR(vkCreateBuffer) = (void*)LDR_PTR(vkGetDeviceProcAddr)(device, "vkCreateBuffer");
  fprintf(stderr, "HOST_BUFFER_CREATE_ENTER allocator=%p alloc=%p free=%p\n", (const void*)allocator,
          allocator ? (const void*)allocator->pfnAllocation : nullptr,
          allocator ? (const void*)allocator->pfnFree : nullptr);
  fflush(stderr);
  auto ret = LDR_PTR(vkCreateBuffer)(device, info, allocator, out);
  fprintf(stderr, "HOST_BUFFER_CREATE_RETURN result=%d buffer=0x%llx\n", ret,
          static_cast<unsigned long long>(ret == VK_SUCCESS ? *out : 0));
  fflush(stderr);
  if (ret == VK_SUCCESS && *out) {
    TracedBuffer = *out;
    memcpy(TracedBufferBytes, reinterpret_cast<const void*>(static_cast<uintptr_t>(*out)), sizeof(TracedBufferBytes));
    HaveTracedBuffer = true;
    DumpBufferWords("HOST_BUFFER_CREATE_BYTES", *out);
  }
  return ret;
}

static void FEXFN_IMPL(vkDestroyBuffer)(VkDevice device, VkBuffer buffer, const VkAllocationCallbacks* allocator) {
  (void*&)LDR_PTR(vkDestroyBuffer) = (void*)LDR_PTR(vkGetDeviceProcAddr)(device, "vkDestroyBuffer");
  fprintf(stderr, "HOST_BUFFER_DESTROY_ENTER buffer=0x%llx allocator=%p alloc=%p free=%p same_handle=%d\n",
          static_cast<unsigned long long>(buffer), (const void*)allocator,
          allocator ? (const void*)allocator->pfnAllocation : nullptr,
          allocator ? (const void*)allocator->pfnFree : nullptr,
          HaveTracedBuffer && buffer == TracedBuffer);
  fflush(stderr);
  if (buffer) {
    DumpBufferWords("HOST_BUFFER_DESTROY_BYTES", buffer);
    if (HaveTracedBuffer && buffer == TracedBuffer) {
      int diff = memcmp(TracedBufferBytes, reinterpret_cast<const void*>(static_cast<uintptr_t>(buffer)), sizeof(TracedBufferBytes));
      fprintf(stderr, "HOST_BUFFER_SNAPSHOT_COMPARE diff=%d\n", diff);
      fflush(stderr);
    }
  }
  LDR_PTR(vkDestroyBuffer)(device, buffer, allocator);
  fprintf(stderr, "HOST_BUFFER_DESTROY_RETURN\n");
  fflush(stderr);
}
'''
if 'HOST_BUFFER_CREATE_ENTER' not in s:
    if needle not in s:
        raise SystemExit('Host insertion point not found')
    s = s.replace(needle, block, 1)
host.write_text(s)
