#!/usr/bin/env python3
from pathlib import Path

p = Path('.github/fieldwork/add_callback_owner_generation_lease.py')
text = p.read_text()

old_registry = '''        p,
        ''' + "'''  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;\n\n  uint8_t* HostTrampolineInstanceDataPtr;'''" + ''',
        ''' + "'''  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;\n  // Diagnostic process-lifetime registry. Product code should give owner generations\n  // explicit reclamation independent of the stable escaped trampoline lifetime.\n  fextl::unordered_map<uint64_t, GuestCallbackOwnerGeneration*> CallbackOwnerGenerations;\n\n  uint8_t* HostTrampolineInstanceDataPtr;'''" + ''',
        "callback owner registry",
'''
new_registry = '''        p,
        "  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;",
        "  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;\\n"
        "  // Diagnostic process-lifetime registry. Product code should give owner generations\\n"
        "  // explicit reclamation independent of the stable escaped trampoline lifetime.\\n"
        "  fextl::unordered_map<uint64_t, GuestCallbackOwnerGeneration*> CallbackOwnerGenerations;",
        "callback owner registry",
'''
count = text.count(old_registry)
if count != 1:
    raise SystemExit(f'owner registry patcher anchor: expected one, found {count}')
text = text.replace(old_registry, new_registry, 1)

old_decl = '''        p,
        ''' + "'''  void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;\n\n  void AppendThunkDefinitions'''" + ''',
        ''' + "'''  bool RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;\n\n  void AppendThunkDefinitions'''" + ''',
        "RetireGuestRange class declaration",
'''
new_decl = '''        p,
        "  void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;",
        "  bool RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;",
        "RetireGuestRange class declaration",
'''
count = text.count(old_decl)
if count != 1:
    raise SystemExit(f'owner retire declaration patcher anchor: expected one, found {count}')
text = text.replace(old_decl, new_decl, 1)

p.write_text(text)
print('relaxed owner-generation registry and RetireGuestRange declaration anchors')
