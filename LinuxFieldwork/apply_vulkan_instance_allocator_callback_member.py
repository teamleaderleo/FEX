from pathlib import Path

# Reuse the already-green generator/member/bridge experiment verbatim.
exec(compile(Path('LinuxFieldwork/apply_vulkan_allocator_callback_member.py').read_text(),
             'apply_vulkan_allocator_callback_member.py', 'exec'))

host = Path('ThunkLibs/libvulkan/Host.cpp')
s = host.read_text()
old = '  auto ret = LDR_PTR(vkCreateInstance)(vk_struct_base, nullptr, &out);'
new = '  auto ret = LDR_PTR(vkCreateInstance)(vk_struct_base, a_1, &out);'
assert s.count(old) == 1, s.count(old)
host.write_text(s.replace(old, new, 1))

print('generated VkAllocationCallbacks + resident bridge + vkCreateInstance allocator forwarding applied')
