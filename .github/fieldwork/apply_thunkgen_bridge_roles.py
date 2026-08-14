#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, got {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    analysis_h = root / "ThunkLibs/Generator/analysis.h"
    analysis_cpp = root / "ThunkLibs/Generator/analysis.cpp"
    data_layout_cpp = root / "ThunkLibs/Generator/data_layout.cpp"
    gen_cpp = root / "ThunkLibs/Generator/gen.cpp"

    replace_once(
        analysis_h,
        '''  // Set of function types for which to generate Guest->Host thunking trampolines.\n  // The map key is a unique identifier that must be consistent between guest/host processing passes.\n  // The map value is a pair of the function pointer's clang::Type and the mapping of parameter annotations\n  std::unordered_map<std::string, std::pair<const clang::Type*, std::unordered_map<unsigned, ParameterAnnotations>>> thunked_funcptrs;\n''',
        '''  struct ThunkedFuncPtr {\n    const clang::Type* type;\n    std::unordered_map<unsigned, ParameterAnnotations> param_annotations;\n    // Orthogonal bridge roles. Canonical signatures may require either or both.\n    bool needs_caller = false;\n    bool needs_unpacker = false;\n  };\n\n  // Function-pointer bridge registrations. The key preserves source provenance\n  // between guest/host passes; bridge emission later deduplicates canonical\n  // signatures while ORing the role requirements.\n  std::unordered_map<std::string, ThunkedFuncPtr> thunked_funcptrs;\n''',
        "analysis.h thunked_funcptrs")

    replace_once(
        analysis_cpp,
        '''        thunked_funcptrs[type.getAsString()] = std::pair {type.getTypePtr(), no_param_annotations};\n''',
        '''        // Store the function prototype rather than the pointer type so all\n        // bridge consumers see the same representation as generated callbacks.\n        auto funcptr = type->getPointeeType()->getAs<clang::FunctionProtoType>();\n        if (!funcptr) {\n          throw report_error(*decl->getSourceRange().getBegin().getPtrEncoding(), "Expected a prototype function-pointer type");\n        }\n        // Explicit function-pointer type declarations predate directional\n        // bridge roles, so conservatively keep both capabilities.\n        thunked_funcptrs[type.getAsString()] = {context.getCanonicalType(funcptr), no_param_annotations, true, true};\n''',
        "explicit function-pointer registration")
    replace_once(
        analysis_cpp,
        '''                thunked_funcptrs[emitted_function->getNameAsString() + "_cb" + std::to_string(param_idx)] =\n                  std::pair {context.getCanonicalType(funcptr), no_param_annotations};\n''',
        '''                thunked_funcptrs[emitted_function->getNameAsString() + "_cb" + std::to_string(param_idx)] =\n                  {context.getCanonicalType(funcptr), no_param_annotations, false, true};\n''',
        "callback parameter registration")
    replace_once(
        analysis_cpp,
        '''            thunked_funcptrs[emitted_function->getNameAsString()] =\n              std::pair {context.getCanonicalType(emitted_function->getFunctionType()), data.param_annotations};\n''',
        '''            thunked_funcptrs[emitted_function->getNameAsString()] =\n              {context.getCanonicalType(emitted_function->getFunctionType()), data.param_annotations, true, false};\n''',
        "indirect call registration")

    # The nested callback_member prototype may be applied before this transform
    # on CUDA/DRM research branches. Preserve that provenance as unpacker-only.
    analysis_text = analysis_cpp.read_text()
    callback_member_old = '''            thunked_funcptrs["callback_member_" + annotated_member->getQualifiedNameAsString()] =\n              std::pair {context.getCanonicalType(funcptr), no_param_annotations};\n'''
    callback_member_new = '''            thunked_funcptrs["callback_member_" + annotated_member->getQualifiedNameAsString()] =\n              {context.getCanonicalType(funcptr), no_param_annotations, false, true};\n'''
    if callback_member_old in analysis_text:
        analysis_cpp.write_text(analysis_text.replace(callback_member_old, callback_member_new, 1))

    replace_once(
        data_layout_cpp,
        '''    auto& [type, param_annotations] = funcptr_type_it->second;\n    auto func_type = type->getAs<clang::FunctionProtoType>();\n''',
        '''    auto& funcptr_info = funcptr_type_it->second;\n    auto* type = funcptr_info.type;\n    auto func_type = type->getAs<clang::FunctionProtoType>();\n''',
        "data_layout.cpp function-pointer record")

    text = gen_cpp.read_text()
    old = "auto* type = type_it->second.first;"
    if text.count(old) != 2:
        raise SystemExit(f"gen.cpp type access: expected two anchors after bridge-output transform, got {text.count(old)}")
    text = text.replace(old, "auto* type = type_it->second.type;")
    old = '''      auto& [type, param_annotations] = host_funcptr_entry.second;\n      auto func_type = type->getAs<clang::FunctionProtoType>();\n'''
    new = '''      auto& funcptr_info = host_funcptr_entry.second;\n      auto* type = funcptr_info.type;\n      auto& param_annotations = funcptr_info.param_annotations;\n      auto func_type = type->getAs<clang::FunctionProtoType>();\n'''
    if text.count(old) != 1:
        raise SystemExit(f"gen.cpp host funcptr destructure: expected one anchor, got {text.count(old)}")
    text = text.replace(old, new, 1)
    gen_cpp.write_text(text)

    old = r'''    std::vector<std::vector<unsigned char>> sha256s;
    for (auto type_it = thunked_funcptrs.begin(); type_it != thunked_funcptrs.end(); ++type_it) {
      auto* type = type_it->second.type;
      std::string funcptr_signature = clang::QualType {type, 0}.getAsString();

      auto cb_sha256 = get_sha256("fexcallback_" + funcptr_signature, false);
      auto it = std::find(sha256s.begin(), sha256s.end(), cb_sha256);
      if (it != sha256s.end()) {
        continue;
      }
      sha256s.push_back(cb_sha256);

      file << "  // " << funcptr_signature << "\n";
      auto funcptr_idx = std::distance(thunked_funcptrs.begin(), type_it);
      fmt::print(file, "  MAKE_CALLBACK_THUNK(callback_{}, {}, \"{:#02x}\");\n", funcptr_idx, funcptr_signature, fmt::join(cb_sha256, ", "));
    }
'''
    new = r'''    struct BridgeSignature {
      const clang::Type* type;
      std::string signature;
      bool needs_caller;
      bool needs_unpacker;
    };
    std::vector<BridgeSignature> bridge_signatures;
    for (const auto& [source_key, info] : thunked_funcptrs) {
      (void)source_key;
      std::string signature = clang::QualType {info.type, 0}.getAsString();
      auto existing = std::find_if(bridge_signatures.begin(), bridge_signatures.end(),
                                   [&](const BridgeSignature& entry) { return entry.signature == signature; });
      if (existing == bridge_signatures.end()) {
        bridge_signatures.push_back({info.type, std::move(signature), info.needs_caller, info.needs_unpacker});
      } else {
        existing->needs_caller |= info.needs_caller;
        existing->needs_unpacker |= info.needs_unpacker;
      }
    }

    for (std::size_t bridge_idx = 0; bridge_idx < bridge_signatures.size(); ++bridge_idx) {
      const auto& bridge = bridge_signatures[bridge_idx];
      auto cb_sha256 = get_sha256("fexcallback_" + bridge.signature, false);
      std::string symbol_hash;
      for (auto byte : cb_sha256) {
        symbol_hash += fmt::format("{:02x}", byte);
      }

      fmt::print(file, "  // FEX_BRIDGE_ROLE index={} caller={} unpacker={} hash={} {}\n", bridge_idx,
                 bridge.needs_caller ? 1 : 0, bridge.needs_unpacker ? 1 : 0, symbol_hash, bridge.signature);

      if (bridge.needs_caller) {
        fmt::print(file, "  MAKE_CALLBACK_THUNK(callback_{}, {}, \"{:#02x}\");\n", bridge_idx, bridge.signature, fmt::join(cb_sha256, ", "));
        fmt::print(file, "  using fex_bridge_caller_signature_{} = {};\n", bridge_idx, bridge.signature);
        fmt::print(file, "  extern \"C\" uintptr_t fex_bridge_invoker_{}() {{\n", symbol_hash);
        fmt::print(file, "    return reinterpret_cast<uintptr_t>(GetCallerForHostFunction((fex_bridge_caller_signature_{}*)nullptr));\n", bridge_idx);
        file << "  }\n";
      }
      if (bridge.needs_unpacker) {
        fmt::print(file, "  using fex_bridge_unpacker_signature_{} = {};\n", bridge_idx, bridge.signature);
        fmt::print(file, "  extern \"C\" uintptr_t fex_bridge_unpacker_{}() {{\n", symbol_hash);
        fmt::print(file, "    return reinterpret_cast<uintptr_t>(&CallbackUnpack<fex_bridge_unpacker_signature_{}>::Unpack);\n", bridge_idx);
        file << "  }\n";
      }
    }
'''
    replace_once(gen_cpp, old, new, "role-aware guest bridge emission")

    accessor_old = r'''  // Companion declaration fragment. The role-aware transform replaces this
  // placeholder with typed caller/unpacker accessors keyed by canonical
  // signature hash. Keeping this as a separate generator mode avoids parsing
  // generated guest C++ in consumers.
  if (!output_filenames.guest_bridge_accessors.empty()) {
    std::ofstream file(output_filenames.guest_bridge_accessors);
    file << "// thunkgen guest bridge accessors require role-aware emission\\n";
  }
'''
    accessor_new = r'''  if (!output_filenames.guest_bridge_accessors.empty()) {
    std::ofstream file(output_filenames.guest_bridge_accessors);
    file << "// Generated by thunkgen -guest-bridge-accessors. Do not edit.\n";
    file << "#pragma once\n#include <cstdint>\n\n";
    file << "template<typename Signature> struct FEXResidentBridgeInvoker;\n";
    file << "template<typename Signature> struct FEXResidentBridgeUnpacker;\n\n";

    struct BridgeAccessorSignature {
      const clang::Type* type;
      std::string signature;
      bool needs_caller;
      bool needs_unpacker;
    };
    std::vector<BridgeAccessorSignature> bridge_signatures;
    for (const auto& [source_key, info] : thunked_funcptrs) {
      (void)source_key;
      std::string signature = clang::QualType {info.type, 0}.getAsString();
      auto existing = std::find_if(bridge_signatures.begin(), bridge_signatures.end(),
                                   [&](const BridgeAccessorSignature& entry) { return entry.signature == signature; });
      if (existing == bridge_signatures.end()) {
        bridge_signatures.push_back({info.type, std::move(signature), info.needs_caller, info.needs_unpacker});
      } else {
        existing->needs_caller |= info.needs_caller;
        existing->needs_unpacker |= info.needs_unpacker;
      }
    }

    for (std::size_t bridge_idx = 0; bridge_idx < bridge_signatures.size(); ++bridge_idx) {
      const auto& bridge = bridge_signatures[bridge_idx];
      auto cb_sha256 = get_sha256("fexcallback_" + bridge.signature, false);
      std::string symbol_hash;
      for (auto byte : cb_sha256) {
        symbol_hash += fmt::format("{:02x}", byte);
      }
      fmt::print(file, "// FEX_BRIDGE_ROLE caller={} unpacker={} hash={} {}\n",
                 bridge.needs_caller ? 1 : 0, bridge.needs_unpacker ? 1 : 0, symbol_hash, bridge.signature);
      fmt::print(file, "using fex_bridge_accessor_signature_{} = {};\n", bridge_idx, bridge.signature);
      if (bridge.needs_caller) {
        fmt::print(file, "extern \"C\" uintptr_t fex_bridge_invoker_{}();\n", symbol_hash);
        fmt::print(file, "template<> struct FEXResidentBridgeInvoker<fex_bridge_accessor_signature_{}> {{\n", bridge_idx);
        fmt::print(file, "  static fex_bridge_accessor_signature_{}* Get() {{ return reinterpret_cast<fex_bridge_accessor_signature_{}*>(fex_bridge_invoker_{}()); }}\n", bridge_idx, bridge_idx, symbol_hash);
        file << "};\n";
      }
      if (bridge.needs_unpacker) {
        fmt::print(file, "extern \"C\" uintptr_t fex_bridge_unpacker_{}();\n", symbol_hash);
        fmt::print(file, "template<> struct FEXResidentBridgeUnpacker<fex_bridge_accessor_signature_{}> {{\n", bridge_idx);
        fmt::print(file, "  static void (*Get())(uintptr_t, void*) {{ return reinterpret_cast<void (*)(uintptr_t, void*)>(fex_bridge_unpacker_{}()); }}\n", symbol_hash);
        file << "};\n";
      }
      file << "\n";
    }

    file << "template<typename Result, typename... Args>\n";
    file << "static Result (*FEXGetResidentCallerForHostFunction(Result (*)(Args...)))(Args...) {\n";
    file << "  return FEXResidentBridgeInvoker<Result(Args...)>::Get();\n}\n\n";
    file << "template<typename Result, typename... Args>\n";
    file << "static void (*FEXGetResidentCallbackUnpacker(Result (*)(Args...)))(uintptr_t, void*) {\n";
    file << "  return FEXResidentBridgeUnpacker<Result(Args...)>::Get();\n}\n\n";
    file << "template<typename Target>\n";
    file << "static Target* FEXAllocateResidentHostTrampolineForGuestFunction(Target* GuestTarget) {\n";
    file << "  return AllocateHostTrampolineForGuestFunction(FEXGetResidentCallbackUnpacker(GuestTarget), GuestTarget);\n}\n";
  }
'''
    replace_once(gen_cpp, accessor_old, accessor_new, "role-aware bridge accessor emission")

    print("Applied thunkgen caller/unpacker bridge-role and accessor prototype")


if __name__ == "__main__":
    main()
