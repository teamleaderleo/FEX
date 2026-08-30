# Owned-fork x86 development loop

This is an internal research profile for `teamleaderleo/FEX`. It is not an upstream contribution
recipe and it is not ARM runtime acceptance.

For a current symptom-to-owner map of the merged research fixes and the remaining hypotheses, start
with [the owned-fork research map](OwnedForkResearchMap.md).

For a beginner-oriented map of Vulkan GIPA/GDPA, custom host wrappers, and the focused inventory
test, see [Vulkan proc-address routing](VulkanProcAddressRouting.md).

For the custom struct-repacking lifecycle, const-safe host cleanup, Vulkan `pNext` ownership, and
its allocation-balance oracle, see [Custom thunk repacking](ThunkRepacking.md).

For the escaped-code lifetime problem, the optional direction-aware generator output, and the
boundary before any library can adopt a resident companion, see
[Resident thunk bridges](ResidentThunkBridge.md).

For Vulkan's generated proc-address invokers, three custom X11 publications, focused local checks,
and the GPU-free retained-call runtime oracle, see
[Vulkan resident companion](VulkanResidentCompanion.md).

## One target, not everything

Before configuring anything on a new machine or chat container, inspect the local boundary:

```sh
./Scripts/ResearchDevBuild.py doctor
```

`doctor` is read-only. Its JSON receipt reports the exact source head/dirty state, recursive pinned
submodule digest, required tool paths, host architecture, and three deliberately separate outcomes:
focused x86-host build/CTest preflight, whether a dirty tree limits the result to developer
feedback, and whether an ARM64 product-runtime question must move to a checked-in profile. A green
preflight still says that configure/build/test did not run, and it is not an ARM runtime result.
Missing or drifted submodules include the
bounded recovery command, but `doctor` never runs it, configures CMake, writes a cache, builds a
target, runs a test, or installs a package. VS Code exposes the same check as **FEX: diagnose
experiment capability**.

Explicitly initialize the pinned submodules once, then build only the target that owns the current
question:

```sh
./Scripts/ResearchDevBuild.py submodules
./Scripts/ResearchDevBuild.py --lane vulkan build vulkan-host-64
./Scripts/ResearchDevBuild.py --lane vulkan status
```

The `submodules` action is the one setup command that intentionally mutates the selected worktree.
It runs the repository's shallow recursive update with up to 16 parallel clone/fetch workers, then
fails unless every recursive repository is at the superproject's exact pinned commit. Its compact
receipt includes the superproject head, dirty bit, worker count, repository count, elapsed time and
a content-addressed digest of the complete pinned commit/path inventory. Use `--jobs N` to choose a
smaller explicit bound. Git checkout progress goes to stderr; stdout is one parseable JSON receipt,
so a carrier can redirect it without mixing progress lines into the evidence file.

### Reusing verified shallow origins before clone

When several local chats or branches need the same submodule generation, opt in to both setup
caches:

```sh
./Scripts/ResearchDevBuild.py \
  --cache-root /same/filesystem/fex-research \
  --source /same/filesystem/fex-worktree \
  submodules --jobs 16 --origin-cache --pack-cache
```

`--origin-cache` saves setup time; `--pack-cache` saves per-consumer storage. On the first compatible
generation, the origin cache deliberately runs the ordinary network update, verifies every recursive
pin and canonical HTTPS origin, then publishes one self-contained shallow repository per origin plus
an immutable manifest keyed by the top-level path/pin/origin inventory. A later worktree with that
exact top-level identity clones from those local repositories under a shared cache lock. The helper
enables file transport and exact URL rewrites only for that one Git command. It writes no persistent
Git configuration, records the original HTTPS origins in each consumer, creates no alternate, and
again verifies the complete recursive path/pin/origin graph before reporting a hit.

When both flags are requested on that first cold population, the helper deliberately defers pack
compaction. The network clone's pack layout is a one-time input; retaining it would add variants that
the local-origin consumers do not reuse. The receipt reports `deferred_until_warm_origin`. The first
later worktree populated from the local origins creates the reusable pack-cache generation instead.
Do not rerun the cold worktree merely to force compaction; initialize the next actual worktree.

