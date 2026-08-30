# GL resident thunk companion

This owned-fork integration is deliberately narrower than making all of `libGL-guest.so`
resident. It gives executable addresses that GL publishes to longer-lived native state a matching
process-lived owner while leaving the ordinary wrapper eligible for physical unload.

## Code flow

```text
native GL function H
  -> libGL-guest glXGetProcAddress
  -> generated typed accessor
  -> one companion invoker dispatcher plus signature index
  -> companion-owned guest invoker T
  -> LinkAddressToFunction(H, T)
```

`ThunkLibs/GuestLibs/CMakeLists.txt` enables `RESIDENT_BRIDGE` only for GL. It builds
`libfex-GL-bridge.so`, links it as a dependency of the ordinary wrapper, and applies ELF
`NODELETE` only to the companion.

`ThunkLibs/libGL/libGL_Guest.cpp` no longer obtains proc-address invokers from its own generated
definitions. It uses the generated resident accessors. Its four custom publications also use
addresses exported by `ThunkLibs/libGL_bridge/Guest.cpp`.

The three X11 publications use tiny companion-owned forwarding targets, not the wrapper's direct
`XSync`, `XGetVisualInfo`, and `XDisplayString` addresses. The companion records a non-optional
`libX11.so.6` dependency, so both the forwarding target and the implementation it calls have an
explicit lifetime owner after the ordinary GL wrapper closes. The malloc target is implemented
directly in the companion.

## Why one indexed dispatcher

The first mechanical integration exported one accessor function for every GL function-pointer
signature. With 736 signatures, that changed the wrapper from 38 to 774 dynamic relocations.

The generated ABI instead imports one invoker lookup function and, when generator analysis proves
callback-direction signatures exist, one unpacker lookup function. Typed template specializations
still enforce the signature-to-index mapping at compile time. On the same GCC 15.2 RelWithDebInfo
control build, the resulting GL wrapper had 39 dynamic relocations: one more than the untouched
wrapper rather than 736 more.

## Small local checks

The generator ownership and constant-import property are covered by one exact test:

```sh
./Scripts/ResearchDevBuild.py --lane gl-resident-companion-current check \
  thunkgentest ResidentBridgeGeneration.ThunkGen
```

The guest products can be built without a full FEX build after producing `thunkgen`:

```sh
cmake -S ThunkLibs/GuestLibs -B guest -G Ninja \
  -DBITNESS=64 \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/Data/CMake/toolchain_x86_64.cmake" \
  -DFEX_PROJECT_SOURCE_DIR="$PWD" \
  -DGENERATOR_EXE=/path/to/thunkgen \
  -DX86_DEV_ROOTFS=/
cmake --build guest --target GL-guest GL_bridge-guest
```

Inspect both DSOs instead of inferring lifetime from CMake source:

```sh
readelf -dW guest/libGL-guest.so
readelf -dW guest/libfex-GL-bridge.so
readelf -rW guest/libGL-guest.so
```

The wrapper must need `libfex-GL-bridge.so` and must not contain `NODELETE`. The companion must
contain `NODELETE` and need `libX11.so.6`.

## Cross-ISA decision profile

`Scripts/ResearchProfiles/gl-resident-companion-v1` is the bounded ARM64 oracle. It first runs an
ordinary x86 control under the exact candidate FEX. Only then does it prove:

- the ordinary GL wrapper mapping disappears after `dlclose`;
- the companion and its X11 dependency remain mapped;
- a retained GL proc-address call still executes;
- a retained GLX call traverses the companion-owned X11 forwarding target and unpacker;
- reserving every old wrapper range forces a moved reload; and
- both the reloaded and originally retained proc addresses remain callable.

The profile is dispatched through the reusable manual ARM carrier described in
`docs/OwnedForkCI.md`. A compile-only result is not a substitute for that runtime receipt.

## Non-goals

This integration does not deduplicate signatures across libraries, reclaim the companion, define a
logical stale-native-handle policy, or make Vulkan resident. It also does not claim that all GL or
X11 state is safe to retain indefinitely; it owns only the executable targets and dependencies
that this wrapper publishes.
