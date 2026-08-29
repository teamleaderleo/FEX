# Owned-fork agent policy

This `teamleaderleo/FEX` repository is an owned experimental fork. The human owner has explicitly authorized broad autonomous research work here.

Agents may create, edit, delete, commit, and iterate on source code, tests, instrumentation, workflows, CI jobs, fixtures, experiment branches, generated test material, and documentation when doing so advances the current investigation. Internal research branches may contain temporary commits, diagnostic hacks, failed experiments, throwaway helpers, or other research churn. Do not spend effort making ordinary research history look upstream-ready.

## Workflow and CI authority

The human owner explicitly authorizes research execution through GitHub Actions and other CI surfaces in repositories and forks owned by `teamleaderleo`.

Agents may create disposable workflow branches, add or edit workflow files, trigger workflows by authorized pushes or dispatches, rerun failed jobs, iterate on harnesses, download and inspect artifacts/logs, and delete or abandon temporary CI machinery when it has served its purpose. This includes ARM64 runners and other owned-fork CI used to reproduce, instrument, or discriminate bugs.

Read `docs/OwnedForkCI.md` before scheduling validation. Broad inherited matrices are opt-in in this fork; prefer the smallest exact-head target or runtime discriminator that answers the open question, and record its scope and receipt.

Keep runtime provenance precise: when an experiment is meant to test an exact FEX product revision, build product source from that exact revision or clearly document any source delta. Policy-only or workflow-only fork commits must not be described as product-source changes.

Workflow authority is limited to owned repositories/forks. It does not authorize writes, workflow dispatches, comments, reactions, reviews, issues, pull requests, or other interaction in third-party/upstream repositories.

AI-assisted coding and experimentation are allowed in this fork. Code created here must not be represented as an upstream FEX contribution merely because it exists here.

The human owner controls the boundary where internal research is converted into an upstream submission. If the human later designates a specific branch or commit series as an upstream candidate, prepare that candidate according to the upstream repository's then-current contribution requirements and the human's instructions. Until that designation, optimize for useful evidence and experimentation rather than candidate-history cleanliness.

External-interaction and backlink hygiene remains separate from fork write authority. Do not create accidental third-party GitHub backlinks or timeline events, and do not open, edit, comment on, review, react to, or otherwise contact upstream FEX unless the human explicitly authorizes that upstream interaction.

A later human instruction may narrow or revoke this authority.
