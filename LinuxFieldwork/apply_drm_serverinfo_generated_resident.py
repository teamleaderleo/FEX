from pathlib import Path


def replace_one(path: str, old: str, new: str) -> None:
    p = Path(path)
    s = p.read_text()
    count = s.count(old)
    assert count == 1, (path, count, old[:120])
    p.write_text(s.replace(old, new, 1))


# Start from the proven generic callback_member generator plus automatically
# derived resident per-library bridge.
base = Path('LinuxFieldwork/apply_nested_callback_resident_bridge.py').read_text()
exec(compile(base, 'apply_nested_callback_resident_bridge.py', 'exec'))

# Research escape policy for callback-bearing records that contain a callback
# whose ABI cannot use the generic callback path. callback_member_null marks a
# function-pointer member as generated-handled but deliberately suppresses it
# in the temporary guest copy. This keeps raw guest function pointers from
# escaping while letting ordinary callback_member siblings remain generated.
replace_one(
    'ThunkLibs/include/common/GeneratorInterface.h',
    'struct callback_member {};\n',
    '''struct callback_member {};\n// Research-only exceptional callback policy: suppress this field in generated\n// temporary copies instead of emitting a trampoline for an unsupported ABI.\nstruct callback_member_null {};\n''')

replace_one(
    'ThunkLibs/Generator/analysis.h',
    '''    std::unordered_set<std::string> custom_repacked_members;\n    std::unordered_set<std::string> callback_members;\n\n    bool UsesCustomRepackFor(const clang::FieldDecl* member) const {\n''',
    '''    std::unordered_set<std::string> custom_repacked_members;\n    std::unordered_set<std::string> callback_members;\n    std::unordered_set<std::string> null_callback_members;\n\n    bool UsesCustomRepackFor(const clang::FieldDecl* member) const {\n''')
replace_one(
    'ThunkLibs/Generator/analysis.h',
    '''    bool UsesCallbackMemberFor(const clang::FieldDecl* member) const {\n      return callback_members.contains(member->getNameAsString());\n    }\n    bool HasManualCustomRepack() const {\n      for (const auto& member : custom_repacked_members) {\n        if (!callback_members.contains(member)) {\n          return true;\n        }\n''',
    '''    bool UsesCallbackMemberFor(const clang::FieldDecl* member) const {\n      return callback_members.contains(member->getNameAsString());\n    }\n    bool UsesNullCallbackMemberFor(const clang::FieldDecl* member) const {\n      return null_callback_members.contains(member->getNameAsString());\n    }\n    bool HasManualCustomRepack() const {\n      for (const auto& member : custom_repacked_members) {\n        if (!callback_members.contains(member) && !null_callback_members.contains(member)) {\n          return true;\n        }\n''')

replace_one(
    'ThunkLibs/Generator/analysis.cpp',
    '''          if (member_annotation != "fexgen::custom_repack" && member_annotation != "fexgen::callback_member") {\n            throw report_error(template_arg_loc, "Unsupported member annotation(s)");\n          }\n''',
    '''          if (member_annotation != "fexgen::custom_repack" && member_annotation != "fexgen::callback_member" &&\n              member_annotation != "fexgen::callback_member_null") {\n            throw report_error(template_arg_loc, "Unsupported member annotation(s)");\n          }\n''')
replace_one(
    'ThunkLibs/Generator/analysis.cpp',
    '''          if (member_annotation == "fexgen::callback_member") {\n            if (!annotated_member->getType()->isFunctionPointerType()) {\n              throw report_error(template_arg_loc, "callback_member requires a function-pointer field");\n            }\n            auto funcptr = annotated_member->getType()->getPointeeType()->getAs<clang::FunctionProtoType>();\n            if (!funcptr) {\n              throw report_error(template_arg_loc, "callback_member requires a prototype function-pointer field");\n            }\n            if (funcptr->isVariadic()) {\n              throw report_error(template_arg_loc, "Variadic callback members are not supported by this prototype");\n            }\n            repack_info_it->second.callback_members.insert(member_name);\n            thunked_funcptrs["callback_member_" + annotated_member->getQualifiedNameAsString()] =\n              std::pair {context.getCanonicalType(funcptr), no_param_annotations};\n          }\n''',
    '''          if (member_annotation == "fexgen::callback_member" || member_annotation == "fexgen::callback_member_null") {\n            if (!annotated_member->getType()->isFunctionPointerType()) {\n              throw report_error(template_arg_loc, "callback member annotations require a function-pointer field");\n            }\n            auto funcptr = annotated_member->getType()->getPointeeType()->getAs<clang::FunctionProtoType>();\n            if (!funcptr) {\n              throw report_error(template_arg_loc, "callback member annotations require a prototype function-pointer field");\n            }\n            if (member_annotation == "fexgen::callback_member") {\n              if (funcptr->isVariadic()) {\n                throw report_error(template_arg_loc, "Variadic callback members are not supported by this prototype");\n              }\n              repack_info_it->second.callback_members.insert(member_name);\n              thunked_funcptrs["callback_member_" + annotated_member->getQualifiedNameAsString()] =\n                std::pair {context.getCanonicalType(funcptr), no_param_annotations};\n            } else {\n              repack_info_it->second.null_callback_members.insert(member_name);\n            }\n          }\n''')

replace_one(
    'ThunkLibs/Generator/gen.cpp',
    '''        for (auto* member : struct_decl->fields()) {\n          if (!callback_struct->UsesCallbackMemberFor(member)) {\n            continue;\n          }\n          auto member_name = member->getNameAsString();\n          fmt::print(file, "    fex_callback_copy_{}.{} = AllocateHostTrampolineForGuestFunction(a_{}->{});\\n", idx, member_name, idx,\n                     member_name);\n        }\n''',
    '''        for (auto* member : struct_decl->fields()) {\n          auto member_name = member->getNameAsString();\n          if (callback_struct->UsesCallbackMemberFor(member)) {\n            fmt::print(file, "    fex_callback_copy_{}.{} = AllocateHostTrampolineForGuestFunction(a_{}->{});\\n", idx, member_name, idx,\n                       member_name);\n          } else if (callback_struct->UsesNullCallbackMemberFor(member)) {\n            fmt::print(file, "    fex_callback_copy_{}.{} = nullptr;\\n", idx, member_name);\n          }\n        }\n''')

iface = Path('ThunkLibs/libdrm/libdrm_interface.cpp')
s = iface.read_text()
old = 'template<>\nstruct fex_gen_config<drmSetServerInfo> {};'
new = 'template<>\nstruct fex_gen_config<drmSetServerInfo> : fexgen::custom_host_impl {};'
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new, 1)

old = 'template<>\nstruct fex_gen_type<drmServerInfo> : fexgen::assume_compatible_data_layout {};'
new = '''template<>\nstruct fex_gen_config<&drmServerInfo::debug_print> : fexgen::callback_member_null {};\ntemplate<>\nstruct fex_gen_config<&drmServerInfo::load_module> : fexgen::callback_member {};\ntemplate<>\nstruct fex_gen_config<&drmServerInfo::get_perms> : fexgen::callback_member_null {};'''
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

print('generated load_module + null exceptional callbacks + resident sidecar + retained container applied')
