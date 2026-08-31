# FEX owned-fork agent router

`teamleaderleo/FEX` is an owned experimental fork. Broad reversible internal research is authorized: inspect, edit, test, commit, push owned branches, add disposable diagnostics/workflows/CI, and iterate on failed experiments when they advance the investigation. Internal history need not be upstream-ready.

## Always preserve

- Owned-fork authority does not authorize third-party/upstream writes, workflow dispatches, comments, reviews, reactions, issues, PRs, backlinks, or other contact. Upstream interaction requires explicit authorization.
- Bind runtime/product claims to the exact FEX source revision actually built or tested, and describe any source delta. Policy/workflow-only fork commits are not product-source changes.
- Focused x86 host readiness is not ARM64 FEX runtime evidence. Before local C++ work, use `./Scripts/ResearchDevBuild.py doctor`; it is a read-only preflight, not a build/test/install claim.
- **Never run `git submodule deinit` as linked-worktree cleanup.** FEX worktrees share superproject submodule registration. For cleanup, load `docs/ResearchDevLoop.md`, prove the exact worktree/ownership/cleanliness/disposition, remove the worktree through Git, then rerun `doctor` from the canonical checkout.
- Code produced in this fork is not an upstream FEX contribution by existence. If the human later designates an upstream candidate, follow the then-current upstream requirements and explicit instructions.
- A later human instruction may narrow or revoke this authority.

## Route before reading

Load only what the task needs:

- C++ development, doctor semantics, worktree/submodule cleanup -> `docs/ResearchDevLoop.md`;
- current owned-fork bug families and merged-vs-hypothesis boundary -> `docs/OwnedForkResearchMap.md`;
- which owned-fork PRs to learn / composition order -> `docs/OwnedForkLearningPath.md`;
- CI or ARM64 target selection -> `docs/OwnedForkCI.md`;
- complete authority/upstream rationale -> `docs/OwnedForkAgentPolicy.md`.

Do not read every PR chronologically. Prefer the smallest exact-head test or runtime discriminator that answers the question; broad inherited CI matrices are opt-in.

## Delivery

Use ordinary Git/local work when it answers the question; disposable owned-repository CI is an experimental carrier, not mandatory machinery. Record the exact source/architecture and scope of evidence. Keep external interaction separate from internal research authority.
