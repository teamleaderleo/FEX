from pathlib import Path

# Start from the proven generic callback_member generator plus automatically
# derived resident per-library bridge. This routes every generated DRM guest
# callback allocation through the NODELETE sidecar by signature.
base = Path('LinuxFieldwork/apply_nested_callback_resident_bridge.py').read_text()
exec(compile(base, 'apply_nested_callback_resident_bridge.py', 'exec'))

iface = Path('ThunkLibs/libdrm/libdrm_interface.cpp')
s = iface.read_text()
old = 'template<>\nstruct fex_gen_config<drmSetServerInfo> {};'
new = 'template<>\nstruct fex_gen_config<drmSetServerInfo> : fexgen::custom_host_impl {};'
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new, 1)

# drmServerInfo::debug_print carries va_list and currently exceeds generic
# callback projection support. Isolate the retained callback implicated by the
# lifetime proof: load_module has the ordinary int(const char*) signature.
old = 'template<>\nstruct fex_gen_type<drmServerInfo> : fexgen::assume_compatible_data_layout {};'
new = 'template<>\nstruct fex_gen_config<&drmServerInfo::load_module> : fexgen::callback_member {};'
assert s.count(old) == 1, s.count(old)
iface.write_text(s.replace(old, new, 1))

host = Path('ThunkLibs/libdrm/Host.cpp')
s = host.read_text()
needle = '#include "thunkgen_host_libdrm.inl"\n\n'
assert s.count(needle) == 1, s.count(needle)
impl = r'''// drmSetServerInfo retains its input pointer inside native libdrm. Thunkgen
// has already repacked load_module and finalized its callback_member trampoline
// by the time this custom host implementation runs. Keep only the containing
// object alive here; callback ABI conversion stays generated.
static drmServerInfo retained_server_info {};

static void fexfn_impl_libdrm_drmSetServerInfo(drmServerInfoPtr info) {
  if (!info) {
    fexldr_ptr_libdrm_drmSetServerInfo(nullptr);
    return;
  }

  retained_server_info = *info;
  fexldr_ptr_libdrm_drmSetServerInfo(&retained_server_info);
}

'''
host.write_text(s.replace(needle, needle + impl, 1))

print('generated drmServerInfo::load_module + resident sidecar + retained container applied')
