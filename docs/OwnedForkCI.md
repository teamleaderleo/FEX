# CI policy for the owned research fork

The upstream FEX workflows are designed for the upstream project's always-on self-hosted runner fleet. This fork is used differently: one owner and multiple research agents run narrow experiments locally, on big-red/Glaeda, or in an explicitly selected Actions lane.

## Default PR behavior

Opening or updating a pull request does **not** enqueue the broad build, GLIBC fault, hostrunner, instruction-count, MinGW, Steam Runtime, Wine DLL, or VIXL matrices in `teamleaderleo/FEX`. Those matrices cover unrelated platforms and can consume many runner-hours even for a deterministic generator or documentation change. They also do not create skipped check-suite noise on ordinary research pull requests.

One narrow exception is `.github/workflows/focused-compiler-compat.yml`. A same-repository pull
request that changes the thunk generator, shared host layout helpers, GL thunk sources, their focused
tests, or the lane itself runs two exact char-pointer return tests and builds only `GL-host-64` on
GitHub's `ubuntu-26.04` preview image with Clang 21. External pull-request branches are refused.
The job may also be dispatched manually. It does not run a guest, a test family, or any inherited
matrix. The preview image changes weekly and carries no GA SLA, so its exact source SHA and printed
toolchain identity are part of the result rather than an implicit stable environment claim.

Each research PR should instead report:

- the exact source commit and dirty state;
- the one question being tested;
- the smallest target/test or runtime discriminator that answers it;
- the command, environment/toolchain, result, wall time, and peak RSS when useful;
- a negative control when the claim is that a patch fixes a regression;
- the boundary of what was not tested.

Reuse an existing exact-head result when code, toolchain, inputs, and relevant environment have not changed.

## Focused self-hosted research lane

`.github/workflows/focused-x86-research.yml` is the default Actions escalation path for x86-host
research. It is manual-only and accepts two bounded modes:

- `build` compiles one exact CMake target with the `dev` profile;
- `linux-test-build` builds `FEX`, `FEXServer`, and one exact 32- or 64-bit guest Linux-test binary.

Both modes reuse `Scripts/ResearchDevBuild.py`, its stable external build path, CPU-bound ccache
namespace, source-switch cleanup, lane lock, and exact-head receipt. They do not run a test suite or
an x86 guest under FEX.

The x86 workflow requires a repository runner carrying all three labels `self-hosted`, `X64`, and
`fex-research`. Check runner availability before dispatch; a missing compatible runner means the job
would only queue and is not evidence:

```bash
gh api repos/teamleaderleo/FEX/actions/runners \
  --jq '.runners[] | {name, status, busy, labels: [.labels[].name]}'

gh workflow run focused-x86-research.yml \
  --repo teamleaderleo/FEX \
  --ref MY_EXACT_BRANCH \
  -f mode=build \
  -f target=thunkgentest \
  -f bitness=64 \
  -f jobs=8
```

The selected `--ref` is the product source revision. The emitted artifact is a convenience copy of
the helper's receipt, not a broad acceptance badge.

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

The manifest declares the exact ID, fixed `ubuntu-24.04-arm` platform, timeout of at most 55
minutes, and sorted allowed variants. `run.sh` owns setup, controls, oracle and profile-specific
evidence. It receives fixed `FEX_RESEARCH_*` paths/identities and must write this file inside the
private receipt directory on success:

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
longer active. Prefer a checked-in ARM64 profile so no new workflow registration is created. If the
single carrier genuinely cannot express the required platform or permission boundary, record the
one-off result receipt first, then retire its registration. The registry helper is dry-run by
default and protects workflow paths present on the default branch or any open pull-request head:

```bash
python3 Scripts/ForkWorkflowRegistry.py \
  --repo teamleaderleo/FEX \
  --before 2026-08-16T00:00:00Z
```

Inspect the plan and repeat the exact command with `--apply` to disable its candidates. Use `--keep-path .github/workflows/example.yml` for an additional explicit exception. Disabling is reversible and does not delete branches, workflow files, logs, artifacts, or recorded receipts.

Prefer extending a reusable focused lane over creating another near-duplicate workflow. If a one-off workflow is still the clearest discriminator, give it a bounded question and retire it when that question is answered.

## Relationship to upstream

The fork workflow triggers intentionally diverge from upstream; no upstream repository state is changed. When refreshing from upstream, retain the fork's focused/manual-dispatch policy unless the owner deliberately changes it.

This policy changes scheduling, not product source. It does not turn a focused x86 generator check into ARM runtime acceptance, and it does not authorize interaction with upstream GitHub state.
