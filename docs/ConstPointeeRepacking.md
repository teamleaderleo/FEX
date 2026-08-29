# Why thunk repacking must remember `const`

This note explains the narrow fix on the owned-fork research branch `codex/thunkgen-const-current`. It assumes familiarity with ordinary C++ pointers, but not with FEX's thunk generator.

## The bridge in one minute

FEX thunks let an x86 guest call a native host library. A pointer passed by the guest cannot always be handed directly to the host: the pointed-to C/C++ structure may have a different layout under the guest and host ABIs.

For those cases, generated host code calls `make_repack_wrapper<T>`:

1. `guest_layout` describes the guest-side value.
2. `repack_wrapper` creates temporary host-layout storage and repacks into it.
3. The native host function receives a pointer to that temporary object.
4. The wrapper's destructor may repack changes back into guest memory.

Step 4 is where `const` matters. A native function accepting `const A*` promises not to modify the `A`. FEX should not perform ordinary exit writeback for that parameter.

## The bug

The wrapper already has the correct policy:

```cpp
if constexpr (!std::is_const_v<std::remove_pointer_t<T>>) {
  // ordinary host-to-guest exit repacking
}
```

But the generator removed `const` from a pointed-to type before choosing `T`. In effect, a source parameter like `const A*` instantiated `repack_wrapper<A*>`. By the time the destructor asked whether the pointee was const, that information had been erased, so the ordinary writeback path was eligible.

## Why preserving `const` is safe

The generator now instantiates the wrapper with the original parameter type: `repack_wrapper<const A*>`.

The wrapper independently removes cv-qualification for its private temporary storage (`PointeeT`). It can still construct and hold the repacked host representation. The original template argument remains available only where API semantics matter—especially the exit-writeback decision.

Custom exit repacking is still invoked. That is intentional because a custom hook may release entry-side allocations or perform other bookkeeping even when the native function received a const pointer. Only the wrapper's automatic guest-memory writeback is suppressed.

## Bounded proof on big-red

Source head: `6a741ede248ef29d903b37efa028765983339b97`, based on fork `origin/main` at `8fe2f3d1e2fd29d78b1927616daf0e973df54816`.

- Focused target `thunkgentest` built successfully with the cached Clang 21/Ninja lane.
- With the fix present, the single Catch2 case `StructRepacking` passed 28 assertions for both x86-32 and x86-64 guest ABIs in 0.98 seconds, with 99,256 KiB peak RSS.
- Negative control: with only the generator behavior temporarily reverted and the new test retained, the same case failed twice. The emitted `make_repack_wrapper<...>` type lacked `const` for both guest ABIs.
- Restoring the fix rebuilt the target in 0.52 seconds and returned the same focused case to green.

This proves code generation preserves the qualifier and proves the regression test detects the old behavior. It does not by itself prove every thunk or an ARM64 runtime workload; those are separate scopes.

## Reading trail

- Generation decision: `ThunkLibs/Generator/gen.cpp`, in `GenerateThunkLibsAction::OnAnalysisComplete`.
- Wrapper storage and exit policy: `ThunkLibs/include/common/Host.h`, `repack_wrapper`.
- Focused regression: `unittests/ThunkLibs/generator.cpp`, `StructRepacking`.
