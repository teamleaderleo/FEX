#!/usr/bin/env python3
from pathlib import Path


def once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor in {path}, found {n}")
    path.write_text(text.replace(old, new, 1))


root = Path('.').resolve()
gen = root / 'ThunkLibs/Generator/gen.cpp'
extractor = root / 'ThunkLibs/Generator/extract_guest_bridge.py'

# Thunkgen already knows which API parameters are native->guest callbacks.
# Convert those typed callback identities into the same signature hash used by
# MAKE_CALLBACK_THUNK before the existing SHA-based deduplication occurs.
once(
    gen,
    '''    // Guest->Host transition points for invoking runtime host-function pointers based on their signature
    std::vector<std::vector<unsigned char>> sha256s;
    for (auto type_it = thunked_funcptrs.begin(); type_it != thunked_funcptrs.end(); ++type_it) {
''',
    '''    // Guest->Host transition points for invoking runtime host-function pointers based on their signature.
    //
    // Keep a separate typed set for signatures that are also used in the
    // native->guest callback direction. A single function-pointer signature may
    // be registered under more than one thunked_funcptrs key, so classify by
    // the stable callback SHA rather than whichever unordered-map entry wins
    // the later duplicate-signature iteration.
    std::vector<std::vector<unsigned char>> resident_callback_sha256s;
    for (const auto& thunk : thunks) {
      for (const auto& [param_idx, callback] : thunk.callbacks) {
        if (callback.is_stub) {
          continue;
        }
        const auto callback_key = thunk.decl->getNameAsString() + "_cb" + std::to_string(param_idx);
        const auto callback_it = thunked_funcptrs.find(callback_key);
        if (callback_it == thunked_funcptrs.end()) {
          continue;
        }
        const auto* callback_type = callback_it->second.first;
        const std::string callback_signature = clang::QualType {callback_type, 0}.getAsString();
        auto callback_sha256 = get_sha256("fexcallback_" + callback_signature, false);
        if (std::find(resident_callback_sha256s.begin(), resident_callback_sha256s.end(), callback_sha256) ==
            resident_callback_sha256s.end()) {
          resident_callback_sha256s.push_back(std::move(callback_sha256));
        }
      }
    }

    std::vector<std::vector<unsigned char>> sha256s;
    for (auto type_it = thunked_funcptrs.begin(); type_it != thunked_funcptrs.end(); ++type_it) {
''',
    'typed callback signature set',
)

once(
    gen,
    '''      // Thunk used for guest-side calls to host function pointers
      file << "  // " << funcptr_signature << "\\n";
      auto funcptr_idx = std::distance(thunked_funcptrs.begin(), type_it);
      fmt::print(file, "  MAKE_CALLBACK_THUNK(callback_{}, {}, \\\"{:#02x}\\\");\\n", funcptr_idx, funcptr_signature, fmt::join(cb_sha256, ", "));
''',
    '''      // Thunk used for guest-side calls to host function pointers.
      // Mark signatures that also require a native->guest CallbackUnpack so a
      // resident bridge can emit the two directional helper sets separately.
      file << "  // " << funcptr_signature << "\\n";
      if (std::find(resident_callback_sha256s.begin(), resident_callback_sha256s.end(), cb_sha256) !=
          resident_callback_sha256s.end()) {
        file << "  // FEX_RESIDENT_CALLBACK_DIRECTION\\n";
      }
      auto funcptr_idx = std::distance(thunked_funcptrs.begin(), type_it);
      fmt::print(file, "  MAKE_CALLBACK_THUNK(callback_{}, {}, \\\"{:#02x}\\\");\\n", funcptr_idx, funcptr_signature, fmt::join(cb_sha256, ", "));
''',
    'generated callback-direction marker',
)

