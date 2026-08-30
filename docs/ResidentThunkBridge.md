# Resident thunk bridges: keeping escaped code alive

This guide explains the owned fork's optional resident-output primitive for `thunkgen`. It assumes
ordinary C++ familiarity, but not FEX internals. The primitive does not yet make GL, Vulkan, or any
other thunk library resident; library integration and ARM64 runtime acceptance are separate work.

## The problem in one picture

An x86 program can ask a native library for a function pointer. FEX cannot return that ARM64 host
address directly to x86 code, so it associates the native address with an x86-callable adapter:

```text
x86 application
  -> native function address H
  -> FEX association H -> guest adapter T
  -> T packs the x86 call and enters the native host function
```

`H` can belong to a process-lived native loader while `T` is compiled into an ordinary guest thunk
shared object. If that shared object unloads after the address escapes, `H` can remain meaningful
while the executable bytes for `T` no longer exist.

Cache invalidation and exact rebinding repair future `H -> T` selection. They cannot pull an old
machine-code target back from another thread that already selected it. The ownership fix is to put
escaped executable adapters in an object whose lifetime is at least as long as every retained
address.

## Vocabulary

| term | meaning here |
| --- | --- |
| guest | the x86 program and x86 executable code |
| host | the native machine and native libraries |
| wrapper | the ordinary per-library guest thunk shared object |
| invoker | guest executable glue that turns a call through a native address into a host call |
| unpacker | guest executable glue entered when host code calls a guest callback |
| resident companion | a small per-library shared object intended to outlive the ordinary wrapper |

The two directions are different. Every runtime native function-pointer signature needs an
invoker. An unpacker is needed only when generator analysis has observed that signature as an
actual host-to-guest callback parameter. Signature shape or argument count is not ownership
evidence.

## What the generator can emit

Normal generation still writes one ordinary guest include. Resident mode derives two siblings
from the same analysis pass:

```text
thunkgen interface analysis
  -> thunkgen_guest_<library>.inl
  -> thunkgen_guest_<library>_bridge.inl
  -> thunkgen_guest_<library>_bridge_accessors.inl
```

The bridge definitions contain one deduplicated `MAKE_CALLBACK_THUNK` invoker per runtime
function-pointer signature. They contain `CallbackUnpack` exports only for the callback-direction
subset recorded by `AnalysisAction`.

The accessor include gives the unloadable wrapper typed functions for obtaining those resident
addresses. All typed invoker accessors share one indexed C dispatcher, and the callback subset
shares a second dispatcher. This keeps the wrapper's dynamic relocation surface constant instead
of adding one imported symbol per signature. Requesting an unpacker for a signature that analysis
did not classify as a callback has no generated specialization and fails at compile time.

The normal output and bridge output share the same signature digest and numbering computed in
`GenerateThunkLibsAction::OnAnalysisComplete`. Resident mode must not quietly create a second
identity system or change the ordinary guest output.

## Code-reading trail

Read these in order:

1. `ThunkLibs/Generator/analysis.cpp` identifies real generated callback parameters.
2. `ThunkLibs/Generator/analysis.h` retains their keys as a subset of all runtime function pointers.
3. `ThunkLibs/Generator/gen.cpp` deduplicates signatures once and emits ordinary, bridge, and
   accessor outputs.
4. `ThunkLibs/Generator/interface.h` carries the optional output paths.
5. `ThunkLibs/Generator/main.cpp` implements the `-guest-resident` command-line mode.
6. `ThunkLibs/GuestLibs/CMakeLists.txt` exposes `generate(... RESIDENT_BRIDGE)` without enabling it
   for a product library.
7. `unittests/ThunkLibs/generator.cpp`, case `ResidentBridgeGeneration`, is the focused oracle.

## Smallest verification

Build and run only the generator case:

```sh
./Scripts/ResearchDevBuild.py --lane resident-thunkgen check \
  thunkgentest ResidentBridgeGeneration.ThunkGen
```

The case proves:

- ordinary output is identical with resident output disabled or enabled;
- a pure runtime function pointer gets one invoker and no unpacker;
- a real callback gets one matching unpacker;
- duplicate signatures retain one identity even when both directions use them;
- repeated generation is byte-identical in the selected toolchain;
- the bridge and allowed typed accessors compile;
- requesting an unproven callback direction fails to compile.

This is x86-host generator evidence. It does not execute x86 code under FEX.

## What remains before a library can adopt it

A product integration still needs a separate, per-library decision and evidence chain:

1. build a companion shared object from the bridge output;
2. make only that companion process-resident;
3. make it the single authority for escaped adapters rather than leaving parallel wrapper-local
   registries;
4. identify custom callback targets and unpackers that generator analysis cannot see;
5. prove the ordinary wrapper physically unloads while retained calls still work;
6. force a moved wrapper reload and repeat the retained call;
7. measure ELF size, relocation time, RSS/PSS, and duplicated signatures;
8. decide logical stale-handle policy separately from executable lifetime.

Whole-wrapper `NODELETE` is a simpler containment, but it also retains wrapper constructors,
mutable globals, TLS, and other library state. A resident companion is meant to narrow that
process-long lifetime to the executable glue whose addresses actually escape. Neither policy is
enabled merely by adding the generator primitive.

Do not run a broad FEX suite to answer a generator ownership question. Escalate to a bounded ARM64
profile only when the unresolved claim is physical unload/reload or callback execution on the real
cross-ISA path.
