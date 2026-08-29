# Why a fixed remap must invalidate its destination

This note describes one focused FEX code-cache repair. It is separate from callback ownership,
wrapper unloading, synthetic entry retirement and general virtual-memory lifetime proposals.

## The two states that can disagree

An executable guest page has at least two relevant representations:

1. Linux maps x86 instruction bytes at a numeric guest virtual address.
2. FEX may cache translated host instructions for that numeric guest address.

Changing the Linux mapping does not inherently remove the second representation. The syscall path
must tell the translation manager which guest address ranges no longer describe the bytes that were
translated.

Before this change, a moved `mremap` invalidated the old source range. That is sufficient when Linux
chooses a previously free destination. It is not sufficient for `MREMAP_FIXED`, whose documented
operation replaces an existing destination mapping.

The failing sequence is:

1. Put code returning `0x11111111` at destination address D and execute D so FEX translates it.
2. Put code returning `0x22222222` at source address S.
3. Successfully call `mremap(S, page, page, MREMAP_MAYMOVE | MREMAP_FIXED, D)`.
4. Linux now maps S's bytes at D, but FEX can still find D's old translation.

On current owned-fork main `8690e1bfc`, the focused ARM64 negative control passed six setup/kernel
assertions and then returned stale `0x11111111` at step 4. Native x86_64 execution of the same test
returned `0x22222222` as Linux semantics require.

## The bounded repair

After a successful remap, the syscall handler now passes the original flags to
`InvalidateCodeRangeIfNecessaryOnRemap`. When the mapping moved and `MREMAP_FIXED` is present, the
helper invalidates `[new_address, new_address + new_size)` in addition to the existing old-range
invalidation.

The flag check matters. Invalidating every moved destination, as the earlier research candidate did,
would do unnecessary translation-manager work for ordinary `MREMAP_MAYMOVE` calls where Linux chose
a free range. The kernel has already accepted the syscall before this helper runs, so the repair does
not invalidate a requested fixed destination when the remap failed.

## Evidence boundary

`smc-2.64` is the focused runtime oracle. It warms D, performs the fixed replacement, and checks the
same function pointer without using `mprotect` or another operation that could independently flush
the code cache. The owned ARM64 workflow builds only `FEX`, `FEXServer`, this guest test binary and
their build prerequisites, then runs only the named Catch2 case.

This proves destination translation invalidation for the tested 64-bit fixed-remap sequence. It does
not prove callback-owner retirement, concurrent execution safety, `MREMAP_DONTUNMAP` ownership,
32-bit allocator behavior or all SMC modes. Those require their own discriminators rather than being
folded into this small repair.
