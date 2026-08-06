# Secure boot on RK3576 — a plan

Secure boot is the layer this system does not have. Without it the documented
boundary stops at "while the bootloader and BL32 are intact", because anyone
who can write the eMMC's first sectors runs their own BL32 at S-EL1 and reads
the HUK directly.

Two fuse fields turn it on, both in the secure OTP and both already surveyed as
blank on the boards here:

| words | field |
| --- | --- |
| `0x8` | secure boot status |
| `0x184`-`0x187` | RSA key hash, 16 bytes |

Getting either wrong is unrecoverable. Enabling secure boot with no key burned
leaves a part the boot ROM will not load anything into.

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

Nothing is fused until stage 4, and each stage has to pass before the next.

### 1. Establish the facts

- Read words `0x8` and `0x184`-`0x187` on the target board with the existing
  OTP survey (`optee/0003`) and confirm both are blank.
- Settle the recovery question before anything else: **does maskrom still
  accept an unsigned SPL loader once secure boot is on?** On some Rockchip
  parts it does not, which turns a bad signed image into a dead board rather
  than a reflash. If it does not, a signed loader has to exist and be kept
  alongside the keys before stage 4.
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

### 4. Fuse, in order, on a board that can be lost

- Key hash first, into `0x184`-`0x187`. Reuse the HUK write path from
  `optee/0013`: it already refuses a partially programmed slot, reads back
  what it wrote, and aborts if the read does not match.
- Reboot and confirm the board still boots. The hash alone changes nothing -
  the ROM does not check until the enable bit is set, so this is the reversible
  half of the operation in practice.
- Only then set the enable bit in word `0x8`.
- Confirm the signed image boots, and that an unsigned one is refused. The
  second half matters: a board that boots either image has secure boot fused
  but not working.

### 5. Live with it

- Every bootloader update is now a signed build. `fwup`'s write at sector 64
  carries a signed idblock or the board stops booting, so the signing step
  belongs in `scripts/build-uboot.sh` rather than in someone's shell history.
- Key loss is fleet loss. There is no path back on a fused part.
- The extraction boundary in the README can then be widened, and only then.

## What this needs decided

- **A board that can be lost.** Stage 4 is the first irreversible step and it
  is best done on hardware nobody minds bricking. Both boards here are in use,
  and one has a fused HUK plus a provisioned NervesHub identity.
- **Where the signing key lives**, given it cannot be rotated after fusing.
- **Whether this is for the example or a fleet.** For the example, stages 1-3
  are the valuable part: they produce a signed image and a verified fuse value
  without touching a fuse.
