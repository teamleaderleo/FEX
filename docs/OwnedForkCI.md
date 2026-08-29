# CI policy for the owned research fork

The upstream FEX workflows are designed for the upstream project's always-on self-hosted runner fleet. This fork is used differently: one owner and multiple research agents run narrow experiments locally, on big-red/Glaeda, or in an explicitly selected Actions lane.

## Default PR behavior

Opening or updating a pull request does **not** enqueue the broad build, GLIBC fault, hostrunner, instruction-count, MinGW, Steam Runtime, or VIXL matrices in `teamleaderleo/FEX`. Those matrices cover unrelated platforms and can consume many runner-hours even for a deterministic generator or documentation change.

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

1. Add the `ci:full` label to a pull request. The label event triggers every broad workflow, and later pushes continue to trigger them while the label remains.
2. Dispatch one workflow manually from GitHub Actions. `workflow_dispatch` is available on each broad workflow, so a single relevant lane can be selected without labeling the PR for all matrices.

Do this only after stating which unresolved risk needs that coverage. A queued self-hosted job is not evidence until a compatible runner actually owns and completes it.

## Relationship to upstream

The job condition preserves upstream behavior when the repository is `FEX-Emu/FEX`. The fork-only gate is maintained alongside the owned-fork policy documents and may remain as a small, intentional divergence when upstream is merged.

This policy changes scheduling, not product source. It does not turn a focused x86 generator check into ARM runtime acceptance, and it does not authorize interaction with upstream GitHub state.
