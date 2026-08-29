#!/usr/bin/env python3
"""Keep Vulkan custom repacker copyback and const cleanup ownership separate."""

from __future__ import annotations

import re
import sys
from pathlib import Path


FUNCTION_PATTERN = re.compile(
    r"(?P<return>void|bool) fex_custom_repack_(?P<kind>entry|exit|cleanup)\("
    r"(?:const )?(?:host_layout|guest_layout)<(?P<type>Vk[A-Za-z0-9_]+)>&[^)]*\)\s*\{"
)
GENERIC_ENTRY = "default_fex_custom_repack_entry(into, from);"
GENERIC_REVERSE = "default_fex_custom_repack_reverse_pnext(into, from);"
GENERIC_CLEANUP = "default_fex_custom_repack_cleanup("


def function_bodies(source: str) -> dict[tuple[str, str], str]:
    bodies: dict[tuple[str, str], str] = {}
    for match in FUNCTION_PATTERN.finditer(source):
        depth = 1
        cursor = match.end()
        while cursor < len(source) and depth:
            if source[cursor] == "{":
                depth += 1
            elif source[cursor] == "}":
                depth -= 1
            cursor += 1
        if depth:
            raise RuntimeError(f"unterminated {match.group('kind')} for {match.group('type')}")
        key = (match.group("kind"), match.group("type"))
        if key in bodies:
            raise RuntimeError(f"duplicate {key[0]} for {key[1]}")
        bodies[key] = source[match.end() : cursor - 1]
    return bodies


def missing_owners(source: str) -> tuple[list[str], list[str], list[str]]:
    bodies = function_bodies(source)
    entry_types = sorted(
        type_name
        for (kind, type_name), body in bodies.items()
        if kind == "entry" and GENERIC_ENTRY in body
    )
    missing_exit = [
        type_name
        for type_name in entry_types
        if GENERIC_REVERSE not in bodies.get(("exit", type_name), "")
    ]
    missing_cleanup = [
        type_name
        for type_name in entry_types
        if GENERIC_CLEANUP not in bodies.get(("cleanup", type_name), "")
    ]
    return entry_types, missing_exit, missing_cleanup


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} SOURCE_ROOT")

    source_root = Path(sys.argv[1]).resolve()
    host_path = source_root / "ThunkLibs/libvulkan/Host.cpp"
    common_host = (source_root / "ThunkLibs/include/common/Host.h").read_text(encoding="utf-8")
    generator = (source_root / "ThunkLibs/Generator/gen.cpp").read_text(encoding="utf-8")
    host = host_path.read_text(encoding="utf-8")

    entry_types, missing_exit, missing_cleanup = missing_owners(host)
    print(f"non-default generic pNext entry owners: {len(entry_types)}")
    print("missing mutable reverse owners: " + (", ".join(missing_exit) or "(none)"))
    print("missing const cleanup owners: " + (", ".join(missing_cleanup) or "(none)"))

    required_contract = {
        "const wrapper cleanup route": "fex_apply_custom_repacking_cleanup(*data);" in common_host,
        "generated cleanup declaration": "void fex_custom_repack_cleanup(const host_layout<{}>& from);" in generator,
        "generated cleanup adapter": "fex_apply_custom_repacking_cleanup(const host_layout<{}>& from)" in generator,
        "default Vulkan cleanup macro": "void fex_custom_repack_cleanup(const host_layout<name>& from)" in host,
        "host-only recursive cleanup": "static void default_fex_custom_repack_cleanup(const VkBaseOutStructure& from)" in host,
    }
    absent_contract = sorted(name for name, present in required_contract.items() if not present)
    if absent_contract:
        print("missing cleanup contract: " + ", ".join(absent_contract))

    negative_source = host.replace(
        "void fex_custom_repack_cleanup(const host_layout<VkInstanceCreateInfo>& from) {\n"
        "  default_fex_custom_repack_cleanup(reinterpret_cast<const VkBaseOutStructure&>(from.data));",
        "void fex_custom_repack_cleanup(const host_layout<VkInstanceCreateInfo>& from) {",
        1,
    )
    if negative_source == host:
        print("negative control setup failed: VkInstanceCreateInfo cleanup site not found")
        negative_ok = False
    else:
        _, _, negative_missing_cleanup = missing_owners(negative_source)
        negative_ok = negative_missing_cleanup == ["VkInstanceCreateInfo"]
        print(
            "negative control missing const cleanup: "
            + (", ".join(negative_missing_cleanup) or "(none)")
        )

    expected_owner_count = 18
    if len(entry_types) != expected_owner_count:
        print(f"unexpected owner count: expected {expected_owner_count}, got {len(entry_types)}")

    return 1 if missing_exit or missing_cleanup or absent_contract or not negative_ok or len(entry_types) != expected_owner_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