A missing generation is an ordinary cold population. A present but malformed manifest, unsafe
directory, missing advertised pin, non-HTTPS origin, unexpected nested repository, active cache
mutation or post-clone identity difference fails closed instead of falling back to an unrecorded
network treatment. The cache is an optimization, not source authority: reusable evidence still
binds the superproject SHA and recursive pin digest.

The accepted exact-head Big Red A-L-L-A control used 18 shallow repositories and 17 origins. Local
origin medians were 6.965 seconds versus 69.045 seconds ordinary, 62.08 seconds / 89.91% lower, with
all 18 canonical origins, zero alternates and zero `git fsck` failures. Local module Git allocation
was 152,248,320 versus 173,598,720 bytes. A private empty pack-cache composition then left only
4,734,976 consumer-unique bytes, below the established 15,790,080-byte pack-cache median. These are
same-host exact-generation results, not a claim about an empty cache, a changed submodule generation,
or hosted runners.

Inspect the origin cache without changing it:

```sh
./Scripts/ResearchDevBuild.py \
  --cache-root /same/filesystem/fex-research \
  submodule-origin-cache
```

The inventory validates immutable manifests, origin bindings, advertised pins and each shallow Git
repository under a nonblocking shared lock. It reports generations, origins, advertised pins and
inode-aware allocation. It has no delete, prune, garbage-collection or eviction action. The origin
cache adds another retained copy of the shallow objects, so keep it opt-in for repeated local
materialization; the one-consumer hosted ARM carrier remains on the ordinary path.

### Sharing immutable submodule packs between retained worktrees

Ordinary `git submodule update --depth 1` deliberately copies objects when its source repository is
shallow, even for a same-filesystem local clone. When two or more exact research worktrees retain
the same recursive pins on one filesystem, opt in to the fork's content-addressed pack cache:

```sh
./Scripts/ResearchDevBuild.py \
  --cache-root /same/filesystem/fex-research \
  --source /same/filesystem/fex-worktree \
  submodules --jobs 16 --pack-cache
```

The ordinary shallow update and complete recursive-pin verification still happen first. The helper
then creates one generation named by that verified pin-inventory digest, hashes every eligible pack
file with SHA-256, and atomically hardlinks identical immutable data into the consumer. It accepts
only current-user, mode-`0444` regular `.pack`, `.idx`, and `.rev` files, holds one cache-wide
exclusive lock, rechecks each target before replacement, verifies the complete pin identity again,
and fails closed on unknown pack-directory files or a cross-filesystem cache. It never writes a Git
alternate. Removing the pool therefore removes only one directory entry per inode; retained
consumer links still contain the objects. A later consumer repack/garbage collection writes or
renames its own files instead of modifying another consumer's link, as verified by the bounded
destructive-isolation control.

This is an explicit multi-consumer storage optimization, not the default setup path. At exact FEX
head `a5ef2fdad7a486ad43115ce76fe2ddcd357cdd7e` on big-red ext4, three ordinary fresh consumers had a
median 173,588,480-byte marginal module store. Three pack-cache consumers had a median 15,790,080
bytes of marginal allocation, a 90.90% reduction. The cache path added 2.06% to median
materialization time (72.03 versus 70.58 seconds), within the preregistered 20% ceiling. Pool plus
two consumers used 191,438,848 bytes, 44.86% less than two ordinary stores; three consumers saved
313,536,512 bytes, or 60.21%. A first consumer plus the pool can cost slightly more than an ordinary
consumer, so the hosted ARM carrier and other disposable one-consumer jobs intentionally remain on
the ordinary path.

Inspect retained generations without changing them:

```sh
./Scripts/ResearchDevBuild.py \
  --cache-root /same/filesystem/fex-research \
  submodule-cache
```

