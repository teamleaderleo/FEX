# Owned-fork x86 development loop

This is an internal research profile for `teamleaderleo/FEX`. It is not an upstream contribution
recipe and it is not ARM runtime acceptance.

## One target, not everything

Initialize the pinned submodules once, then build only the target that owns the current question:

```sh
git submodule update --init --recursive --depth 1
./Scripts/ResearchDevBuild.py --lane vulkan build vulkan-host-64
./Scripts/ResearchDevBuild.py --lane vulkan status
```

The helper configures Clang, Ninja, lld, ccache, `RelWithDebInfo`, assertions, thunks and tests,
without LTO or the GUI. It prints and stores a receipt containing the exact source head, dirty bit,
target, worker count, duration, result and cache namespace. A target build does not claim a full
build or full-test pass.

Each named lane owns a stable `src` view, external build tree and nonblocking lock under the user
cache directory. Switching a lane to another worktree cleans the old outputs before repointing the
view, so an older source mtime cannot preserve a stale object. The stable source/build spellings let
ccache reuse content across exact worktree switches. The cache namespace includes a CPU-model hash
because this host-development profile uses FEX's `-march=native` default. A versioned profile marker
prevents a lane from silently adopting a CMake tree configured with different options.

Use different lane names for simultaneous experiments. Do not share one active lane.

## Measured big-red checkpoint

At exact fork head `8fe2f3d1e2fd29d78b1927616daf0e973df54816`, Clang 21.1.8, CMake 4.2.3,
Ninja 1.13.2 and ccache 4.12.3:

| operation | wall time | result |
| --- | ---: | --- |
| fresh configure | 4.43 s | configured |
| first `vulkan-host-64` completion after dependency repair | 8.05 s | remaining 4 build steps |
| no-op target | 0.07 s | no work |
| real edit to Vulkan `Host.cpp` | 5.32 s | compile + link |
| revert to cached content | 0.11 s | one direct ccache hit + link |
| cold exact-SHA target in a different raw worktree/build path | 61.21 s | 0/296 cache hits; rejected layout |
| clean rebuild after exact-SHA stable-view switch | 17.01 s | 273/296 cache hits |

The stable view cut that clean target rebuild by 72.2%, not to zero. Generated/dependency-sensitive
steps still missed. Ordinary edits should stay in one warm lane; worktree switching is for isolation,
review and exact-head experiments.

## Ubuntu development packages used on big-red

The successful thunk-enabled x86 configure required the compiler/build tools plus Clang/LLVM
development libraries, OpenSSL, pkg-config, OpenGL/X11 extension headers and the 32-bit cross C++
toolchain. On Ubuntu, the corresponding package families are:

```text
cmake ninja-build clang lld ccache nasm mold
llvm-dev libclang-dev libssl-dev pkg-config
libedit-dev libzstd-dev libcurl4-openssl-dev
libgl-dev libxrandr-dev libxrender-dev libxext-dev
g++-i686-linux-gnu
```

Package names and available LLVM versions vary by Ubuntu release. Missing headers should be treated
as environment failures, not FEX product failures.

## Execution boundary

The x86 host profile is useful for source review, thunk generation, focused wrapper builds and many
unit/static checks. Actual x86-on-ARM execution, Vulkan callback ABI behavior and lifetime races need
an exact ARM64 Actions/Glaeda profile with its own external temp root and receipt. Reuse a prior exact
result when source, toolchain, target and inputs have not changed.
