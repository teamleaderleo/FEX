#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    guest = Path(sys.argv[1]).resolve() / "ThunkLibs/libGL/libGL_Guest.cpp"

    # After the split bridge owns the complete generated symbol->adapter table,
    # retaining the wrapper-local table needlessly keeps references to every
    # wrapper-local generated adapter. Vulkan's successful split removes this
    # equivalent table entirely.
    old_map = '''// Maps OpenGL API function names to the address of a guest function which is\n// linked to the corresponding host function pointer\nconst std::unordered_map<std::string_view, uintptr_t /* guest function address */> HostPtrInvokers = std::invoke([]() {\n#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));\n  std::unordered_map<std::string_view, uintptr_t> Ret;\n  FOREACH_internal_SYMBOL(PAIR);\n  return Ret;\n#undef PAIR\n});\n\n'''
    replace_once(guest, old_map, '', 'HostPtrInvokers')

    old_fallback = '''  auto TargetFuncIt = HostPtrInvokers.find(reinterpret_cast<const char*>(procname));\n  if (TargetFuncIt == HostPtrInvokers.end()) {\n    std::string_view procname_s {reinterpret_cast<const char*>(procname)};\n    // If glXGetProcAddress is querying itself, then we can just return itself.\n    // Some games do this for unknown reasons.\n    if (procname_s == "glXGetProcAddress" || procname_s == "glXGetProcAddressARB") {\n      return reinterpret_cast<voidFunc*>(glXGetProcAddress);\n    }\n\n    // Extension found in host but not in our interface definition => Not fatal but warn about it\n    // Some games query leaked GLES symbols but don't use them\n    // glFrustrumf : ES 1.x function\n    //  - Papers, Please\n    //  - Dicey Dungeons\n    fprintf(stderr, "glXGetProcAddress: not found %s\\n", procname);\n    return nullptr;\n  }\n\n  LinkAddressToFunction((uintptr_t)Ret, TargetFuncIt->second);\n  return Ret;\n'''
    new_fallback = '''  std::string_view procname_s {reinterpret_cast<const char*>(procname)};\n  // If glXGetProcAddress is querying itself, then we can just return itself.\n  if (procname_s == "glXGetProcAddress" || procname_s == "glXGetProcAddressARB") {\n    return reinterpret_cast<voidFunc*>(glXGetProcAddress);\n  }\n\n  // A non-null host function with no resident generated adapter is not safe to\n  // advertise to the guest because its signature is unknown to thunkgen.\n  fprintf(stderr, "glXGetProcAddress: not found %s\\n", procname);\n  return nullptr;\n'''
    replace_once(guest, old_fallback, new_fallback, 'wrapper adapter fallback')

    old_malloc = '''// Wrapper around malloc() without noexcept specifiers\nstatic void* malloc_wrapper(size_t size) {\n  return malloc(size);\n}\n\n'''
    replace_once(guest, old_malloc, '', 'wrapper malloc target')

    print('Pruned wrapper-local GL adapter map and malloc target')


if __name__ == '__main__':
    main()
