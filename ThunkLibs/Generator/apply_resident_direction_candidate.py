#!/usr/bin/env python3

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one replacement anchor, found {count}")
    path.write_text(text.replace(old, new, 1))


root = Path(__file__).resolve().parents[2]
analysis_h = root / "ThunkLibs/Generator/analysis.h"
analysis_cpp = root / "ThunkLibs/Generator/analysis.cpp"
gen = root / "ThunkLibs/Generator/gen.cpp"

# Analysis already knows which thunked function-pointer entries originate from
# actual host->guest callback parameters. Preserve that fact explicitly instead
# of inferring callback direction later from names or signature arity.
replace_once(
    analysis_h,
    '''  std::unordered_map<std::string, std::pair<const clang::Type*, std::unordered_map<unsigned, ParameterAnnotations>>> thunked_funcptrs;

  std::unordered_map<const clang::Type*, RepackedType> types;
''',
    '''  std::unordered_map<std::string, std::pair<const clang::Type*, std::unordered_map<unsigned, ParameterAnnotations>>> thunked_funcptrs;

  // Subset of thunked_funcptrs that are used as real Host->Guest callbacks.
  // These require a resident CallbackUnpack entrypoint; pure Guest->Host
  // runtime function pointers only require the resident invoker.
  std::unordered_set<std::string> host_to_guest_callback_funcptrs;

  std::unordered_map<const clang::Type*, RepackedType> types;
''',
)

replace_once(
    analysis_cpp,
    '''              data.callbacks.emplace(param_idx, callback);
              if (!callback.is_stub && !data.custom_host_impl) {
                thunked_funcptrs[emitted_function->getNameAsString() + "_cb" + std::to_string(param_idx)] =
                  std::pair {context.getCanonicalType(funcptr), no_param_annotations};
              }
''',
    '''              data.callbacks.emplace(param_idx, callback);
              if (!callback.is_stub && !data.custom_host_impl) {
                const auto callback_key = emitted_function->getNameAsString() + "_cb" + std::to_string(param_idx);
                thunked_funcptrs[callback_key] = std::pair {context.getCanonicalType(funcptr), no_param_annotations};
                host_to_guest_callback_funcptrs.insert(callback_key);
              }
''',
)

# The first-class resident-output candidate has already been applied before this
# script. Aggregate callback-direction use while deduplicating signatures: the
# same signature may appear once as a dynamic host PFN and elsewhere as a true
# callback, in which case its single resident entry must support both directions.
replace_once(
    gen,
    '''  struct GuestFuncPtrEntry {
    std::size_t index;
    std::string signature;
    std::vector<unsigned char> sha256;
  };
  std::vector<GuestFuncPtrEntry> guest_funcptr_entries;
  {
    std::vector<std::vector<unsigned char>> sha256s;
    for (auto type_it = thunked_funcptrs.begin(); type_it != thunked_funcptrs.end(); ++type_it) {
      auto* type = type_it->second.first;
      std::string funcptr_signature = clang::QualType {type, 0}.getAsString();
      auto cb_sha256 = get_sha256("fexcallback_" + funcptr_signature, false);
      auto it = std::find(sha256s.begin(), sha256s.end(), cb_sha256);
      if (it != sha256s.end()) {
        continue;
      }
      sha256s.push_back(cb_sha256);
      guest_funcptr_entries.push_back({
        .index = static_cast<std::size_t>(std::distance(thunked_funcptrs.begin(), type_it)),
        .signature = std::move(funcptr_signature),
        .sha256 = std::move(cb_sha256),
      });
    }
  }
''',
    '''  struct GuestFuncPtrEntry {
    std::size_t index;
    std::string signature;
    std::vector<unsigned char> sha256;
    bool used_as_callback;
  };
  std::vector<GuestFuncPtrEntry> guest_funcptr_entries;
  {
    for (auto type_it = thunked_funcptrs.begin(); type_it != thunked_funcptrs.end(); ++type_it) {
      auto* type = type_it->second.first;
      std::string funcptr_signature = clang::QualType {type, 0}.getAsString();
      auto cb_sha256 = get_sha256("fexcallback_" + funcptr_signature, false);
      const bool used_as_callback = host_to_guest_callback_funcptrs.contains(type_it->first);
      auto existing = std::find_if(guest_funcptr_entries.begin(), guest_funcptr_entries.end(),
                                   [&](const GuestFuncPtrEntry& entry) { return entry.sha256 == cb_sha256; });
      if (existing != guest_funcptr_entries.end()) {
        existing->used_as_callback |= used_as_callback;
        continue;
      }
      guest_funcptr_entries.push_back({
        .index = static_cast<std::size_t>(std::distance(thunked_funcptrs.begin(), type_it)),
        .signature = std::move(funcptr_signature),
        .sha256 = std::move(cb_sha256),
        .used_as_callback = used_as_callback,
      });
    }
  }
''',
)

