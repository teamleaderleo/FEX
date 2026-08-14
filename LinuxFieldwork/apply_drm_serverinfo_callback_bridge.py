from pathlib import Path

iface = Path('ThunkLibs/libdrm/libdrm_interface.cpp')
s = iface.read_text()
old = 'template<>\nstruct fex_gen_config<drmSetServerInfo> {};'
new = 'template<>\nstruct fex_gen_config<drmSetServerInfo> : fexgen::custom_host_impl, fexgen::custom_guest_entrypoint {};'
assert s.count(old) == 1, s.count(old)
iface.write_text(s.replace(old, new, 1))

guest = Path('ThunkLibs/libdrm/Guest.cpp')
s = guest.read_text()
needle = 'extern "C" {\n'
assert s.count(needle) == 1, s.count(needle)
wrapper = r'''void drmSetServerInfo(drmServerInfoPtr info) {
  if (!info) {
    fexfn_pack_drmSetServerInfo(info);
    return;
  }

  drmServerInfo host_info = *info;
  host_info.load_module = AllocateHostTrampolineForGuestFunction(info->load_module);
  fexfn_pack_drmSetServerInfo(&host_info);
}

'''
guest.write_text(s.replace(needle, needle + wrapper, 1))

host = Path('ThunkLibs/libdrm/Host.cpp')
s = host.read_text()
needle = '#include "thunkgen_host_libdrm.inl"\n\n'
assert s.count(needle) == 1, s.count(needle)
impl = r'''static drmServerInfo retained_server_info {};

static void fexfn_impl_libdrm_drmSetServerInfo(drmServerInfoPtr info) {
  if (!info) {
    fexldr_ptr_libdrm_drmSetServerInfo(nullptr);
    return;
  }

  retained_server_info = *info;
  FinalizeHostTrampolineForGuestFunction(retained_server_info.load_module);
  fexldr_ptr_libdrm_drmSetServerInfo(&retained_server_info);
}

'''
host.write_text(s.replace(needle, needle + impl, 1))
