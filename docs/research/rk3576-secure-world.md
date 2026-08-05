# RK3576 secure world

What the secure world on this SoC consists of, what is in its one-time
programmable memory, and how this firmware uses it.

## Address map

From TF-A's `plat/rockchip/rk3576/rk3576_def.h`, plus two addresses it omits.

| block | base | notes |
| --- | --- | --- |
| `CRYPTO_NS` | `0x2a400000` | `crypto@2a400000` in the device trees |
| `RKRNG_NS` | `0x2a410000` | `rng@2a410000`, `HCLK_TRNG_NS` |
| `KEYLADDER` | `0x2a420000` | |
| `CRYPTO_S` | `0x2a430000` | |
| `RKRNG_S` | `0x2a440000` | not in any public source; see below |
| `OTP_S` | `0x2a480000` | secure OTP, `0x200` words |
| `DCF` | `0x2a490000` | |
| `STIMER0/1` | `0x2a4a0000` / `0x2a4b0000` | |
| `WDT_S` | `0x2a4c0000` | |
| `OTP_MASK` | `0x2a4d0000` | |
| `OTP_NS` | `0x2a580000` | `otp@2a580000`, 256 bytes, readable by Linux |

`OTP_S` is genuinely firewalled. Reading it from the U-Boot prompt, which runs
non-secure, external-aborts:

```
=> md.l 0x2a480000 4
"Synchronous Abort" handler, esr 0x96000010, far 0x2a480000
```

`RKRNG_S` behaves the same way, which is how it was distinguished from a mirror
of the non-secure block: the secure world gets fresh data from `0x2a440000` on
every request, and `md.l 0x2a440014` from U-Boot aborts where `0x2a410014`
reads back normally. TF-A names `FW_SLV_ID_RKRNG_S` as its own firewall slave
but never gives its address; it was found by trying the four gaps in the secure
block and confirmed on two dies.

## Secure OTP layout

Rockchip's U-Boot ships `drivers/misc/rk3576-secure-otp.S` as generated
assembly. Its whitelist survives as three range checks on byte offsets, and
those are the only range constants in the file - each appearing once on the
read path and once on the write path, so the list is complete.

| bytes | words | field |
| --- | --- | --- |
| 32-33 | `0x8` | secure boot status |
| 512-575 | `0x80`-`0x8f` | HUK key material |
| 1552-1567 | `0x184`-`0x187` | RSA key hash |

The index is in 32-bit words: `ROCKCHIP_OTP_HUK_SIZE` is `0x4` and OP-TEE's
`HW_UNIQUE_KEY_LENGTH` is 16 bytes. The array is `0x200` words, not the `0x300`
RK3588 uses.

Note that driver is normal-world code in `drivers/misc/` and contains no `smc`
- it reaches the OTP controller directly, and permits reads of the HUK range as
well as writes. For that to work, the vendor firmware must leave `OTP_S`
reachable from the normal world. It is not reachable under the firmware built
here, where the access aborts.

## What is actually programmed

Surveyed across the whole `0..0x200` range on two Sige5 boards, reporting a
FNV-1a hash per four-word group. Three groups are non-zero and the rest read
zero:

```
otp[0x008..0x00b]  fnv1a=0x9d164c95
otp[0x064..0x067]  fnv1a=0x8f784b0d
otp[0x1c8..0x1cb]  fnv1a=0x9d164c95
```

Both boards give identical hashes at all three, while their `cpuid#` differ
(`...0d0c12` and `...0e2612`), so none of it is per-device. `0x008` and `0x1c8`
hold the same content - a config word and a mirror, the redundancy OTP uses for
values that must survive a bit flip. This is not read aliasing: a `0x3f` address
mask would have produced twelve hits, and a `0x1c0` period would have repeated
`0x064` at `0x224`; neither appears.

`0x080` is blank in all four words, and `0x184` is blank, consistent with secure
boot being off.

The normal-world OTP is the opposite - fully readable, and holding the chip ID,
a lot code, a serial and the PVTM/leakage values:

