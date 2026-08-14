#!/usr/bin/env python3

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one replacement anchor, found {count}")
    path.write_text(text.replace(old, new, 1))


root = Path(__file__).resolve().parents[2]

# One guest thunkgen invocation may now expose two additional generated files:
# the resident bridge definitions and typed accessors used by the unloadable wrapper.
replace_once(
    root / "ThunkLibs/Generator/interface.h",
    '''struct OutputFilenames {
  std::string host;
  std::string guest;
};''',
    '''struct OutputFilenames {
  std::string host;
  std::string guest;
  std::string guest_bridge;
  std::string guest_bridge_accessors;
};''',
)

replace_once(
    root / "ThunkLibs/Generator/main.cpp",
    '''  if (target_abi == "-host") {
    output_filenames.host = std::move(output_filename);
  } else if (target_abi == "-guest") {
    output_filenames.guest = std::move(output_filename);
  } else {
    std::cerr << "Unrecognized generator target ABI \\"" << target_abi << "\\"\\n";
    return EXIT_FAILURE;
  }
''',
    '''  if (target_abi == "-host") {
    output_filenames.host = std::move(output_filename);
  } else if (target_abi == "-guest") {
    output_filenames.guest = std::move(output_filename);
  } else if (target_abi == "-guest-resident") {
    output_filenames.guest = output_filename;
    if (!output_filename.ends_with(".inl")) {
      std::cerr << "Resident guest output filename must end in .inl\\n";
      return EXIT_FAILURE;
    }
    output_filename.resize(output_filename.size() - 4);
    output_filenames.guest_bridge = output_filename + "_bridge.inl";
    output_filenames.guest_bridge_accessors = output_filename + "_bridge_accessors.inl";
  } else {
    std::cerr << "Unrecognized generator target ABI \\"" << target_abi << "\\"\\n";
    return EXIT_FAILURE;
  }
''',
)

gen = root / "ThunkLibs/Generator/gen.cpp"

replace_once(
    gen,
    '''  auto get_callback_name = [](std::string_view function_name, unsigned param_index) -> std::string {
    return fmt::format("{}CBFN{}", function_name, param_index);
  };

  // Files used guest-side
''',
    '''  auto get_callback_name = [](std::string_view function_name, unsigned param_index) -> std::string {
    return fmt::format("{}CBFN{}", function_name, param_index);
  };

  // Compute runtime function-pointer thunk identities once so the ordinary guest
  // output and resident companion share the exact same numbering and signatures.
  struct GuestFuncPtrEntry {
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

  // Files used guest-side
''',
)

replace_once(
    gen,
    '''    // Guest->Host transition points for invoking runtime host-function pointers based on their signature
    std::vector<std::vector<unsigned char>> sha256s;
    for (auto type_it = thunked_funcptrs.begin(); type_it != thunked_funcptrs.end(); ++type_it) {
      auto* type = type_it->second.first;
      std::string funcptr_signature = clang::QualType {type, 0}.getAsString();

      auto cb_sha256 = get_sha256("fexcallback_" + funcptr_signature, false);
      auto it = std::find(sha256s.begin(), sha256s.end(), cb_sha256);
      if (it != sha256s.end()) {
        // TODO: Avoid this ugly way of avoiding duplicates
        continue;
      } else {
        sha256s.push_back(cb_sha256);
      }

      // Thunk used for guest-side calls to host function pointers
      file << "  // " << funcptr_signature << "\\n";
      auto funcptr_idx = std::distance(thunked_funcptrs.begin(), type_it);
      fmt::print(file, "  MAKE_CALLBACK_THUNK(callback_{}, {}, \\"{:#02x}\\");\\n", funcptr_idx, funcptr_signature, fmt::join(cb_sha256, ", "));
    }
''',
    '''    // Guest->Host transition points for invoking runtime host-function pointers based on their signature
    for (const auto& entry : guest_funcptr_entries) {
      file << "  // " << entry.signature << "\\n";
      fmt::print(file, "  MAKE_CALLBACK_THUNK(callback_{}, {}, \\"{:#02x}\\");\\n", entry.index, entry.signature, fmt::join(entry.sha256, ", "));
    }
''',
)

