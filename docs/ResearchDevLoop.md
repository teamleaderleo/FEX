# Owned-fork x86 development loop

This is an internal research profile for `teamleaderleo/FEX`. It is not an upstream contribution
recipe and it is not ARM runtime acceptance.

For a beginner-oriented map of Vulkan GIPA/GDPA, custom host wrappers, and the focused inventory
test, see [Vulkan proc-address routing](VulkanProcAddressRouting.md).

For the custom struct-repacking lifecycle, const-safe host cleanup, Vulkan `pNext` ownership, and
its allocation-balance oracle, see [Custom thunk repacking](ThunkRepacking.md).

## One target, not everything

Initialize the pinned submodules once, then build only the target that owns the current question:

```sh
git submodule update --init --recursive --depth 1
./Scripts/ResearchDevBuild.py --lane vulkan build vulkan-host-64
./Scripts/ResearchDevBuild.py --lane vulkan status
```

The helper checks every recursive submodule before it configures anything. It fails immediately when
a submodule is uninitialized, conflicted, or not at the superproject's pinned commit, and prints the
exact recovery command above. This keeps missing third-party sources from turning into a long wall of
unrelated CMake errors. It does not update submodules automatically because doing so mutates the
worktree and can erase the provenance distinction between the requested source and the environment
that happened to be present.

The helper configures Clang, Ninja, lld, ccache, `RelWithDebInfo`, assertions, thunks and tests,
without LTO or the GUI. It prints and stores a receipt containing the exact source head, dirty bit,
target, worker count, configuration mode, setup and target durations, result, cache namespace and
ccache sloppiness policy. FEX's CMake
already elects `time_macros` reuse for sources that embed `__DATE__` or `__TIME__`; the helper
exports that setting explicitly because the CMake 4.2/Ninja launcher observed on big-red retained
`ccache` but dropped its policy argument. A cache hit can therefore retain an earlier embedded build
timestamp, as intended by that repository policy. A target build does not claim a full build or
full-test pass.

The receipt observes `HEAD` and the dirty bit immediately before target execution; it does not
snapshot or lock a worktree that an editor can still change. That is appropriate for fast local
feedback, but it is not immutable experiment identity. For a result that will be reused as exact
evidence, point the lane at a dedicated clean worktree, make no concurrent edits, and recheck the
worktree identity after the command before publishing the receipt. A dirty receipt is developer
feedback, not an exact-head acceptance record.

When the question needs an x86 guest Linux test binary, select the bounded profile explicitly:

```sh
./Scripts/ResearchDevBuild.py --profile linux-tests --lane smc linux-test-build smc-2
```

This adds `BUILD_FEX_LINUX_TESTS=True` to the same base profile. It still builds only the named
target. The profile ID participates in both the fail-closed lane marker and the ccache namespace,
so a Linux-test lane cannot silently reuse a CMake tree or native-code cache namespace from the
ordinary developer profile. The action builds the exact `FEX` and `FEXServer` runtime
prerequisites, configures the guest test sub-build, and builds only `smc-2.64`. It does not run the
binary or build the rest of FEXLinuxTests. The receipt prints both product and guest-binary paths.
Use `--bitness 32` for the corresponding 32-bit build. Actual emulated execution still belongs in
an exact ARM64 Actions/Glaeda profile; x86-host-debug FEX is not a product-runtime oracle.

Each named lane owns a stable `src` view, external build tree and nonblocking lock under the user
cache directory. Switching a lane to another worktree cleans the old outputs before repointing the
view, so an older source mtime cannot preserve a stale object. The stable source/build spellings let
ccache reuse content across exact worktree switches. The cache namespace includes a CPU-model hash
because this host-development profile uses FEX's `-march=native` default. A versioned profile marker
prevents a lane from silently adopting a CMake tree configured with different options.

After that clean repoint, a matching configured/profile-marked lane uses ordinary incremental CMake
regeneration instead of deleting CMake's cache and compiler discovery. An explicit `configure`, a
missing build graph or any profile-marker mismatch still selects `cmake --fresh`. The receipt names
the actual `fresh`, `incremental` or `reuse` mode; do not infer it from target time.

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

Re-run `editor` after pulling or switching commits, changing CMake structure, switching the lane to
another worktree or changing the build profile. On an existing lane it performs an incremental CMake
regeneration before exporting the database; it does not throw away the warm object tree. Ordinary
edits inside already-known source files do not require regenerating the database.

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

A follow-up at source head `cf33899aed10a9ce0ccf2a6f285f0054ee18874d`, with eight workers and
a route-private copy of the same frozen cache, measured the two remaining causes separately:

| clean exact-head replay | target time | wrapper wall | cache result |
| --- | ---: | ---: | --- |
| deterministic Vulkan thunk output | 5.76 s | 12.58 s | 294/298 cacheable calls hit |
| explicit `time_macros` policy | 1.00 s | 7.07 s | 298/298 cacheable calls hit |

The first row is the realized benefit of sorting thunk declarations deterministically before the
existing dependency pass: the generated 1,951,591-byte include remained byte-identical, and the
4,890,688-byte Vulkan `Host.cpp` object changed from a roughly 4.8-second compilation to a direct
hit. The second row validates this helper's explicit export of FEX's already-selected timestamp
policy: all three sources containing `__DATE__` or `__TIME__` became direct hits. The full change
from the fixed-head cache-population build (21.43 s target) to the final replay (1.00 s) was 95.3%,
but part of that combined improvement came from populating the second worktree-path manifest for
`Context.cpp`; it must not all be attributed to timestamp handling. Wrapper time is now dominated
by the roughly 5.8-second configure/source-switch phase. These are target-build measurements, not
runtime or test-suite results.

A bounded source-switch experiment then crossed the known CMake graph change from `8fe2f3d1e` to
`deb871325`: the newer tree added both `DiskCache.cpp` and `WorkQueueThread.cpp`. Three fresh and
three incremental configurations ran in fresh/incremental/incremental/fresh/fresh/incremental order
with CMake 4.2.3 and concurrency one:

| treatment | samples (s) | median | result |
| --- | --- | ---: | --- |
| fresh | 5.581, 6.758, 6.413 | 6.413 s | semantic graph green |
| incremental | 2.648, 2.622, 2.630 | 2.630 s | semantic graph green |

Incremental regeneration reduced median configuration wall time by 59.0%. Every treatment produced
the same 498-entry compile graph, 153-target CMake File API codemodel and 17,312-node product Ninja
inventory; both new sources and `vulkan-host-64` appeared exactly once. One selected target build
from each treatment also passed. Their target times are not an A/B measurement: the fresh build ran
first and populated the shared route-private ccache for the incremental build.

A stricter preliminary oracle was killed rather than relabeled: `ninja -t targets all` exposed 90
additional fresh-only CMake 4.2.3 compiler-detection module inputs. Incremental had no extra nodes,
and all non-configuration nodes plus the semantic codemodel matched. This is why the accepted graph
oracle separates CMake's configure/re-run inputs from product targets instead of silently calling
the whole raw Ninja listing equivalent. The result covers this exact toolchain and source-addition
boundary; it does not justify incremental reuse after a profile mismatch, missing graph or explicit
fresh-configure request.

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
