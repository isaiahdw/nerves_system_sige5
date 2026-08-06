# Secure boot on RK3576 — a plan

Secure boot is the layer this system does not have. Without it the documented
boundary stops at "while the bootloader and BL32 are intact", because anyone
who can write the eMMC's first sectors runs their own BL32 at S-EL1 and reads
the HUK directly.

Two fuse fields turn it on, both in the secure OTP:

| words | field | surveyed state |
| --- | --- | --- |
| `0x8` | secure boot status | **not blank** - `0x008..0x00b` reads non-zero, mirrored at `0x1c8` |
| `0x184`-`0x187` | RSA key hash, 16 bytes | blank |

Getting either wrong is unrecoverable. Enabling secure boot with no key burned
leaves a part the boot ROM will not load anything into.

`0x8` is a live configuration word, not an empty slot. The survey reads the
same value on both boards here, and the same value again at its `0x1c8` mirror,
so it already carries settings the part depends on. Only the enable bit within
it may be written, and its position is not established. Programming the word as
though it were blank would set every bit in it, permanently, in a word the boot
ROM reads.

## What the fuses cover

The fuses buy one link: the boot ROM verifies the idblock — TPL plus SPL — at
sector 64 against the burned key hash. Everything above that link is verified
only if something in the chain is built to verify it.

| stage | verified by | present today |
| --- | --- | --- |
| idblock (TPL + SPL) | boot ROM, against the fused hash | after stage 5 |
| FIT: TF-A, OP-TEE, U-Boot proper | SPL, `CONFIG_SPL_FIT_SIGNATURE` | no |
| kernel + DTB | U-Boot, `CONFIG_FIT_SIGNATURE` on a signed kernel FIT | no |
| rootfs | dm-verity root hash carried in the signed FIT | no |
| app partition | nothing — it is read-write by design | n/a |

Stopping after the first row is a chain that verifies its own bootloader and
then loads an unsigned kernel, which is worth nothing against an attacker who
can write the eMMC. Every row down to the rootfs has to be closed for the
extraction boundary in the README to widen.

Two more things gate the same claim, neither of them a fuse:

- **The U-Boot console.** An interactive prompt runs `bootm` on any image the
  operator points it at, which walks around every row above. It has to be shut:
  no console entry, no `bootcmd` override from the environment.
- **The environment.** `fwup` and the running system can both write sector
  30720. A boot command assembled from writable storage is an unsigned input to
  a signed chain, so the environment has to be either read-only to the OS or
  ignored in favour of a built-in `bootcmd`.

## Rollback

Fuses are not a version. A signed image stays signed after the bug in it is
found, so anyone who can write the eMMC can put an older signed build back and
attack that instead. Nothing in this plan prevents it. If it matters, the
counter has to live somewhere the normal world cannot rewind — an RPMB counter
checked by SPL, or an OTP word burned per revision — and that is a design of
its own, not a step in this one.

## What signs what

Rockchip's `rk_sign_tool` (rkbin, x86-64 static) is the tool that matches the
boot ROM:

```
sign_tool kk  [--bits=2048]        generate a key pair
sign_tool sl  <--loader>           sign the loader (idblock: TPL + SPL)
sign_tool si  <--img>              sign an image (the U-Boot FIT)
sign_tool otp <--loader> [--hash]  extract the OTP data from a signed loader
```

That last one is what makes this tractable: the exact bytes the ROM compares
against come out of the tool rather than being derived by hand.

## Stages

Nothing is fused until stage 5, and each stage has to pass before the next.
The chain above the fuses is closed first: a fused board that cannot boot an
updated bootloader has no way back, so every part of it is built and tested
while the part is still recoverable.

### 1. Establish the facts

- Read `0x184`-`0x187` on the target board with the existing OTP survey
  (`optee/0003`) and confirm it is blank.
- **Decode `0x8` before anything else.** It is already programmed. Establish
  which bit is the secure-boot enable, from Rockchip's own OTP driver or their
  `rk_sign_tool` output, and confirm that bit specifically reads clear. A plan
  that writes the whole word is a plan to brick the part.
- Settle the recovery question before anything else: **does maskrom still
  accept an unsigned SPL loader once secure boot is on?** On some Rockchip
  parts it does not, which turns a bad signed image into a dead board rather
  than a reflash. If it does not, a signed loader has to exist and be kept
  alongside the keys before stage 5, and `rkdeveloptool db` has to be proven
  against it — a recovery path that has never been run is not a recovery path.
- Confirm the hash is 16 bytes as the driver's whitelist implies, and what it
  is a hash of, by comparing `sign_tool otp --hash` output against the key.

### 2. Sign, without fusing

- Generate the key with `sign_tool kk`, kept off devices and out of the
  repository - the same custody as the TA key, and a stronger case for an HSM
  or an offline machine, because this one cannot be rotated on a fused part.
- Sign the loader and the FIT for the existing `SECURE_WORLD=1` build.
- Flash the signed image to a board with secure boot **off** and confirm it
  boots. A signed image must boot on an unfused part; if it does not, the
  signing is wrong and fusing it would brick the board.

### 3. Prove the pair

- `sign_tool otp --loader` against the signed image, and keep that output.
- Verify it independently: hash the public key the same way and check it
  matches, so the value going into the fuses is confirmed twice.

### 4. Close the rest of the chain

The fuses verify one link; the rows in "What the fuses cover" are the rest.
Signed FIT for TF-A/OP-TEE/U-Boot, signed kernel FIT, dm-verity for the rootfs,
console and environment locked. None of it needs a fuse, so all of it is built
and proven here, on an unfused part that can still be reflashed.

Negative testing belongs here too, while failure is recoverable: a tampered
kernel FIT must be refused, a modified rootfs must fail verity, and the console
must not offer a prompt.

### 5. Fuse, in order, on a board that can be lost

- Key hash first, into `0x184`-`0x187`. Reuse the HUK write path from
  `optee/0013`: it reads back what it wrote and aborts if the read does not
  match. Its read path catches a burn interrupted between words, but not one
  interrupted inside the last word, so an interrupted key-hash burn is suspect
  rather than safely rejected.
- Reboot, then read `0x184`-`0x187` back on the running system and compare
  against the stage 3 output. The write path's own read-back happens while the
  OTP controller is still hot from the burn; a value that only survives that
  and not a power cycle is exactly the failure the enable bit makes permanent.
- Only then set the enable bit in word `0x8` - that one bit, through a
  read-modify-write that preserves every other bit in the word, and never a
  whole-word program.
- Confirm the signed image boots, and that an unsigned one is refused. The
  second half matters: a board that boots either image has secure boot fused
  but not working.

### 6. Live with it

- Every bootloader update is now a signed build. `fwup`'s write at sector 64
  carries a signed idblock or the board stops booting, so the signing step
  belongs in `scripts/build-uboot.sh` rather than in someone's shell history.
- Key loss is fleet loss. There is no path back on a fused part.
- The extraction boundary in the README can be widened once stage 5 is done.

## What this needs decided

- **A board that can be lost.** Stage 5 is the first irreversible step and it
  is best done on hardware nobody minds bricking. Both boards here are in use,
  and one has a fused HUK plus a provisioned NervesHub identity.
- **Where the signing key lives**, given it cannot be rotated after fusing.
- **Whether rollback is in scope**, since it changes stage 4 rather than being
  added afterwards.
- **Whether this is for the example or a fleet.** For the example, stages 1-3
  and 5 are the valuable part: they produce a signed, verified-all-the-way-up
  image without touching a fuse.
