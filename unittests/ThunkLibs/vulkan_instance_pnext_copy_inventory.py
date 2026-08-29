#!/usr/bin/env python3
"""Keep the instance-create pNext copier synchronized with Vulkan XML."""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


CASE_PATTERN = re.compile(r"case (VK_STRUCTURE_TYPE_[A-Z0-9_]+):")


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


def source_inventory(host_path: Path) -> tuple[set[str], str]:
    host = host_path.read_text(encoding="utf-8")
    start = host.index("static bool CopyInstanceCreatePNextChain")
    end = host.index("\n}\n#endif", start)
    body = host[start:end]
    return set(CASE_PATTERN.findall(body)), body


def main() -> int:
    if len(sys.argv) not in (2, 3):
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} SOURCE_ROOT [HOST_CPP]")

    source_root = Path(sys.argv[1]).resolve()
    host_path = Path(sys.argv[2]) if len(sys.argv) == 3 else source_root / "ThunkLibs/libvulkan/Host.cpp"
    expected = xml_inventory(source_root)
    copied, body = source_inventory(host_path)
    expected_cases = set(expected)
    missing = sorted(expected_cases - copied)
    extra = sorted(copied - expected_cases)

    print(f"Vulkan XML instance pNext types: {len(expected_cases)}")
    print(f"Host copy cases: {len(copied)}")
    print("missing: " + (", ".join(f"{case} ({expected[case]})" for case in missing) or "(none)"))
    print("extra: " + (", ".join(extra) or "(none)"))

    callback_substitutions = (
        "copy.pfnCallback = DummyVkDebugReportCallback;",
        "copy.pfnUserCallback = DummyVkDebugUtilsMessengerCallback;",
    )
    absent_substitutions = [line for line in callback_substitutions if line not in body]
    if absent_substitutions:
        print("missing callback substitutions: " + ", ".join(absent_substitutions))

    return 1 if missing or extra or absent_substitutions else 0


if __name__ == "__main__":
    raise SystemExit(main())