# Remove the temporary arity gate. Resident CallbackUnpack code is emitted only
# for signatures proven by analysis to be used in callback direction.
replace_once(
    gen,
    '''    file << "// Generated by thunkgen resident-output mode. Do not edit.\\n";
    file << "#include <type_traits>\\n\\n";
    file << "template<typename Signature> struct FEXResidentBridgeCanUnpack;\\n";
    file << "template<typename Result, typename... Args>\\n";
    file << "struct FEXResidentBridgeCanUnpack<Result(Args...)> : std::bool_constant<(sizeof...(Args) <= 19 || sizeof...(Args) == 24)> {};\\n\\n";
    file << "template<typename Signature> static uintptr_t FEXResidentBridgeUnpackerAddress() {\\n";
    file << "  if constexpr (FEXResidentBridgeCanUnpack<Signature>::value) {\\n";
    file << "    return reinterpret_cast<uintptr_t>(CallbackUnpack<Signature>::Unpack);\\n";
    file << "  } else {\\n";
    file << "    return 0;\\n";
    file << "  }\\n";
    file << "}\\n\\n";
''',
    '''    file << "// Generated by thunkgen resident-output mode. Do not edit.\\n\\n";
''',
)

replace_once(
    gen,
    '''      fmt::print(file, "extern \\\"C\\\" uintptr_t fex_bridge_{}_invoker_{}() {{\\n", libname, entry.index);
      fmt::print(file, "  return reinterpret_cast<uintptr_t>(GetCallerForHostFunction((fex_bridge_signature_{}*)nullptr));\\n", entry.index);
      file << "}\\n";
      fmt::print(file, "extern \\\"C\\\" uintptr_t fex_bridge_{}_unpacker_{}() {{\\n", libname, entry.index);
      fmt::print(file, "  return FEXResidentBridgeUnpackerAddress<fex_bridge_signature_{}>();\\n", entry.index);
      file << "}\\n\\n";
''',
    '''      fmt::print(file, "extern \\\"C\\\" uintptr_t fex_bridge_{}_invoker_{}() {{\\n", libname, entry.index);
      fmt::print(file, "  return reinterpret_cast<uintptr_t>(GetCallerForHostFunction((fex_bridge_signature_{}*)nullptr));\\n", entry.index);
      file << "}\\n";
      if (entry.used_as_callback) {
        fmt::print(file, "extern \\\"C\\\" uintptr_t fex_bridge_{}_unpacker_{}() {{\\n", libname, entry.index);
        fmt::print(file, "  return reinterpret_cast<uintptr_t>(CallbackUnpack<fex_bridge_signature_{}>::Unpack);\\n", entry.index);
        file << "}\\n";
      }
      file << "\\n";
''',
)

replace_once(
    gen,
    '''    file << "// Generated by thunkgen resident-output mode. Do not edit.\\n";
    file << "#pragma once\\n#include <cstdint>\\n#include <type_traits>\\n\\n";
    file << "template<typename Signature> struct FEXResidentBridgeInvoker;\\n";
    file << "template<typename Signature> struct FEXResidentBridgeUnpacker;\\n";
    file << "template<typename Signature> struct FEXResidentBridgeCanUnpack;\\n";
    file << "template<typename Result, typename... Args>\\n";
    file << "struct FEXResidentBridgeCanUnpack<Result(Args...)> : std::bool_constant<(sizeof...(Args) <= 19 || sizeof...(Args) == 24)> {};\\n\\n";
''',
    '''    file << "// Generated by thunkgen resident-output mode. Do not edit.\\n";
    file << "#pragma once\\n#include <cstdint>\\n\\n";
    file << "template<typename Signature> struct FEXResidentBridgeInvoker;\\n";
    file << "template<typename Signature> struct FEXResidentBridgeUnpacker;\\n\\n";
''',
)

