from pathlib import Path

host_path = Path("ThunkLibs/libvulkan/Host.cpp")
host = host_path.read_text()
anchor = '''static VkResult FEXFN_IMPL(vkCreateInstance)(const VkInstanceCreateInfo* a_0, const VkAllocationCallbacks* a_1, guest_layout<VkInstance*> a_2) {'''
start = host.find(anchor)
if start < 0:
    raise SystemExit("vkCreateInstance custom wrapper anchor missing")
# Insert after the complete vkCreateInstance wrapper and before vkCreateDevice.
end_anchor = '''static VkResult FEXFN_IMPL(vkCreateDevice)'''
end = host.find(end_anchor, start)
if end < 0:
    raise SystemExit("vkCreateDevice anchor missing")
if "FEXFN_IMPL(vkDestroyInstance)" in host:
    raise SystemExit("vkDestroyInstance is already custom in Host.cpp")
insert = '''static void FEXFN_IMPL(vkDestroyInstance)(VkInstance a_0, const VkAllocationCallbacks* a_1) {
  (void)a_1;
  LDR_PTR(vkDestroyInstance)(a_0, nullptr);
}

'''
host = host[:end] + insert + host[end:]
host_path.write_text(host)

iface_path = Path("ThunkLibs/libvulkan/libvulkan_interface.cpp")
iface = iface_path.read_text()
old = '''template<>
struct fex_gen_config<vkDestroyInstance> {};'''
new = '''template<>
struct fex_gen_config<vkDestroyInstance> : fexgen::custom_host_impl {};'''
if iface.count(old) != 1:
    raise SystemExit(f"expected one vkDestroyInstance config, found {iface.count(old)}")
iface = iface.replace(old, new, 1)
iface_path.write_text(iface)
