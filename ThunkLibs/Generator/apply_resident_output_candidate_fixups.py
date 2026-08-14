#!/usr/bin/env python3

from pathlib import Path

path = Path(__file__).resolve().with_name("main.cpp")
text = path.read_text()
old = '''    output_filenames.guest = output_filename;
    if (!output_filename.ends_with(".inl")) {
      std::cerr << "Resident guest output filename must end in .inl\\n";
      return EXIT_FAILURE;
    }
    output_filename.resize(output_filename.size() - 4);
    output_filenames.guest_bridge = output_filename + "_bridge.inl";
    output_filenames.guest_bridge_accessors = output_filename + "_bridge_accessors.inl";
'''
new = '''    output_filenames.guest = output_filename;
    if (!output_filename.ends_with(".inl")) {
      std::cerr << "Resident guest output filename must end in .inl\\n";
      return EXIT_FAILURE;
    }
    auto output_base = output_filename.substr(0, output_filename.size() - 4);
    output_filenames.guest_bridge = output_base + "_bridge.inl";
    output_filenames.guest_bridge_accessors = output_base + "_bridge_accessors.inl";
'''
if text.count(old) != 1:
    raise SystemExit(f"main.cpp fixup anchor count={text.count(old)}")
path.write_text(text.replace(old, new, 1))
print("Applied resident-output filename fixup")
