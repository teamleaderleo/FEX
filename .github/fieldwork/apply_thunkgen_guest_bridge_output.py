#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise SystemExit(f"{label}: expected one anchor, got {text.count(old)}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    interface = root / "ThunkLibs/Generator/interface.h"
    main_cpp = root / "ThunkLibs/Generator/main.cpp"
    gen_cpp = root / "ThunkLibs/Generator/gen.cpp"
    guest_cmake = root / "ThunkLibs/GuestLibs/CMakeLists.txt"

    replace_once(
        interface,
        'struct OutputFilenames {\n  std::string host;\n  std::string guest;\n};',
        'struct OutputFilenames {\n  std::string host;\n  std::string guest;\n  std::string guest_bridge;\n};',
        'OutputFilenames')

    replace_once(
        main_cpp,
        '  } else if (target_abi == "-guest") {\n    output_filenames.guest = std::move(output_filename);\n  } else {',
        '  } else if (target_abi == "-guest") {\n    output_filenames.guest = std::move(output_filename);\n  } else if (target_abi == "-guest-bridge") {\n    output_filenames.guest_bridge = std::move(output_filename);\n  } else {',
        'main target switch')
    replace_once(
        main_cpp,
        '    if (target_abi == "-guest") {\n      const char* platform = is_32bit_guest ? "i686-linux-gnu" : "x86_64-linux-gnu";',
        '    if (target_abi == "-guest" || target_abi == "-guest-bridge") {\n      const char* platform = is_32bit_guest ? "i686-linux-gnu" : "x86_64-linux-gnu";',
        'guest include adjustment')

    guest_anchor = '  // Files used guest-side\n  if (!output_filenames.guest.empty()) {'
    bridge_block = r'''  // Minimal guest-side fragment for process-resident bridge companions.
  // This intentionally excludes API packing/public wrappers and emits only
  // signature-specific CallHostFunction adapters plus symbol enumerators.
  if (!output_filenames.guest_bridge.empty()) {
    std::ofstream file(output_filenames.guest_bridge);

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

      file << "  // " << funcptr_signature << "\n";
      auto funcptr_idx = std::distance(thunked_funcptrs.begin(), type_it);
      fmt::print(file, "  MAKE_CALLBACK_THUNK(callback_{}, {}, \"{:#02x}\");\n", funcptr_idx, funcptr_signature, fmt::join(cb_sha256, ", "));
    }

    for (std::size_t namespace_idx = 0; namespace_idx < namespaces.size(); ++namespace_idx) {
      const auto& ns = namespaces[namespace_idx];
      file << "#define FOREACH_" << ns.name << (ns.name.empty() ? "" : "_") << "SYMBOL(EXPAND) \\\n";
      for (auto& symbol : thunked_api) {
        if (symbol.symtable_namespace.value_or(0) == namespace_idx) {
          file << "  EXPAND(" << symbol.function_name << ", \"TODO\") \\\n";
        }
      }
      file << "\n";
    }
  }

'''
    replace_once(gen_cpp, guest_anchor, bridge_block + guest_anchor, 'guest bridge emit block')

    cmake_anchor = '''  add_custom_command(\n    OUTPUT "${OUTFILE}"\n    DEPENDS "${GENERATOR_EXE}"\n    DEPENDS "${SOURCE_FILE}"\n    COMMAND "${GENERATOR_EXE}" "${SOURCE_FILE}" "${NAME}" "-guest" "${OUTFILE}" "${X86_DEV_ROOTFS}" ${BITNESS_FLAGS} -- -std=c++20 ${BITNESS_FLAGS2}\n      # Expand compile definitions to space-separated list of -D parameters\n      "$<$<BOOL:${compile_prop}>:;-D$<JOIN:${compile_prop},;-D>>"\n      # Expand include directories to space-separated list of -isystem parameters\n      "$<$<BOOL:${prop}>:;-isystem$<JOIN:${prop},;-isystem>>"\n    VERBATIM\n    COMMAND_EXPAND_LISTS)\n\n  list(APPEND OUTPUTS "${OUTFILE}")\n'''
    cmake_repl = cmake_anchor + r'''  set(BRIDGE_OUTFILE "${OUTFOLDER}/thunkgen_bridge_${NAME}.inl")
  add_custom_command(
    OUTPUT "${BRIDGE_OUTFILE}"
    DEPENDS "${GENERATOR_EXE}"
    DEPENDS "${SOURCE_FILE}"
    COMMAND "${GENERATOR_EXE}" "${SOURCE_FILE}" "${NAME}" "-guest-bridge" "${BRIDGE_OUTFILE}" "${X86_DEV_ROOTFS}" ${BITNESS_FLAGS} -- -std=c++20 ${BITNESS_FLAGS2}
      "$<$<BOOL:${compile_prop}>:;-D$<JOIN:${compile_prop},;-D>>"
      "$<$<BOOL:${prop}>:;-isystem$<JOIN:${prop},;-isystem>>"
    VERBATIM
    COMMAND_EXPAND_LISTS)
  add_custom_target(${NAME}-guest-bridge-gen DEPENDS "${BRIDGE_OUTFILE}")

'''
    replace_once(guest_cmake, cmake_anchor, cmake_repl, 'GuestLibs generate command')

    print('Applied thunkgen -guest-bridge prototype')


if __name__ == '__main__':
    main()
