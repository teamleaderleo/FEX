# Contributing to this research fork

AI-assisted coding, instrumentation, experiments, tests, workflows, CI jobs, and research are allowed in `teamleaderleo/FEX`.

This repository is an owned experimental fork. Fork-local contributors and agents may create disposable branches, modify source and harnesses, launch or rerun GitHub Actions workflows, inspect artifacts, and retain diagnostic commits when that work advances an investigation.

Research commits and CI machinery in this fork are not upstream FEX submissions. When an experiment claims to test an exact upstream/current FEX revision, record the exact product revision and distinguish workflow-only or policy-only fork changes from product-source changes.

The human owner decides if any work becomes an upstream candidate. Any future contribution to the upstream FEX project must be independently prepared in accordance with the upstream repository's current contribution policy at the time of submission and must not be represented as upstream-ready merely because it was useful in this fork.

Fork write and CI authority does not authorize interaction with upstream or other third-party repositories. Do not open, edit, comment on, review, react to, dispatch workflows in, or otherwise contact upstream FEX without explicit human authorization.

For focused x86-host research builds in this fork, see
[`docs/ResearchDevLoop.md`](docs/ResearchDevLoop.md). It keeps build state outside the worktree,
declares one exact target, and records a receipt without implying ARM runtime or full-suite acceptance.

For the plain-language separation between Vulkan routing, compiled CustomIR retirement, and escaped
callback-code lifetime, see [`docs/LinuxFieldworkLifetimeMap.md`](docs/LinuxFieldworkLifetimeMap.md).
