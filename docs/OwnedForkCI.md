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

## Requesting broad CI deliberately

There are two explicit routes:

1. Add the `ci:full` label to a pull request. The label event triggers every broad workflow once. After a later push, remove and re-add the label if the new exact head genuinely needs the same broad coverage.
2. Dispatch one workflow manually from GitHub Actions. `workflow_dispatch` is available on each broad workflow, so a single relevant lane can be selected without labeling the PR for all matrices.

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

The fork workflow triggers intentionally diverge from upstream; no upstream repository state is changed. When refreshing from upstream, retain the fork's label/manual-dispatch trigger policy unless the owner deliberately changes it.

This policy changes scheduling, not product source. It does not turn a focused x86 generator check into ARM runtime acceptance, and it does not authorize interaction with upstream GitHub state.
