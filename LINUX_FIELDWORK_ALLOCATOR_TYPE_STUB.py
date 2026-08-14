from pathlib import Path

iface_path = Path("ThunkLibs/libvulkan/libvulkan_interface.cpp")
iface = iface_path.read_text()
old = '''// TODO: Should not be opaque, but it's usually NULL anyway. Supporting the contained function pointers will need more work.
template<>
struct fex_gen_type<VkAllocationCallbacks> : fexgen::opaque_type {};'''
new = '''// Linux Fieldwork experiment: expose VkAllocationCallbacks to the normal
// struct repacker and handle all pointer-bearing members explicitly.
template<>
struct fex_gen_type<VkAllocationCallbacks> : fexgen::emit_layout_wrappers {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pUserData> : fexgen::custom_repack {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnAllocation> : fexgen::custom_repack {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnReallocation> : fexgen::custom_repack {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnFree> : fexgen::custom_repack {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnInternalAllocation> : fexgen::custom_repack {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnInternalFree> : fexgen::custom_repack {};'''
if iface.count(old) != 1:
    raise SystemExit(f"expected one VkAllocationCallbacks opaque annotation, found {iface.count(old)}")
iface_path.write_text(iface.replace(old, new, 1))

host_path = Path("ThunkLibs/libvulkan/Host.cpp")
host = host_path.read_text()
anchor = '#include "thunkgen_host_libvulkan.inl"\n'
if host.count(anchor) != 1:
    raise SystemExit(f"expected one generated-host include, found {host.count(anchor)}")
insert = r'''

[[noreturn]] static void AllocatorCallbackStubFatal(const char* callback) {
  fprintf(stderr, "FEX_ALLOCATOR_STUB callback=%s\n", callback);
  fflush(stderr);
  std::abort();
}

static VKAPI_ATTR void* VKAPI_CALL AllocatorAllocationStub(void*, size_t, size_t, VkSystemAllocationScope) {
  AllocatorCallbackStubFatal("pfnAllocation");
}

static VKAPI_ATTR void* VKAPI_CALL AllocatorReallocationStub(void*, void*, size_t, size_t, VkSystemAllocationScope) {
  AllocatorCallbackStubFatal("pfnReallocation");
}

static VKAPI_ATTR void VKAPI_CALL AllocatorFreeStub(void*, void*) {
  AllocatorCallbackStubFatal("pfnFree");
}

static VKAPI_ATTR void VKAPI_CALL AllocatorInternalAllocationStub(void*, size_t, VkInternalAllocationType, VkSystemAllocationScope) {
  AllocatorCallbackStubFatal("pfnInternalAllocation");
}

static VKAPI_ATTR void VKAPI_CALL AllocatorInternalFreeStub(void*, size_t, VkInternalAllocationType, VkSystemAllocationScope) {
  AllocatorCallbackStubFatal("pfnInternalFree");
}

void fex_custom_repack_entry(host_layout<VkAllocationCallbacks>& into, const guest_layout<VkAllocationCallbacks>& from) {
  into.data.pUserData = from.data.pUserData.force_get_host_pointer();
  into.data.pfnAllocation = from.data.pfnAllocation.data ? AllocatorAllocationStub : nullptr;
  into.data.pfnReallocation = from.data.pfnReallocation.data ? AllocatorReallocationStub : nullptr;
  into.data.pfnFree = from.data.pfnFree.data ? AllocatorFreeStub : nullptr;
  into.data.pfnInternalAllocation = from.data.pfnInternalAllocation.data ? AllocatorInternalAllocationStub : nullptr;
  into.data.pfnInternalFree = from.data.pfnInternalFree.data ? AllocatorInternalFreeStub : nullptr;
}

bool fex_custom_repack_exit(guest_layout<VkAllocationCallbacks>&, const host_layout<VkAllocationCallbacks>&) {
  return false;
}
'''
host_path.write_text(host.replace(anchor, anchor + insert, 1))

# Trace by a distinctive member name rather than relying on the generator's
# spelling of the canonical struct type.
data_path = Path("ThunkLibs/Generator/data_layout.cpp")
data = data_path.read_text()
loop_needle = '''  } else if (guest_struct_info) {
    std::vector<TypeCompatibility> member_compat;
    for (std::size_t member_idx = 0; member_idx < guest_struct_info->members.size(); ++member_idx) {
'''
loop_repl = '''  } else if (guest_struct_info) {
    const bool trace_allocator = std::any_of(guest_struct_info->members.begin(), guest_struct_info->members.end(),
                                             [](const auto& member) { return member.member_name == "pfnAllocation"; });
    if (trace_allocator) {
      fmt::print(stderr, "ALLOC_COMPAT type={} guest_members={} host_members={} custom_members={} initial={}\\n",
                 clang::QualType {type, 0}.getAsString(), guest_struct_info->members.size(),
                 host_info.get_if_struct()->members.size(), types.at(type).custom_repacked_members.size(), static_cast<int>(compat));
    }
    std::vector<TypeCompatibility> member_compat;
    for (std::size_t member_idx = 0; member_idx < guest_struct_info->members.size(); ++member_idx) {
'''
if data.count(loop_needle) != 1:
    raise SystemExit(f"expected one struct loop anchor, found {data.count(loop_needle)}")
data = data.replace(loop_needle, loop_repl, 1)
member_needle = '''      if (types.at(type).UsesCustomRepackFor(host_member_field)) {
        member_compat.push_back(TypeCompatibility::Repackable);
        continue;
'''
member_repl = '''      if (trace_allocator) {
        fmt::print(stderr, "ALLOC_COMPAT member={} guest_type={} host_type={} custom={}\\n",
                   guest_struct_info->members.at(member_idx).member_name,
                   guest_struct_info->members.at(member_idx).type_name,
                   host_member_field->getType().getAsString(),
                   types.at(type).UsesCustomRepackFor(host_member_field));
      }
      if (types.at(type).UsesCustomRepackFor(host_member_field)) {
        member_compat.push_back(TypeCompatibility::Repackable);
        continue;
'''
if data.count(member_needle) != 1:
    raise SystemExit(f"expected one member compatibility anchor, found {data.count(member_needle)}")
data = data.replace(member_needle, member_repl, 1)
result_needle = '''    } else {
      // Downgrade to None
      compat = TypeCompatibility::None;
    }
  }

  type_compat.at(type) = compat;
'''
result_repl = '''    } else {
      // Downgrade to None
      compat = TypeCompatibility::None;
    }
    if (trace_allocator) {
      fmt::print(stderr, "ALLOC_COMPAT result={} member_results=", static_cast<int>(compat));
      for (auto member_result : member_compat) {
        fmt::print(stderr, "{} ", static_cast<int>(member_result));
      }
      fmt::print(stderr, "\\n");
    }
  }

  type_compat.at(type) = compat;
'''
if data.count(result_needle) != 1:
    raise SystemExit(f"expected one result anchor, found {data.count(result_needle)}")
data_path.write_text(data.replace(result_needle, result_repl, 1))