The inventory obtains a nonblocking shared lock and reports generation, entry, allocated,
consumer-linked, and pool-only reclaimable bytes. It does not delete or evict anything. Review
process/worktree ownership before removing a generation; a `reclaimableAllocatedBytes` value is
evidence about link count, not deletion authority. Ordinary `du` reports hardlinked blocks once per
path and can therefore double-count the pool plus consumer; use the bootstrap receipt's
`consumerUniqueMarginalBytes` and the inventory's inode-aware totals for decisions. The pool is a
local optimization, never provenance: exact source SHA and recursive pin digest remain the receipt
identity.

The helper checks every recursive submodule before it configures anything. It fails immediately when
a submodule is uninitialized, conflicted, or not at the superproject's pinned commit, and prints the
exact bounded Git recovery command. This keeps missing third-party sources from turning into a long wall of
unrelated CMake errors. It does not update submodules automatically because doing so mutates the
worktree and can erase the provenance distinction between the requested source and the environment
that happened to be present.

### Discover the configured target and test names

An established lane can search its configured CMake targets and CTest registry before you decide
what to build:

```sh
./Scripts/ResearchDevBuild.py --lane editor discover Vulkan
```

The query is one case-insensitive literal, not a regular expression. The JSON result reports CMake
targets and tests separately, with at most 32 results from each registry by default. Tests whose
executables have not been built are labelled `not_built`; a registered Python or binary command is
only a registry fact, not proof that the test is ready or that a similarly named target owns it.
Use `--limit N` to request up to 64 results.

`discover` requires an existing lane at the requested source and exact profile. It verifies CMake's
glob sentinel and refuses pending regeneration, then reads semantic target headings from the
generated Ninja graph and uses CTest's JSON show-only mode. It does not configure, build a target,
run a test, write a receipt, persist an index or infer target/test ownership. The same query is
available as the VS Code task **FEX: discover configured targets and tests**.

The helper configures Clang, Ninja, lld, ccache, `RelWithDebInfo`, assertions, thunks and tests,
without LTO or the GUI. It prints and stores a receipt containing the exact source head, dirty bit,
target, worker count, configuration mode, setup and target durations, result, cache namespace and
ccache sloppiness policy. FEX's CMake
already elects `time_macros` reuse for sources that embed `__DATE__` or `__TIME__`; the helper
exports that setting explicitly because the CMake 4.2/Ninja launcher observed on big-red retained
`ccache` but dropped its policy argument. A cache hit can therefore retain an earlier embedded build
timestamp, as intended by that repository policy. A target build does not claim a full build or
full-test pass.

### Explain a rebuild before spending it

On an already configured lane, preview the exact pending target edges without compiling, linking,
generating or testing:

```sh
./Scripts/ResearchDevBuild.py --lane vulkan plan vulkan-host-64
```

`plan` requires that the lane already points to the requested source and has the exact current
profile marker. It never creates, switches, cleans or configures a lane. It runs CMake's generated
glob comparison, then gives Ninja a private temporary manifest that removes only the glob check's
always-dirty force edge. The real CMake-input regeneration relationship remains in the shadow graph.
If a `CMakeLists.txt`, configured glob or other CMake input changed, the command exits 2 and asks for
normal regeneration instead of reporting a stale downstream plan. A never-built graph may also
fail closed when its dynamic-dependency files do not exist yet.

The JSON receipt reports the exact source head/dirty state, profile, lane and target; glob-check and
plan durations; pending edge/reason counts; and bounded samples with stable source/build path
placeholders. A no-op reports zero steps. An ordinary source edit names the compile, archive and
link chain it would trigger. Ninja runs with `-n`; the helper hashes `.ninja_log`, `.ninja_deps` and
the last build/check receipt before and after, refuses any change, removes the private manifest and
does not overwrite the last successful receipt. The CMake glob sentinel is the only permitted
state change.

This predicts Ninja's current work graph, not elapsed build time or ccache hit/miss outcome. Use it
to decide whether the selected owner is unexpectedly broad; use the real focused build for timing
and compiler-cache evidence. VS Code exposes the same command as **FEX: explain one target rebuild**.