```
+0x000: 52 4B 35 76 22 00 FF 00 01 01 4E 59 37 55 33 00
+0x010: 00 00 00 00 00 00 00 0D 0C 12 00 00 00 00 05 04
+0x020: 05 06 0D 00 21 01 21 01 21 01 22 01 21 01 1E 00
+0x060: 00 00 00 00 21 01 00 00 00 00 00 00 00 00 00 00
+0x0F0: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 0F
```

U-Boot derives `cpuid#` from bytes `+0x0a`, and `serial#` and `ethaddr` from
that. Per-device identity exists on these parts, lives where any process can
read it, and has no secret counterpart in the secure OTP.

## The HUK

RK3576 ships with no hardware unique key. That is by design rather than an
oversight on these units: Rockchip's stack carries `trusty_write_oem_huk` and
`trusty_oem_otp_key_is_written` because the key is OEM-written, not
factory-burned.

OP-TEE reads four words at `0x80` and requires all four to be non-zero. A slot
with some words programmed and some not means a burn that did not finish, and
since OTP bits only go 0 to 1 such a slot can never be completed or rewritten;
accepting it would key secure storage on a short key while everything
downstream appeared to work. A genuine random key trips that check with
probability about 4 x 2^-32.

Until a key is fused, `tee_fs_init_key_manager` fails and secure storage does
not initialise. That is the current state on both boards, and it is why the
PKCS#11 TA loads but cannot open a session.

## Random numbers

`hw_get_random_bytes()` drives RKRNG at `RKRNG_S`, the secure instance. A
request is written to
`CTRL` hiword-masked, `TRNG_RDY` comes up in `STATE`, and 32 bytes appear at
`DATA0`. The data registers hold the last block until something asks for
another, so the implementation requests once more before returning and leaves
behind a block no caller was given.

`plat_init_soft_prng()` seeds the software PRNG with 64 bytes from it. A TRNG
that cannot be read is fatal: OP-TEE's default seeding is a boot timestamp,
described in its own source as "not suitable for a secure environment", and
every key the secure world derives would inherit that.

Quality, from 1 MB through `/dev/hwrng`:

| test | result | ideal |
| --- | --- | --- |
| distinct byte values | 256 of 256 | 256 |
| chi-square, 255 df | 278.9 | 205-310 unremarkable |
| ones proportion | 0.499947 (z = -0.31) | 0.5 |
| Shannon entropy | 7.99981 bits/byte | 8.0 |
| serial correlation | +0.000257 | 0 |
| zlib ratio | 1.0003 | above 1 |
| longest equal-byte run | 3 | - |
| repeated 16-byte blocks | 0 of 65536 | 0 |

Those figures come from the non-secure instance, because `/dev/hwrng` is the
only one Linux can bind - the secure block is unreachable from the normal world
by design. Both are the same IP on the same die. At 7.9998 bits/byte the
64-byte seed carries far more than the 256 bits it was sized for on a
conservative 4 bits/byte assumption.

Direct evidence about `RKRNG_S` itself: it returns fresh data on every request
on both dies, and every candidate drawn from it has passed the checks below,
with no two alike across six draws.

## Burning a HUK

Nothing in this repository has been fused.

```sh
USE_OPENSOURCE_TEE=1 PERSIST_HUK=1 ./scripts/build-uboot.sh
```

`CFG_RK3576_PERSIST_HUK` is off by default, and a build with it on programs the
first unprovisioned part it boots - there is no per-board confirmation. Such an
image is a provisioning tool rather than a firmware, and is distinguishable:

```sh
strings u-boot-rockchip-ostee.bin | grep 'HUK burn'   # empty = persist is out
```

Two read-only flags sit alongside it: `HUK_DRY_RUN=1` reports what a burn would
do without writing, and `TRNG_S_PROBE=1` re-runs the search for `RKRNG_S`.

### Order of operations

Everything checkable is checked before the first word, because afterwards there
is nothing left to do.

1. Refuse unless all four words are zero, so a burn can never add bits to
   another value.