# Replace the proof extractor with a directional version. MAKE_CALLBACK_THUNK
# definitions remain present for all indirect signatures because resident
# GetCallerForHostFunction adapters still need them. CallbackUnpack instances
# are emitted only for signatures thunkgen marked from typed callback metadata.
extractor.write_text(r'''#!/usr/bin/env python3

import argparse
import re
from pathlib import Path

CALLBACK_RE = re.compile(r'^\s*MAKE_CALLBACK_THUNK\(callback_(\d+),\s*(.*),\s*"(.*)"\);\s*$')
CALLBACK_DIRECTION_MARKER = 'FEX_RESIDENT_CALLBACK_DIRECTION'


def parse_callbacks(text: str):
    callbacks = []
    callback_direction = False
    for line in text.splitlines():
        if CALLBACK_DIRECTION_MARKER in line:
            callback_direction = True
            continue
        match = CALLBACK_RE.match(line)
        if match:
            callbacks.append((int(match.group(1)), match.group(2).strip(), match.group(3), callback_direction))
            callback_direction = False
    if not callbacks:
        raise RuntimeError("No MAKE_CALLBACK_THUNK entries found in generated guest thunk output")
    return callbacks


def clean_prefix(prefix: str) -> str:
    result = re.sub(r'[^A-Za-z0-9_]', '_', prefix)
    if not result or result[0].isdigit():
        result = "bridge_" + result
    return result


def write_bridge(path: Path, callbacks, prefix: str):
    lines = [
        "// Generated by extract_guest_bridge.py. Do not edit.",
        "",
    ]
    for index, signature, hash_bytes, _ in callbacks:
        lines.append(f"// {signature}")
        lines.append(f'MAKE_CALLBACK_THUNK(callback_{index}, {signature}, "{hash_bytes}");')
    lines.append("")

    for index, signature, _, needs_unpacker in callbacks:
        lines += [
            f"using fex_bridge_signature_{index} = {signature};",
            f'extern "C" uintptr_t fex_bridge_{prefix}_invoker_{index}() {{',
            f"  return reinterpret_cast<uintptr_t>(GetCallerForHostFunction((fex_bridge_signature_{index}*)nullptr));",
            "}",
        ]
        if needs_unpacker:
            lines += [
                f'extern "C" uintptr_t fex_bridge_{prefix}_unpacker_{index}() {{',
                f"  return reinterpret_cast<uintptr_t>(CallbackUnpack<fex_bridge_signature_{index}>::Unpack);",
                "}",
            ]
        lines.append("")
    path.write_text("\n".join(lines))


def write_accessors(path: Path, callbacks, prefix: str):
    lines = [
        "// Generated by extract_guest_bridge.py. Do not edit.",
        "#pragma once",
        "#include <cstdint>",
        "",
        "template<typename Signature>",
        "struct FEXResidentBridgeInvoker;",
        "template<typename Signature>",
        "struct FEXResidentBridgeUnpacker;",
        "",
    ]

    for index, signature, _, needs_unpacker in callbacks:
        lines += [
            f'extern "C" uintptr_t fex_bridge_{prefix}_invoker_{index}();',
            f"using fex_bridge_accessor_signature_{index} = {signature};",
            f"template<> struct FEXResidentBridgeInvoker<fex_bridge_accessor_signature_{index}> {{",
            f"  static fex_bridge_accessor_signature_{index}* Get() {{",
            f"    return reinterpret_cast<fex_bridge_accessor_signature_{index}*>(fex_bridge_{prefix}_invoker_{index}());",
            "  }",
            "};",
        ]
        if needs_unpacker:
            lines += [
                f'extern "C" uintptr_t fex_bridge_{prefix}_unpacker_{index}();',
                f"template<> struct FEXResidentBridgeUnpacker<fex_bridge_accessor_signature_{index}> {{",
                "  static void (*Get())(uintptr_t, void*) {",
                f"    return reinterpret_cast<void (*)(uintptr_t, void*)>(fex_bridge_{prefix}_unpacker_{index}());",
                "  }",
                "};",
            ]
        lines.append("")

    lines += [
        "template<typename Result, typename... Args>",
        "static Result (*FEXGetResidentCallerForHostFunction(Result (*)(Args...)))(Args...) {",
        "  return FEXResidentBridgeInvoker<Result(Args...)>::Get();",
        "}",
        "",
        "template<typename Result, typename... Args>",
        "static void (*FEXGetResidentCallbackUnpacker(Result (*)(Args...)))(uintptr_t, void*) {",
        "  return FEXResidentBridgeUnpacker<Result(Args...)>::Get();",
        "}",
        "",
        "template<typename Target>",
        "static Target* FEXAllocateResidentHostTrampolineForGuestFunction(Target* GuestTarget) {",
        "  return AllocateHostTrampolineForGuestFunction(FEXGetResidentCallbackUnpacker(GuestTarget), GuestTarget);",
        "}",
        "",
    ]
    path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("guest_inl", type=Path)
    parser.add_argument("bridge_inl", type=Path)
    parser.add_argument("accessors_inl", type=Path)
    parser.add_argument("--prefix", default="libvulkan")
    args = parser.parse_args()

    prefix = clean_prefix(args.prefix)
    callbacks = parse_callbacks(args.guest_inl.read_text())
    args.bridge_inl.parent.mkdir(parents=True, exist_ok=True)
    args.accessors_inl.parent.mkdir(parents=True, exist_ok=True)
    write_bridge(args.bridge_inl, callbacks, prefix)
    write_accessors(args.accessors_inl, callbacks, prefix)
    unpacker_count = sum(1 for *_, needs_unpacker in callbacks if needs_unpacker)
    print(f"extracted {len(callbacks)} resident host-call signatures and {unpacker_count} resident callback signatures for {prefix}")


if __name__ == "__main__":
    main()
''')

print('Applied typed directional resident-bridge prototype')
