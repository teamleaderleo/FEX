# CI policy for the owned research fork

The upstream FEX workflows are designed for the upstream project's always-on self-hosted runner fleet. This fork is used differently: one owner and multiple research agents run narrow experiments locally, on big-red/Glaeda, or in an explicitly selected Actions lane.

## Default PR behavior

Opening or updating a pull request does **not** enqueue the broad build, GLIBC fault, hostrunner, instruction-count, MinGW, Steam Runtime, Wine DLL, or VIXL matrices in `teamleaderleo/FEX`. Those matrices cover unrelated platforms and can consume many runner-hours even for a deterministic generator or documentation change. They also do not create skipped check-suite noise on ordinary research pull requests.

Two narrow exceptions cover product boundaries that benefit from a same-repository PR check:

- `.github/workflows/focused-compiler-compat.yml` watches the thunk generator, shared host layout
  helpers, GL thunk sources, their focused tests and the lane itself. It runs two exact char-pointer
  return tests and builds only `GL-host-64` on GitHub's `ubuntu-26.04` preview image with Clang 21.
- `.github/workflows/focused-code-cache.yml` watches the format-3 whole-file parser, the block-blob
  layout parser, the Fossilize index parser and their focused registration/integration paths. It
  compiles the three pure parser targets directly rather than linking the full JIT library, then
  runs only their discovered `*.CodeCacheFile.FEXCore_Tests`, `*.DiskCacheFile.FEXCore_Tests` and
  `*.DiskCacheIndexFile.FEXCore_Tests` cases with the complete LLVM/Clang 18 development package on
  `ubuntu-24.04`. A producer change still gets an explicit affected-source/target build in its
  research receipt; it does not make every parser-only PR compile the Linux emulation stack.

Both refuse external pull-request branches and may be dispatched manually. Neither runs a guest, a
test family or an inherited matrix. The Clang 21 preview image changes weekly and carries no GA SLA,
so that lane's exact source SHA and printed toolchain identity are part of the result rather than an
implicit stable environment claim.
Helper-only changes to `Scripts/ResearchDevBuild.py` do not enqueue this compiler/product lane.
They use `.github/workflows/research-tooling.yml`: one same-repository, path-filtered job that checks
out the exact PR head and runs the closed Python research-tooling test inventory. It does not
initialize submodules, configure CMake, compile FEX, invoke a research profile, or run product
tests. The same bounded inventory is available locally through one command:

```sh
python3 Scripts/RunResearchToolingTests.py
```

Independent test files run with at most four workers by default. Their output is buffered per file
and emitted in inventory order so concurrency does not make the log nondeterministic. A later
relevant product change still runs the compiler lane through the helper at that exact head.

Each research PR should instead report:

- the exact source commit and dirty state;
- the one question being tested;
- the smallest target/test or runtime discriminator that answers it;
- the command, environment/toolchain, result, wall time, and peak RSS when useful;
- a negative control when the claim is that a patch fixes a regression;
- the boundary of what was not tested.

Reuse an existing exact-head result when code, toolchain, inputs, and relevant environment have not changed.

## Shared profile carrier and platform adapters

Both hosted ARM64 and self-hosted x86 experiments use
`Scripts/ResearchProfileCarrier.py`. A profile is immutable product code; it owns setup, variants,
controls, oracle and the small evidence files. The carrier binds the exact product and carrier
commits, requires a clean pinned submodule graph, executes only committed `run.sh` bytes, enforces
the declared timeout, terminates the profile process group on timeout and requires one bounded
outcome document.

GitHub must select a runner before it can read a product profile, so the repository retains two
small scheduling adapters rather than one dynamic workflow:

- `.github/workflows/focused-x86-research.yml` requires the exact
  `self-hosted`, `X64`, `fex-research` labels and passes
  `self-hosted-x86-fex-research` to the carrier;
- `.github/workflows/focused-arm64-research.yml` uses GitHub's `ubuntu-24.04-arm` runner and passes
  that exact platform to the carrier.

A profile for one adapter is refused by the other. Neither adapter accepts a command, script body,
target name, package list, branch-scoped product identity or arbitrary JSON. Add or refine a profile
instead of registering another workflow.

## Focused self-hosted x86 research carrier

`.github/workflows/focused-x86-research.yml` is the default Actions escalation path for x86-host
research. Dispatch it only from the default branch carrier and provide one full immutable product
SHA, one safe profile ID, one declared variant and bounded jobs. The checked-in
`x86-codegen-snapshot-v1` profile is the first concrete contract: it composes three exact atomic
snapshot/host-feature CTests and the `FEX`, `FEXServer`, and `FEXOfflineCompiler` affected targets in
one retained external lane, then copies all six exact-head helper receipts into the carrier artifact.
It runs no guest and is not broad FEX acceptance.

The x86 workflow requires a repository runner carrying all three labels `self-hosted`, `X64`, and
`fex-research`. Check runner availability before dispatch; a missing compatible runner means the job
would only queue and is not evidence:

```bash
gh api repos/teamleaderleo/FEX/actions/runners \
  --jq '.runners[] | {name, status, busy, labels: [.labels[].name]}'

gh workflow run focused-x86-research.yml \
  --repo teamleaderleo/FEX \
  --ref main \
  -f source_sha="$(git rev-parse HEAD)" \
  -f profile=x86-codegen-snapshot-v1 \
  -f variant=default \
  -f jobs=8
```

The selected `--ref` is the default-branch carrier, not a mutable product branch. The immutable
`source_sha` owns the product and profile. The emitted artifact contains the carrier/submodule
receipts plus profile-specific evidence; it is not a broad acceptance badge.

