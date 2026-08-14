#!/usr/bin/env python3
from pathlib import Path

p = Path('.github/fieldwork/add_callback_owner_generation_lease.py')
text = p.read_text()
old = '''        p,
        ''' + "'''  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;\n\n  uint8_t* HostTrampolineInstanceDataPtr;'''" + ''',
        ''' + "'''  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;\n  // Diagnostic process-lifetime registry. Product code should give owner generations\n  // explicit reclamation independent of the stable escaped trampoline lifetime.\n  fextl::unordered_map<uint64_t, GuestCallbackOwnerGeneration*> CallbackOwnerGenerations;\n\n  uint8_t* HostTrampolineInstanceDataPtr;'''" + ''',
        "callback owner registry",
'''
new = '''        p,
        "  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;",
        "  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;\\n"
        "  // Diagnostic process-lifetime registry. Product code should give owner generations\\n"
        "  // explicit reclamation independent of the stable escaped trampoline lifetime.\\n"
        "  fextl::unordered_map<uint64_t, GuestCallbackOwnerGeneration*> CallbackOwnerGenerations;",
        "callback owner registry",
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f'owner registry patcher anchor: expected one, found {count}')
p.write_text(text.replace(old, new, 1))
print('relaxed callback owner registry anchor to the stable Guestcall map declaration')
