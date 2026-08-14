from pathlib import Path

path = Path("ThunkLibs/libvulkan/Host.cpp")
text = path.read_text()

replacements = {
    'return LDR_PTR(vkCreateShaderModule)(a_0, a_1, nullptr, a_3);':
        'return LDR_PTR(vkCreateShaderModule)(a_0, a_1, a_2, a_3);',
    'auto ret = LDR_PTR(vkCreateInstance)(vk_struct_base, nullptr, &out);':
        'auto ret = LDR_PTR(vkCreateInstance)(vk_struct_base, a_1, &out);',
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

for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one allocator suppression site for {old!r}, found {count}")
    text = text.replace(old, new, 1)

path.write_text(text)