replace_once(
    gen,
    '''      fmt::print(file, "extern \\\"C\\\" uintptr_t fex_bridge_{}_invoker_{}();\\n", libname, entry.index);
      fmt::print(file, "extern \\\"C\\\" uintptr_t fex_bridge_{}_unpacker_{}();\\n", libname, entry.index);
      fmt::print(file, "using fex_bridge_accessor_signature_{} = {};\\n", entry.index, entry.signature);
      fmt::print(file, "template<> struct FEXResidentBridgeInvoker<fex_bridge_accessor_signature_{}> {{\\n", entry.index);
      fmt::print(file, "  static fex_bridge_accessor_signature_{}* Get() {{\\n", entry.index);
      fmt::print(file, "    return reinterpret_cast<fex_bridge_accessor_signature_{}*>(fex_bridge_{}_invoker_{}());\\n", entry.index, libname, entry.index);
      file << "  }\\n};\\n";
      fmt::print(file, "template<> struct FEXResidentBridgeUnpacker<fex_bridge_accessor_signature_{}> {{\\n", entry.index);
      file << "  static void (*Get())(uintptr_t, void*) {\\n";
      fmt::print(file, "    return reinterpret_cast<void (*)(uintptr_t, void*)>(fex_bridge_{}_unpacker_{}());\\n", libname, entry.index);
      file << "  }\\n};\\n\\n";
''',
    '''      fmt::print(file, "extern \\\"C\\\" uintptr_t fex_bridge_{}_invoker_{}();\\n", libname, entry.index);
      fmt::print(file, "using fex_bridge_accessor_signature_{} = {};\\n", entry.index, entry.signature);
      fmt::print(file, "template<> struct FEXResidentBridgeInvoker<fex_bridge_accessor_signature_{}> {{\\n", entry.index);
      fmt::print(file, "  static fex_bridge_accessor_signature_{}* Get() {{\\n", entry.index);
      fmt::print(file, "    return reinterpret_cast<fex_bridge_accessor_signature_{}*>(fex_bridge_{}_invoker_{}());\\n", entry.index, libname, entry.index);
      file << "  }\\n};\\n";
      if (entry.used_as_callback) {
        fmt::print(file, "extern \\\"C\\\" uintptr_t fex_bridge_{}_unpacker_{}();\\n", libname, entry.index);
        fmt::print(file, "template<> struct FEXResidentBridgeUnpacker<fex_bridge_accessor_signature_{}> {{\\n", entry.index);
        file << "  static void (*Get())(uintptr_t, void*) {\\n";
        fmt::print(file, "    return reinterpret_cast<void (*)(uintptr_t, void*)>(fex_bridge_{}_unpacker_{}());\\n", libname, entry.index);
        file << "  }\\n};\\n";
      }
      file << "\\n";
''',
)

replace_once(
    gen,
    '''    file << "template<typename Result, typename... Args>\\n";
    file << "static void (*FEXGetResidentCallbackUnpacker(Result (*)(Args...)))(uintptr_t, void*) {\\n";
    file << "  using Signature = Result(Args...);\\n";
    file << "  static_assert(FEXResidentBridgeCanUnpack<Signature>::value, \\\"resident bridge callback unpacker requested for unsupported PackedArguments arity\\\");\\n";
    file << "  return FEXResidentBridgeUnpacker<Signature>::Get();\\n";
    file << "}\\n\\n";
''',
    '''    file << "template<typename Result, typename... Args>\\n";
    file << "static void (*FEXGetResidentCallbackUnpacker(Result (*)(Args...)))(uintptr_t, void*) {\\n";
    file << "  return FEXResidentBridgeUnpacker<Result(Args...)>::Get();\\n";
    file << "}\\n\\n";
''',
)

print("Applied direction-aware resident callback candidate")