### Build one owner and run one exact CTest

When the smallest useful check is already registered with CTest, keep the target build and test
selection in the same lane and receipt:

```sh
./Scripts/ResearchDevBuild.py --lane vulkan check \
  thunkgentest VulkanCustomRouteInventory.ThunkGen
```

`check` first builds only the named owning target, then passes the test name through CTest's exact
`--tests-from-file` selector. The test argument is a literal name, not a regular expression:
`VulkanCustomRouteInventory.*` therefore selects nothing and fails instead of widening to a family.
An unknown name also fails through `--no-tests=error`. The helper writes the exact request to a
private temporary file, scans CMake's generated test registry for exactly one literal definition,
then runs CTest with the same exact-name file while the lane lock holds the build graph. Generated
registry parsing fails closed on unsupported names or unsafe files. The temporary file disappears
before the command returns.

The registry receipt counts conservative generated definitions, not necessarily active CTest rows:
Catch discovery files can retain an inactive `NOT_BUILT` fallback beside the active discovered
tests. That can create a safe false rejection if two definitions share the requested name; it
cannot widen the selected test.

The receipt separates setup, target-build, exact-selection and test durations and records the exact
head, dirty bit, profile, lane, target, test, selected-test list, workers, cache namespace and exit
code. It means only that one target built and one host-side CTest ran. It is not a broad suite
result or ARM runtime evidence. As with `build`, a dirty receipt is fast developer feedback rather
than reusable exact-head acceptance.

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

If the old worktree has already been retired, CMake cannot regenerate its graph before cleaning.
In that one case the helper validates the retained `build.ninja` as a current-user, bounded regular
file and invokes Ninja's built-in clean tool directly. Ninja tool mode removes recorded outputs
without executing compiler, linker, generator, test or product commands. Only a successful clean
permits the atomic source-view repoint; an unsafe manifest or clean failure leaves the lane pointing
at the retired source for inspection. A live old source retains the ordinary CMake-mediated clean.

After that clean repoint, a matching configured/profile-marked lane uses ordinary incremental CMake
regeneration instead of deleting CMake's cache and compiler discovery. An explicit `configure`, a
missing build graph or any profile-marker mismatch still selects `cmake --fresh`. The receipt names
the actual `fresh`, `incremental` or `reuse` mode; do not infer it from target time.

Use different lane names for simultaneous experiments. Do not share one active lane.

List every retained lane before reusing or cleaning one:

```sh
./Scripts/ResearchDevBuild.py lanes
```

This action is read-only. It inventories each stable lane's source-view liveness, nonblocking lock
state, allocated bytes without following symlinks, configured-build marker, receipt/profile parse
state and compact receipt identity. A `reviewCandidate` is only a dead source symlink with no held
helper lock and a valid receipt. It is a prompt for ownership/process review, not deletion authority;
the command never removes a lane or shared ccache entry. A non-symlink source view, malformed lane
root, unsafe metadata file, cross-filesystem tree or size error fails closed as unsafe.

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
- run **FEX: explain one target rebuild** first when the pending owner chain is unclear;
- run **FEX: build target and run one exact CTest** when a focused host-side test owns the oracle;
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

At exact fork head `1ab30cadba1e3202c949f7813ca669e01823fcf7`, Git 2.53.0 and 16 logical
CPUs, three serial and three `--jobs 16` shallow recursive initializations ran in balanced order in
fresh exact-head worktrees. Serial walls were 100.58/99.49/105.83 seconds (median 100.58); parallel
walls were 69.97/66.57/69.79 seconds (median 69.79), 30.61% lower. Every sample produced the same 18
repositories, zero invalid pins and the same complete commit/path digest. Median module-store
allocation changed from 173,592,576 to 173,608,960 bytes (0.009% higher). This supports bounded
parallel initialization, not a claim that the per-worktree submodule repository storage is shared
or deduplicated.

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