replace_once(
    gen,
    '''  }

  // Files used host-side
''',
    '''  }

  if (!output_filenames.guest_bridge.empty()) {
    std::ofstream file(output_filenames.guest_bridge);
    file << "// Generated by thunkgen resident-output mode. Do not edit.\\n";
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

    for (const auto& entry : guest_funcptr_entries) {
      file << "// " << entry.signature << "\\n";
      fmt::print(file, "MAKE_CALLBACK_THUNK(callback_{}, {}, \\"{:#02x}\\");\\n", entry.index, entry.signature, fmt::join(entry.sha256, ", "));
    }
    file << "\\n";

    for (const auto& entry : guest_funcptr_entries) {
      fmt::print(file, "using fex_bridge_signature_{} = {};\\n", entry.index, entry.signature);
      fmt::print(file, "extern \\"C\\" uintptr_t fex_bridge_{}_invoker_{}() {{\\n", libname, entry.index);
      fmt::print(file, "  return reinterpret_cast<uintptr_t>(GetCallerForHostFunction((fex_bridge_signature_{}*)nullptr));\\n", entry.index);
      file << "}\\n";
      fmt::print(file, "extern \\"C\\" uintptr_t fex_bridge_{}_unpacker_{}() {{\\n", libname, entry.index);
      fmt::print(file, "  return FEXResidentBridgeUnpackerAddress<fex_bridge_signature_{}>();\\n", entry.index);
      file << "}\\n\\n";
    }
  }

  if (!output_filenames.guest_bridge_accessors.empty()) {
    std::ofstream file(output_filenames.guest_bridge_accessors);
    file << "// Generated by thunkgen resident-output mode. Do not edit.\\n";
    file << "#pragma once\\n#include <cstdint>\\n#include <type_traits>\\n\\n";
    file << "template<typename Signature> struct FEXResidentBridgeInvoker;\\n";
    file << "template<typename Signature> struct FEXResidentBridgeUnpacker;\\n";
    file << "template<typename Signature> struct FEXResidentBridgeCanUnpack;\\n";
    file << "template<typename Result, typename... Args>\\n";
    file << "struct FEXResidentBridgeCanUnpack<Result(Args...)> : std::bool_constant<(sizeof...(Args) <= 19 || sizeof...(Args) == 24)> {};\\n\\n";

    for (const auto& entry : guest_funcptr_entries) {
      fmt::print(file, "extern \\"C\\" uintptr_t fex_bridge_{}_invoker_{}();\\n", libname, entry.index);
      fmt::print(file, "extern \\"C\\" uintptr_t fex_bridge_{}_unpacker_{}();\\n", libname, entry.index);
      fmt::print(file, "using fex_bridge_accessor_signature_{} = {};\\n", entry.index, entry.signature);
      fmt::print(file, "template<> struct FEXResidentBridgeInvoker<fex_bridge_accessor_signature_{}> {{\\n", entry.index);
      fmt::print(file, "  static fex_bridge_accessor_signature_{}* Get() {{\\n", entry.index);
      fmt::print(file, "    return reinterpret_cast<fex_bridge_accessor_signature_{}*>(fex_bridge_{}_invoker_{}());\\n", entry.index, libname, entry.index);
      file << "  }\\n};\\n";
      fmt::print(file, "template<> struct FEXResidentBridgeUnpacker<fex_bridge_accessor_signature_{}> {{\\n", entry.index);
      file << "  static void (*Get())(uintptr_t, void*) {\\n";
      fmt::print(file, "    return reinterpret_cast<void (*)(uintptr_t, void*)>(fex_bridge_{}_unpacker_{}());\\n", libname, entry.index);
      file << "  }\\n};\\n\\n";
    }

    file << "template<typename Result, typename... Args>\\n";
    file << "static Result (*FEXGetResidentCallerForHostFunction(Result (*)(Args...)))(Args...) {\\n";
    file << "  return FEXResidentBridgeInvoker<Result(Args...)>::Get();\\n";
    file << "}\\n\\n";
    file << "template<typename Result, typename... Args>\\n";
    file << "static void (*FEXGetResidentCallbackUnpacker(Result (*)(Args...)))(uintptr_t, void*) {\\n";
    file << "  using Signature = Result(Args...);\\n";
    file << "  static_assert(FEXResidentBridgeCanUnpack<Signature>::value, \\"resident bridge callback unpacker requested for unsupported PackedArguments arity\\");\\n";
    file << "  return FEXResidentBridgeUnpacker<Signature>::Get();\\n";
    file << "}\\n\\n";
    file << "template<typename Target>\\n";
    file << "static Target* FEXAllocateResidentHostTrampolineForGuestFunction(Target* GuestTarget) {\\n";
    file << "  return AllocateHostTrampolineForGuestFunction(FEXGetResidentCallbackUnpacker(GuestTarget), GuestTarget);\\n";
    file << "}\\n";
  }

  // Files used host-side
''',
)

