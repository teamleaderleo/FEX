#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")

    root = Path(sys.argv[1]).resolve()
    host = root / "ThunkLibs/libvulkan/Host.cpp"
    text = host.read_text()

    required_impls = (
        "FEXFN_IMPL(vkCreateDebugReportCallbackEXT)",
        "FEXFN_IMPL(vkDestroyDebugReportCallbackEXT)",
    )
    for impl in required_impls:
        if impl not in text:
            raise SystemExit(f"required custom implementation missing: {impl}")

    create_route = '''  } else if (a_1 == "vkCreateDebugReportCallbackEXT"sv) {\n    return (PFN_vkVoidFunction)fexfn_impl_libvulkan_vkCreateDebugReportCallbackEXT;\n'''
    destroy_route = '''  } else if (a_1 == "vkDestroyDebugReportCallbackEXT"sv) {\n    return (PFN_vkVoidFunction)fexfn_impl_libvulkan_vkDestroyDebugReportCallbackEXT;\n'''

    if create_route in text or destroy_route in text:
        raise SystemExit("debug-report dynamic lookup routing already present or partially applied")

    anchor = '''  } else if (a_1 == "vkAcquireXlibDisplayEXT"sv) {\n'''
    if text.count(anchor) != 1:
        raise SystemExit(f"expected one Vulkan lookup insertion anchor, found {text.count(anchor)}")

    text = text.replace(anchor, create_route + destroy_route + anchor, 1)
    host.write_text(text)

    verify = host.read_text()
    if verify.count('a_1 == "vkCreateDebugReportCallbackEXT"sv') != 1:
        raise SystemExit("create debug-report route verification failed")
    if verify.count('a_1 == "vkDestroyDebugReportCallbackEXT"sv') != 1:
        raise SystemExit("destroy debug-report route verification failed")

    print("Added Vulkan debug-report create/destroy dynamic lookup routes")


if __name__ == "__main__":
    main()
