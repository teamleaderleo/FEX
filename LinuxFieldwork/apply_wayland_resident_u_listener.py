#!/usr/bin/env python3
from pathlib import Path
import runpy

# Compatibility entrypoint for the already-proven synchronous retained-listener
# workflow. The narrow one-signature prototype has served its discriminator;
# subsequent runtime regressions use the generalized 41-signature resident
# dispatcher so the same "u" path validates the full companion.
script = Path(__file__).with_name('apply_wayland_resident_all_listeners.py')
runpy.run_path(str(script), run_name='__main__')
