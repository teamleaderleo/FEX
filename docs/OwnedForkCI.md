# CI policy for the owned research fork

The upstream FEX workflows are designed for the upstream project's always-on self-hosted runner fleet. This fork is used differently: one owner and multiple research agents run narrow experiments locally, on big-red/Glaeda, or in an explicitly selected Actions lane.

## Default PR behavior

Opening or updating a pull request does **not** enqueue the broad build, GLIBC fault, hostrunner, instruction-count, MinGW, Steam Runtime, Wine DLL, or VIXL matrices in `teamleaderleo/FEX`. Those matrices cover unrelated platforms and can consume many runner-hours even for a deterministic generator or documentation change. They also do not create skipped check-suite noise on ordinary research pull requests.

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
an x86 guest under FEX. Use a branch-scoped ARM64 workflow when the unresolved fact is runtime
behavior rather than x86-host compilation.

The workflow requires a repository runner carrying all three labels `self-hosted`, `X64`, and
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

## Requesting inherited broad CI deliberately

The inherited broad workflows are manual-only in this fork. Dispatch one relevant workflow from
GitHub Actions after confirming that a compatible runner exists. There is intentionally no label
that fans every platform matrix out at once.

The upstream pull-request formatter is not registered in this fork: its implementation is tied to
upstream's runner and writes to `FEX-Emu/FEX`. Run a focused local formatter check when a changed
source file needs it instead of creating an owned-fork job that can only skip.

Do this only after stating which unresolved risk needs that coverage. A queued self-hosted job is not evidence until a compatible runner actually owns and completes it.

## Disposable workflow lifecycle

One-off experiment workflows remain registered in the Actions UI even when their branch is no longer active. Record the result receipt first, then retire the registration. The registry helper is dry-run by default and protects workflow paths present on the default branch or any open pull-request head:

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
