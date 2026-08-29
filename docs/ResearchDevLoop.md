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

## VS Code without a mystery build

VS Code is a good FEX editor when it is treated as a view over the same explicit build lane, not as
a second build system. Install the recommended clangd, CMake Tools and Microsoft C/C++ extensions,
open the repository root, then run **Terminal → Run Task → FEX: prepare editor lane** once.

The task configures the external `editor` lane when needed and writes an ignored
`compile_commands.json` at the worktree root. That file tells clangd the real compiler flags for
every translation unit. The helper translates the stable cache-view source paths back to the open
worktree; this is why simply symlinking the external compilation database is not equivalent.

After preparation:

- use `F12` for definition, `Shift+F12` for references and `Ctrl+P`, then `#`, for symbols;
- press `Ctrl+Shift+B` for **FEX: build one target** and enter the exact CMake target;
- run **FEX: show editor lane receipt** before reporting what was built;
- use the Run and Debug pane with GDB or LLDB only after identifying a runnable x86-host test or
  tool. The main FEX runtime still requires an ARM64 execution environment.

The Microsoft C++ language engine is disabled for this workspace so it does not duplicate clangd's
diagnostics and indexing. Its debugger remains available. CMake Tools supplies CMake syntax and
project affordances, but automatic configuration is disabled so it cannot silently create a second,
uncached build tree.

Command-line users get the same setup with:

```sh
./Scripts/ResearchDevBuild.py --lane editor editor
./Scripts/ResearchDevBuild.py --lane editor build vulkan-host-64
```

Re-run `editor` after changing CMake structure, switching the lane to another worktree or changing
the build profile. Ordinary source edits do not require regenerating the database.

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