An ARM64 profile must prove a small ordinary x86 control before its product-specific oracle. If the
control traps in JIT code, a base and candidate that die at the same instruction are environment
diagnostics, not an A/B result. A host-feature override is acceptable only when the disabled path is
outside the claim and the receipt names the override; it does not accept the omitted host-feature
path. Prefer moving the product oracle to Glaeda or an installed-FEX host over repeatedly compiling
the same two sources on a hosted VM that cannot pass its control.

## Focused hosted ARM64 research carrier

Use `.github/workflows/focused-arm64-research.yml` when the unresolved fact genuinely requires an
ARM64 host. The manual workflow is registered once on the default branch. A dispatch supplies only:

- one full immutable FEX source SHA;
- one safe ID under `Scripts/ResearchProfiles`;
- one variant declared by that profile; and
- bounded parallelism from 1 through 16 workers.

It does not accept a command, script body, branch name, package list or arbitrary JSON input. The
default-branch carrier is checked out separately from the requested product tree. It verifies the
product's exact HEAD, clean tracked state and complete pinned recursive submodule inventory both
before and after the profile. The product checkout uses the measured bounded parallel submodule
bootstrap and publishes that separate receipt. Both profile files must exist byte-for-byte in the
requested source commit; an untracked local profile is refused. The profile must emit
`profile-outcome.json`; a zero process exit without that bounded pass document is a failure. The
always-uploaded carrier receipt binds both
commits, both checked-in profile-file digests, variant, jobs, timeout, source state, duration and
result.

Add or refine a profile in the product branch instead of adding a workflow file. A profile is:

```text
Scripts/ResearchProfiles/<safe-id>/profile.json
Scripts/ResearchProfiles/<safe-id>/run.sh
```

The manifest declares the exact ID, one supported platform adapter, timeout of at most 55 minutes,
and sorted allowed variants. `run.sh` owns setup, controls, oracle and profile-specific evidence. It
receives fixed `FEX_RESEARCH_*` paths/identities and must write this file inside the private receipt
directory on success:

```json
{"schemaVersion": 1, "status": "pass", "summary": "bounded factual result"}
```

Inspect a profile locally before dispatch:

```bash
python3 Scripts/ResearchProfileCarrier.py inspect \
  --profile arm64-environment-smoke --variant default
```

Then dispatch the workflow from default-branch `main`, while selecting the immutable product SHA
separately:

```bash
gh workflow run focused-arm64-research.yml \
  --repo teamleaderleo/FEX \
  --ref main \
  -f source_sha="$(git rev-parse HEAD)" \
  -f profile=arm64-environment-smoke \
  -f variant=default \
  -f jobs=8
```

The retained smoke profile validates carrier checkout/identity/receipt mechanics only. Reuse its
accepted result until the carrier or runner image changes; it is not FEX product acceptance.

The current `arm64-disk-cache-shapes-v1` profile is the only retained ARM profile that compiles FEX
on the hosted runner. Its carrier restores a compiler cache under `RUNNER_TEMP`; it never restores
an opaque CMake or Ninja build tree. The cache key binds runner OS/architecture, the Clang family,
exact source SHA, profile and variant. Ccache independently binds compiler content and compile
arguments, uses the same `time_macros` policy as the repository's local research loop, and caps the
local cache at 1 GiB. The uploaded receipt distinguishes the Actions restore key from the profile's
cacheable compiler calls, direct/preprocessed hits, misses, cache size, and configure/build wall
times. A restored archive is only a performance input: the profile still configures a fresh build
tree, builds its exact targets, runs its ordinary control and product oracle, and rechecks the exact
source afterward. Do not add another profile to this cache merely because it compiles; first show
that its toolchain and cache namespace are compatible and that reuse saves more time than archive
transfer.

## Requesting inherited broad CI deliberately

The inherited broad workflows are manual-only in this fork. Dispatch one relevant workflow from
GitHub Actions after confirming that a compatible runner exists. There is intentionally no label
that fans every platform matrix out at once.

The upstream pull-request formatter is not registered in this fork: its implementation is tied to
upstream's runner and writes to `FEX-Emu/FEX`. Run a focused local formatter check when a changed
source file needs it instead of creating an owned-fork job that can only skip.

Do this only after stating which unresolved risk needs that coverage. A queued self-hosted job is not evidence until a compatible runner actually owns and completes it.

## Disposable workflow lifecycle

One-off experiment workflows remain registered in the Actions UI even when their branch is no
longer active. Prefer a checked-in profile so no new workflow registration is created. If the
shared carrier and platform adapters genuinely cannot express the required platform or permission
boundary, record the one-off result receipt first, then retire its registration. The registry
helper is dry-run by default and protects workflow paths present on the default branch or any open
pull-request head:

```bash
python3 Scripts/ForkWorkflowRegistry.py \
  --repo teamleaderleo/FEX \
  --before 2026-08-16T00:00:00Z
```

Inspect the plan and repeat the exact command with `--apply` to disable its candidates. Use `--keep-path .github/workflows/example.yml` for an additional explicit exception. Disabling is reversible and does not delete branches, workflow files, logs, artifacts, or recorded receipts.

Prefer adding a bounded checked-in profile over creating another near-duplicate workflow. If a
one-off workflow is still the clearest platform or permission boundary, give it a bounded question
and retire it when that question is answered.

## Relationship to upstream

The fork workflow triggers intentionally diverge from upstream; no upstream repository state is changed. When refreshing from upstream, retain the fork's focused/manual-dispatch policy unless the owner deliberately changes it.

This policy changes scheduling, not product source. It does not turn a focused x86 generator check into ARM runtime acceptance, and it does not authorize interaction with upstream GitHub state.
