from pathlib import Path

path = Path("ThunkLibs/libvulkan/Host.cpp")
text = path.read_text()
old = '''  VkInstance out;
  auto ret = LDR_PTR(vkCreateInstance)(vk_struct_base, nullptr, &out);
  *a_2.get_pointer() = to_guest(to_host_layout(out));'''
new = '''  VkInstance out;
  auto ret = LDR_PTR(vkCreateInstance)(vk_struct_base, a_1, &out);
  *a_2.get_pointer() = to_guest(to_host_layout(out));'''
if text.count(old) != 1:
    raise SystemExit(f"expected one vkCreateInstance allocator suppression site, found {text.count(old)}")
path.write_text(text.replace(old, new, 1))
