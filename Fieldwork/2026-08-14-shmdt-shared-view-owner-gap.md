# shmdt shared-view owner gap — 2026-08-14

Pinned FEX: `71afe476751deac24adabd1adb575fd2337b6e0a`
Carrier branch: `ci/shmdt-view-owner-20260814`
Carrier commit: `7acba0a703b064ab5a3c169050e0e5e3ad4bf74b`
Workflow run: `31793884656`
Artifact: `shmdt-view-owner-31793884656`
Artifact digest: `sha256:0aad6c73bc20c2fa46c8919b2518c209816c589be4c886bb61c1da0595cd71cb`

## Result

All discriminator assertions passed:

```text
inspect=0
no-reregister=0
reregister=0
```

The fixture creates one SysV SHM resource with three attachments: a RW view, an executable `old` view, and a second executable `new` view. `H=0x700000060000` is registered to `old`; both executable views initially run `111`.

After `shmdt(old)`, the RW and `new` attachments remain alive. The detached `old` VA is then reused for unrelated anonymous executable code returning `333`.

No-reregister receipt:

```text
DIAG_OWNER_CLAIM_ACTIVE H=0x700000060000 T=0x7ffff7ec3000 owner=0xe new=1
SHMDT_VIEW detached-old old=0x7ffff7ec3000 surviving-rw=0x7ffff7ec4000 surviving-exec=0x7ffff7ec0000
SHMDT_VIEW reused-old old=0x7ffff7ec3000 direct=333 surviving-exec-value=111
SHMDT_VIEW final H-value=333 reregister=0 expected-current-gap=333
```

Explicit-reregister receipt:

```text
DIAG_OWNER_CLAIM_STANDBY H=0x700000060000 T=0x7ffff7ec0000 owner=0xe new=1
SHMDT_VIEW reregister H=0x700000060000 T-new=0x7ffff7ec0000
SHMDT_VIEW final H-value=333 reregister=1 expected-current-gap=333
```

## Conclusion

VMA/resource owner identity is too coarse for SHM attachment lifetime. Detaching one executable view can destroy the executable target at an address while another attachment keeps the same mapped resource and owner ID alive. The old H claim therefore survives and follows address reuse; an explicit claim to the surviving executable attachment is held standby behind it.

This is the same conceptual class exposed by `MREMAP_DONTUNMAP`: resource/mapping-owner lifetime and executable-content-at-address lifetime can diverge.

The repair hook should retire the detached attachment's address range before `shmdt`, with prepare/commit/rollback semantics. The attachment length must be queried from VMA/SHM tracking before the kernel removes the view.