from pathlib import Path

# Add a host-side enumerator generated from the same custom_host_impl metadata
# used to generate the thunk implementation declarations.
gen_path = Path("ThunkLibs/Generator/gen.cpp")
gen = gen_path.read_text()
anchor = '''    EmitLayoutWrappers(context, file, type_compat);

    // Forward declarations for symbols loaded from the native host library
'''
insert = '''    EmitLayoutWrappers(context, file, type_compat);

    // Symbol enumerators for dynamically callable API functions whose host-side
    // implementation is custom. Restrict this to namespaces that participate in
    // generated guest symbol tables and indirect guest calls.
    auto has_custom_host_impl = [&](const ThunkedAPIFunction& api) {
      for (const auto& thunk : thunks) {
        const auto thunk_name = thunk.is_variadic ? thunk.GetOriginalFunctionName() : thunk.function_name;
        if (thunk_name == api.function_name) {
          return thunk.custom_host_impl;
        }
      }
      return false;
    };
    for (std::size_t namespace_idx = 0; namespace_idx < namespaces.size(); ++namespace_idx) {
      const auto& ns = namespaces[namespace_idx];
      if (!ns.generate_guest_symtable || !ns.indirect_guest_calls) {
        continue;
      }

      file << "#define FOREACH_" << ns.name << (ns.name.empty() ? "" : "_") << "CUSTOM_HOST_SYMBOL(EXPAND) \\\\n";
      for (const auto& symbol : thunked_api) {
        if (symbol.symtable_namespace.value_or(0) == namespace_idx && has_custom_host_impl(symbol)) {
          file << "  EXPAND(" << symbol.function_name << ", \\\"TODO\\\") \\\\n";
        }
      }
      file << "\\n";
    }

    // Forward declarations for symbols loaded from the native host library
'''
if gen.count(anchor) != 1:
    raise SystemExit(f"expected one generator host-output anchor, found {gen.count(anchor)}")
gen = gen.replace(anchor, insert, 1)
gen_path.write_text(gen)

# Replace Vulkan's duplicated hand-written custom lookup with the generated
# internal custom-host symbol enumerator. This script is run after V3, so assert
# the three callback names are present before removing the manual list.
host_path = Path("ThunkLibs/libvulkan/Host.cpp")
host = host_path.read_text()
start_marker = "static PFN_vkVoidFunction LookupCustomVulkanFunction(const char* a_1) {"
end_marker = "\nstatic PFN_vkVoidFunction FEXFN_IMPL(vkGetDeviceProcAddr)"
start = host.find(start_marker)
end = host.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("could not locate Vulkan custom lookup function")
old = host[start:end]
for required in (
    "vkCreateDebugReportCallbackEXT",
    "vkDestroyDebugReportCallbackEXT",
    "vkCreateDebugUtilsMessengerEXT",
    "vkCreateInstance",
    "vkCreateDevice",
):
    if required not in old:
        raise SystemExit(f"V3 lookup anchor missing required symbol {required}")

new = '''static PFN_vkVoidFunction LookupCustomVulkanFunction(const char* a_1) {
#define LOOKUP_CUSTOM_HOST(symbol, unused) \\
  if (std::string_view {a_1} == #symbol) { \\
    return reinterpret_cast<PFN_vkVoidFunction>(FEXFN_IMPL(symbol)); \\
  }
  FOREACH_internal_CUSTOM_HOST_SYMBOL(LOOKUP_CUSTOM_HOST)
#undef LOOKUP_CUSTOM_HOST
  return nullptr;
}
'''
host = host[:start] + new + host[end:]
host_path.write_text(host)
