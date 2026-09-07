# Why thunk repacking must remember `const`

Current explanation checked against owned-fork [`72872bac83`](https://github.com/teamleaderleo/FEX/tree/72872bac83ed714ff9f79143beb4d85926186c32) on 2026-09-07. [PR #5](https://github.com/teamleaderleo/FEX/pull/5) preserves pointee constness during generation; [PR #14](https://github.com/teamleaderleo/FEX/pull/14) subsequently separates host-only cleanup from mutable exit/copyback. The historical PR #5 measurements are retained below.

For a line-by-line introduction to the surrounding C++, begin with [Reading FEX through its small fixes](FEXCodeReadingCompanion.md). The complete current three-hook contract lives in [ThunkRepacking.md](ThunkRepacking.md).

## The bridge in one minute

FEX thunks let an x86 guest call a native host library. When the guest and host representations differ, generated host code uses `make_repack_wrapper<T>` to prepare temporary host-layout storage. The native function receives access to that storage, and the wrapper's destructor handles the appropriate exit operation. Read the constructor, storage fields, and destructor together in [`common/Host.h`](../ThunkLibs/include/common/Host.h).

There are two separate decisions: what storage the host-side temporary needs, and whether the original guest input permits copyback. A const input can require temporary allocation and therefore cleanup, while preserving the guest's original fields.

## The original generator bug

The generator erased the pointee's `const` qualifier before emitting the wrapper type. A source parameter such as `const A*` therefore selected mutable-pointer behavior in the wrapper's exit policy.

The repair in [`gen.cpp`](../ThunkLibs/Generator/gen.cpp), inside `GenerateThunkLibsAction::OnAnalysisComplete`, preserves the original parameter type when emitting `make_repack_wrapper<...>`. For a `const A*` parameter, the factory now receives that same type as its explicit template argument.

The wrapper keeps mutable private storage through:

```cpp
using PointeeT = std::remove_cv_t<std::remove_pointer_t<T>>;
```

For `T = const A*`, the inner transformation yields `const A` and the outer one yields `A`. The original `T` remains available for the exit-policy decision. This is why the host representation can be constructed and managed while the guest input retains its const access policy.

`const A*` and `A* const` qualify different things: the former describes a const access path to the pointee; the latter fixes the pointer variable. The language rule is about that access path, rather than global immutability of every alias. See [C++ cv-qualification](https://eel.is/c++draft/dcl.type.cv).

## Current cleanup behavior after PR #14

At the source checkpoint, the relevant destructor branch is:

```cpp
if constexpr (std::is_const_v<std::remove_pointer_t<T>>) {
  fex_apply_custom_repacking_cleanup(*data);
} else {
  // Mutable exit hook, then eligible automatic copyback.
}
```

This is a shortened excerpt: the real header also checks whether the wrapper is eligible and holds data, and contains the complete mutable branch. For the language mechanism, see [C++ constexpr if](https://eel.is/c++draft/stmt.if).

The const branch releases entry-side host resources through the dedicated cleanup hook. The mutable branch calls the exit hook, then performs the existing automatic repacking when the hook result and compatibility conditions select it.

An earlier version of this guide described the intermediate PR #5 behavior, where custom exit processing still ran for const inputs. PR #14 superseded that policy. Read [ThunkRepacking.md](ThunkRepacking.md) for entry allocation, mutable exit, host-only cleanup, nested array owners, and the focused ownership checks.

## Historical PR #5 evidence on big-red

Original product head: `6a741ede248ef29d903b37efa028765983339b97`, based on fork `origin/main` at `8fe2f3d1e2fd29d78b1927616daf0e973df54816`.

- The focused target `thunkgentest` built with the cached Clang 21/Ninja lane.
- With the fix present, the single Catch2 case `StructRepacking` passed 28 assertions for both x86-32 and x86-64 guest ABIs in 0.98 seconds, with 99,256 KiB peak RSS.
- The recorded negative control restored the old generator behavior while retaining the new test. The same case failed twice because the emitted wrapper type lacked `const` for both guest ABIs.
- Restoring the fix rebuilt the target in 0.52 seconds and returned the same focused case to green.

After the fork incorporated upstream commit `98964c552773b374676610776357a030a6825e53`, the refreshed product head `4086fa083dd4aacbb532f6fb6ddd4f95e1940ea5` rebuilt the focused target in 2.19 seconds. `StructRepacking` again passed 28 assertions in 1.47 seconds with 99,396 KiB peak RSS.

These are the retained generator-level records from the original investigation. They establish qualifier preservation and sensitivity to the old behavior. The current cleanup contract has its own checks documented in [ThunkRepacking.md](ThunkRepacking.md); guest execution through FEX on ARM remains a separate evidence class. This documentation revision performed source/reference review only.

## Reading trail

Follow [`gen.cpp`](../ThunkLibs/Generator/gen.cpp) for the emitted type, [`Host.h`](../ThunkLibs/include/common/Host.h) for storage and exit behavior, and [`generator.cpp`](../unittests/ThunkLibs/generator.cpp) for the `StructRepacking` regression. Then use [exercise 4](FEXReadingExercises.md#4-follow-the-const-objects-complete-lifetime) to explain the const and mutable paths yourself.