2. Draw a candidate from `RKRNG_S`.
3. Require no zero word, no repeated word, 40 to 88 of 128 bits set, and at
   least 10 distinct bytes. These catch a broken source rather than grade a
   working one, and rejection is free - no write happens and the next boot can
   retry.
4. Draw again and require the two to differ. A single draw from a generator
   stuck on one value passes every check above.
5. Write.
6. Read back and require an exact match before trusting it.

### Cost of a power cut

Not a brick. The write is four words, each polled with a 1 ms timeout, so the
operation is bounded at about 4 ms. Losing power inside that window leaves a
half-programmed slot: the board boots normally and the read path refuses the
slot, so that unit cannot hold a HUK at `0x80` and is otherwise unaffected.

The fuses that can brick an RK3576 are the secure boot status at `0x008` and the
RSA hash at `0x184` - enable secure boot with no key burned and the boot ROM
demands a signature it cannot verify, which maskrom does not rescue. Neither is
touched.

There is also room to retry: the whitelist reserves `0x80`-`0x8f` and OP-TEE
uses four of those words, leaving `0x84`, `0x88` and `0x8c` spare. No fallback
is implemented.

### Limits

Two things are not established, and neither shrinks by testing harder.

A part burned here may not satisfy Rockchip's BL32 if that blob is ever run on
it, since what the blob expects at `0x80` cannot be read out of a binary.

And the bulk statistics are of the sibling instance rather than `RKRNG_S`
itself, since Linux cannot reach the secure block. Direct evidence about the
block a key comes from is six candidates across two boards plus the
requirement that two requests differ - enough to catch a catastrophically
broken source, not enough to distinguish a plausible-looking low-entropy one.
Closing it means accumulating a histogram inside OP-TEE and printing aggregate
statistics only. Even then, statistical tests cannot tell a good DRBG from a
true RNG; proving entropy needs the SP 800-90B assessment Rockchip published
for RK3588 and has not for this part. The block is asked for its true-random
path (`RKRNG_CTRL_REQ_TRNG`), not the DRNG mode.

## RPMB

`CONFIG_RPMB` enumerates `/sys/class/rpmb/rpmb0` and the OP-TEE driver binds to
it, so the secure world reaches the replay-protected partition through the
kernel rather than proxying frames through `tee-supplicant`. The eMMC reports a
4 MiB RPMB.

Do not use `mmc-utils` on it. One `mmc rpmb read-counter /dev/mmcblk0rpmb` timed
out and left the controller wedged - every subsequent read `EIO`, rootfs
unreadable, recovered only by reboot. Nothing was damaged. The boot log has the
likely reason, `mmc0: Command Queue Engine enabled`; RPMB transfers need the
controller out of command-queue mode and that transition is a known way to hang
CQHCI.

### The RPMB key

Nothing about it is stored. `tee_rpmb_key_gen()` is

```c
huk_subkey_derive(HUK_SUBKEY_RPMB, cid, sizeof(cid), key, len)
```

where `cid` is the eMMC's own CID with the PRV and CRC fields masked off, since
those change on an eMMC firmware update. So the key is a deterministic function
of the HUK and that specific eMMC, re-derived on every boot and never written
anywhere. There is no key to back up, escrow or remember - the HUK stays in
fuses and never leaves the secure world, and neither does anything derived from
it.

Two consequences follow. The pairing is bound to both halves: move the eMMC to
another board, or fit another eMMC to this one, and the derived key no longer
matches what RPMB was provisioned with. And if the SoC fails, everything sealed
against its HUK is gone. Both are the intended behaviour of a device-bound key.

Programming the key into the eMMC is itself one-shot, and `CFG_RPMB_WRITE_KEY`
is `n` by default - the same deliberate-act pattern as the HUK burn. The order
is: fuse the HUK, validate it, then provision RPMB. Otherwise one mistake
strands the eMMC too.

## Scope

This protects a key from extraction. It does not stop someone booting their own
image and asking the secure world to sign for them, which needs verified boot -
and the fuses that enable that are the ones that can brick a part.
