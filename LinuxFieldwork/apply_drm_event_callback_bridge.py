from pathlib import Path

iface = Path('ThunkLibs/libdrm/libdrm_interface.cpp')
s = iface.read_text()
old = 'template<>\nstruct fex_gen_config<drmHandleEvent> {};'
new = 'template<>\nstruct fex_gen_config<drmHandleEvent> : fexgen::custom_host_impl, fexgen::custom_guest_entrypoint {};'
assert s.count(old) == 1, s.count(old)
iface.write_text(s.replace(old, new, 1))

guest = Path('ThunkLibs/libdrm/Guest.cpp')
s = guest.read_text()
needle = 'extern "C" {\n'
assert s.count(needle) == 1, s.count(needle)
wrapper = r'''int drmHandleEvent(int fd, drmEventContextPtr evctx) {
  if (!evctx) {
    return fexfn_pack_drmHandleEvent(fd, evctx);
  }

  drmEventContext host_ctx = *evctx;
  host_ctx.vblank_handler = AllocateHostTrampolineForGuestFunction(evctx->vblank_handler);
  if (evctx->version >= 2) {
    host_ctx.page_flip_handler = AllocateHostTrampolineForGuestFunction(evctx->page_flip_handler);
  }
  if (evctx->version >= 3) {
    host_ctx.page_flip_handler2 = AllocateHostTrampolineForGuestFunction(evctx->page_flip_handler2);
  }
  if (evctx->version >= 4) {
    host_ctx.sequence_handler = AllocateHostTrampolineForGuestFunction(evctx->sequence_handler);
  }
  return fexfn_pack_drmHandleEvent(fd, &host_ctx);
}

'''
guest.write_text(s.replace(needle, needle + wrapper, 1))

host = Path('ThunkLibs/libdrm/Host.cpp')
s = host.read_text()
needle = '#include "thunkgen_host_libdrm.inl"\n\n'
assert s.count(needle) == 1, s.count(needle)
impl = r'''static int fexfn_impl_libdrm_drmHandleEvent(int fd, drmEventContextPtr evctx) {
  if (evctx) {
    FinalizeHostTrampolineForGuestFunction(evctx->vblank_handler);
    if (evctx->version >= 2) {
      FinalizeHostTrampolineForGuestFunction(evctx->page_flip_handler);
    }
    if (evctx->version >= 3) {
      FinalizeHostTrampolineForGuestFunction(evctx->page_flip_handler2);
    }
    if (evctx->version >= 4) {
      FinalizeHostTrampolineForGuestFunction(evctx->sequence_handler);
    }
  }
  return fexldr_ptr_libdrm_drmHandleEvent(fd, evctx);
}

'''
host.write_text(s.replace(needle, needle + impl, 1))
