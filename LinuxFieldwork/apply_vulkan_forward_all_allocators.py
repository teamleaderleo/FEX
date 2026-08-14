from pathlib import Path

# Start from the already-green generated VkAllocationCallbacks callback_member
# conversion, resident sidecar, and vkCreateInstance allocator forwarding.
exec(compile(Path('LinuxFieldwork/apply_vulkan_instance_allocator_callback_member.py').read_text(),
             'apply_vulkan_instance_allocator_callback_member.py', 'exec'))

p = Path('ThunkLibs/libvulkan/Host.cpp')
s = p.read_text()
repls = {
    'return LDR_PTR(vkCreateShaderModule)(a_0, a_1, nullptr, a_3);':
        'return LDR_PTR(vkCreateShaderModule)(a_0, a_1, a_2, a_3);',
    'auto ret = LDR_PTR(vkCreateDevice)(a_0, a_1, nullptr, &out);':
        'auto ret = LDR_PTR(vkCreateDevice)(a_0, a_1, a_2, &out);',
    'return LDR_PTR(vkAllocateMemory)(a_0, a_1, nullptr, a_3);':
        'return LDR_PTR(vkAllocateMemory)(a_0, a_1, a_2, a_3);',
    'LDR_PTR(vkFreeMemory)(a_0, a_1, nullptr);':
        'LDR_PTR(vkFreeMemory)(a_0, a_1, a_2);',
    'return LDR_PTR(vkCreateDebugReportCallbackEXT)(a_0, &overridden_callback, nullptr, a_3);':
        'return LDR_PTR(vkCreateDebugReportCallbackEXT)(a_0, &overridden_callback, a_2, a_3);',
    'LDR_PTR(vkDestroyDebugReportCallbackEXT)(a_0, a_1, nullptr);':
        'LDR_PTR(vkDestroyDebugReportCallbackEXT)(a_0, a_1, a_2);',
    'return LDR_PTR(vkCreateDebugUtilsMessengerEXT)(a_0, &overridden_callback, nullptr, a_3);':
        'return LDR_PTR(vkCreateDebugUtilsMessengerEXT)(a_0, &overridden_callback, a_2, a_3);',
}
for old, new in repls.items():
    count = s.count(old)
    assert count == 1, (old, count)
    s = s.replace(old, new, 1)
p.write_text(s)

print('forwarded generated VkAllocationCallbacks through all custom host allocator call sites')