cmake = root / "ThunkLibs/GuestLibs/CMakeLists.txt"
replace_once(
    cmake,
    '''  set(OUTFILE "${OUTFOLDER}/thunkgen_guest_${NAME}.inl")

  file(MAKE_DIRECTORY "${OUTFOLDER}")
''',
    '''  set(OUTFILE "${OUTFOLDER}/thunkgen_guest_${NAME}.inl")
  set(GENERATOR_TARGET "-guest")
  set(GENERATOR_OUTPUTS "${OUTFILE}")
  if ("RESIDENT_BRIDGE" IN_LIST ARGN)
    set(GENERATOR_TARGET "-guest-resident")
    string(REGEX REPLACE "\\.inl$" "_bridge.inl" BRIDGE_OUTFILE "${OUTFILE}")
    string(REGEX REPLACE "\\.inl$" "_bridge_accessors.inl" BRIDGE_ACCESSORS_OUTFILE "${OUTFILE}")
    list(APPEND GENERATOR_OUTPUTS "${BRIDGE_OUTFILE}" "${BRIDGE_ACCESSORS_OUTFILE}")
  endif()

  file(MAKE_DIRECTORY "${OUTFOLDER}")
''',
)
replace_once(
    cmake,
    '''  add_custom_command(
    OUTPUT "${OUTFILE}"
    DEPENDS "${GENERATOR_EXE}"
    DEPENDS "${SOURCE_FILE}"
    COMMAND "${GENERATOR_EXE}" "${SOURCE_FILE}" "${NAME}" "-guest" "${OUTFILE}" "${X86_DEV_ROOTFS}" ${BITNESS_FLAGS} -- -std=c++20 ${BITNESS_FLAGS2}
''',
    '''  add_custom_command(
    OUTPUT ${GENERATOR_OUTPUTS}
    DEPENDS "${GENERATOR_EXE}"
    DEPENDS "${SOURCE_FILE}"
    COMMAND "${GENERATOR_EXE}" "${SOURCE_FILE}" "${NAME}" "${GENERATOR_TARGET}" "${OUTFILE}" "${X86_DEV_ROOTFS}" ${BITNESS_FLAGS} -- -std=c++20 ${BITNESS_FLAGS2}
''',
)
replace_once(
    cmake,
    '''  list(APPEND OUTPUTS "${OUTFILE}")
  set(GEN_${NAME} ${OUTPUTS} PARENT_SCOPE)
endfunction()
''',
    '''  list(APPEND OUTPUTS "${OUTFILE}")
  set(GEN_${NAME} ${OUTPUTS} PARENT_SCOPE)
  if ("RESIDENT_BRIDGE" IN_LIST ARGN)
    set(GEN_${NAME}_BRIDGE "${BRIDGE_OUTFILE}" PARENT_SCOPE)
    set(GEN_${NAME}_BRIDGE_ACCESSORS "${BRIDGE_ACCESSORS_OUTFILE}" PARENT_SCOPE)
  endif()
endfunction()
''',
)
replace_once(
    cmake,
    '''generate(libGL ${CMAKE_CURRENT_SOURCE_DIR}/../libGL/libGL_interface.cpp)
target_include_directories_from_pkgconfig(libGL-guest-deps gl)
target_include_directories_from_pkgconfig(libGL-guest-deps "xcb;x11;xrandr;xrender")
add_guest_lib(GL "libGL.so.1")
''',
    '''generate(libGL ${CMAKE_CURRENT_SOURCE_DIR}/../libGL/libGL_interface.cpp RESIDENT_BRIDGE)
target_include_directories_from_pkgconfig(libGL-guest-deps gl)
target_include_directories_from_pkgconfig(libGL-guest-deps "xcb;x11;xrandr;xrender")

add_library(libGL_bridge-guest-deps INTERFACE)
target_link_libraries(libGL_bridge-guest-deps INTERFACE libGL-guest-deps)
set(GEN_libGL_bridge "${GEN_libGL_BRIDGE}")
add_guest_lib(GL_bridge "libfex-GL-bridge.so")
set_target_properties(GL_bridge-guest PROPERTIES OUTPUT_NAME "fex-GL-bridge")
target_link_options(GL_bridge-guest PRIVATE "LINKER:-z,nodelete")

add_guest_lib(GL "libGL.so.1")
target_link_libraries(GL-guest PRIVATE GL_bridge-guest)
''',
)

print("Applied first-class thunkgen resident-output candidate")
