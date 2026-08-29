#!/usr/bin/env python3
"""Keep the instance-create pNext copier synchronized with Vulkan XML."""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


CASE_PATTERN = re.compile(r"case (VK_STRUCTURE_TYPE_[A-Z0-9_]+):")
REJECTED_INSTANCE_TYPES = {"VK_STRUCTURE_TYPE_DIRECT_DRIVER_LOADING_LIST_LUNARG"}


def xml_inventory(source_root: Path) -> dict[str, str]:
    registry = source_root / "External/Vulkan-Headers/registry/vk.xml"
    root = ET.parse(registry).getroot()
    result: dict[str, str] = {}

    for type_element in root.findall("./types/type"):
        if type_element.get("category") != "struct":
            continue
        if "VkInstanceCreateInfo" not in (type_element.get("structextends") or "").split(","):
            continue
        if type_element.get("api") and "vulkan" not in type_element.get("api", "").split(","):
            continue

        type_name = type_element.get("name")
        stype = type_element.find("./member[@values]")
        if not type_name or stype is None or stype.findtext("name") != "sType":
            raise RuntimeError(f"cannot recover sType for {type_name or '<unnamed>'}")
        result[stype.get("values", "")] = type_name

    return result


def source_inventory(host: str) -> tuple[set[str], set[str], str]:
    start = host.index("static bool CopyInstanceCreatePNextChain")
    end = host.index("\n}\n#endif", start)
    body = host[start:end]
    copied = set(CASE_PATTERN.findall(body))
    rejected = {
        structure_type
        for structure_type in REJECTED_INSTANCE_TYPES
        if f"vk_struct->sType == {structure_type}" in host
    }
    return copied, rejected, body


def main() -> int:
    if len(sys.argv) not in (2, 3):
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} SOURCE_ROOT [HOST_CPP]")

    source_root = Path(sys.argv[1]).resolve()
    host_path = Path(sys.argv[2]) if len(sys.argv) == 3 else source_root / "ThunkLibs/libvulkan/Host.cpp"
    host = host_path.read_text(encoding="utf-8")
    guest = (source_root / "ThunkLibs/libvulkan/Guest.cpp").read_text(encoding="utf-8")
    interface = (source_root / "ThunkLibs/libvulkan/libvulkan_interface.cpp").read_text(encoding="utf-8")
    expected = xml_inventory(source_root)
    copied, rejected, body = source_inventory(host)
    expected_cases = set(expected)
    handled = copied | rejected
    missing = sorted(expected_cases - handled)
    extra = sorted(handled - expected_cases)
    overlap = sorted(copied & rejected)

    print(f"Vulkan XML instance pNext types: {len(expected_cases)}")
    print(f"Host copy cases: {len(copied)}")
    print(f"Explicitly rejected cases: {len(rejected)}")
    print("missing: " + (", ".join(f"{case} ({expected[case]})" for case in missing) or "(none)"))
    print("extra: " + (", ".join(extra) or "(none)"))
    print("copied/rejected overlap: " + (", ".join(overlap) or "(none)"))

    callback_substitutions = (
        "copy.pfnCallback = DummyVkDebugReportCallback;",
        "copy.pfnUserCallback = DummyVkDebugUtilsMessengerCallback;",
    )
    absent_substitutions = [line for line in callback_substitutions if line not in body]
    if absent_substitutions:
        print("missing callback substitutions: " + ", ".join(absent_substitutions))

    unsupported_extension_guards = (
        "VK_LUNARG_direct_driver_loading is unsupported across the FEX Vulkan ISA boundary",
    )
    guest_extension_guards = (
        "strcmp(property.extensionName, VK_LUNARG_DIRECT_DRIVER_LOADING_EXTENSION_NAME) == 0",
        'a_1 == std::string_view {"vkEnumerateInstanceExtensionProperties"}',
    )
    absent_guards = [guard for guard in unsupported_extension_guards if guard not in host]
    absent_guards.extend(guard for guard in guest_extension_guards if guard not in guest)
    custom_enumerator = (
        "struct fex_gen_config<vkEnumerateInstanceExtensionProperties> : fexgen::custom_guest_entrypoint {};"
    )
    if custom_enumerator not in interface:
        absent_guards.append(custom_enumerator)
    if absent_guards:
        print("missing unsupported-extension guards: " + ", ".join(absent_guards))

    return 1 if missing or extra or overlap or absent_substitutions or absent_guards else 0


if __name__ == "__main__":
    raise SystemExit(main())
