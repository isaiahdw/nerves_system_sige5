# RK3576 secure world

What the secure world on this SoC consists of, what is in its one-time
programmable memory, and how this firmware uses it.

## What it is for

The board has no secure element - no ATECC608, no TPM - and the hardware cannot
be changed. What it does have is TrustZone, which lets a small separate OS
(OP-TEE) run at a privilege level Linux cannot reach, and a few fuses inside the
SoC that only that OS can read.

Between them those give the thing a secure element would: **a private key the
device can use but nobody can copy off it.**

### The problem being solved

A device identity key - for NervesHub, an MQTT broker, a certificate - is
normally a file on the data partition. Anyone who unsolders the eMMC, or gets
root once, reads it and can impersonate that device forever, and you cannot tell
that it happened.

With a secure world the key is generated inside OP-TEE and never leaves it. The
application does not hold the key; it asks the secure world to sign a challenge
and gets a signature back. Dump the eMMC and you get ciphertext.

### The pieces, and what each one buys

| piece | what it gives you | needed for a device key? |
| --- | --- | --- |
| **OP-TEE** | a place to run code and hold secrets that Linux cannot read | yes |
| **HUK** (fused per device) | the root secret everything else is encrypted against, unique to that SoC | yes |
| **PKCS#11 TA** | the standard interface for generating keys and signing with them | yes |
| **Secure storage (REE-FS)** | keys stored encrypted and authenticated, keyed from the HUK | yes - and this is the default |
| **RPMB** | rollback protection: nobody can restore an older copy of secure storage | no - see below |

**RPMB is not required for secure storage**, despite the common assumption. OP-TEE's default backend is `CFG_REE_FS`,
which keeps encrypted, authenticated files on the normal filesystem, keyed from
the HUK. Confidentiality and integrity come from that alone.

What RPMB adds is only rollback protection. Without it, someone holding the eMMC
can restore an *old snapshot* of secure storage and OP-TEE cannot tell. That
matters for things that must move in one direction - revocation lists, usage
counters, "this firmware version was superseded" - and matters very little for a
device identity key, where restoring an old snapshot just hands back the same
key.

### Compared to Trust&GO

The closest familiar equivalent is Microchip's ATECC608B in Trust&GO trim, and
the shape is the same:

| | ATECC608B Trust&GO | this |
| --- | --- | --- |
| where the private key lives | inside the chip | inside OP-TEE, encrypted against the HUK |
| can it be read out | no | no |
| how the application uses it | ask the chip to sign | ask the secure world to sign, over PKCS#11 |
| provisioning | done in Microchip's factory, ships with a cert | done here, on first boot |
| what you register with the cloud | the public key / cert | the public key |
| root of trust | the chip's own fused key | the SoC's fused HUK |

The practical differences: Trust&GO arrives pre-provisioned with a certificate
chain and needs no fusing, where this needs a HUK burned once per device. And a
discrete element is its own tamper boundary, where here the boundary is the SoC
- an attacker who defeats TrustZone defeats the key, and there is no separate
package to attack.

### What neither protects against

The boot chain here is not verified, so the boundary is: protects against
compromised normal-world software, and against storage removed from the board,
for as long as the bootloader and BL32 are intact.

A replacement BL32 runs at S-EL1 and can read the same secure OTP words
`plat_rockchip_get_huk()` reads, so whoever can replace the firmware takes the
key rather than merely using it. Closing that means verified boot, which is
more fuses - and the ones that enable it are the ones that can brick a part.

This is not specific to TrustZone: anyone who can talk to an ATECC608 can ask it
to sign too, unless its slots and I/O protection are configured to prevent it.

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
boot being off. That is the as-shipped state; `0x080` is no longer blank on
board 1.

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
not initialise: the PKCS#11 TA loads but cannot open a session. That was the
state of both boards when this was written. Board 1 has since had a HUK fused
(see "Burning a HUK" below) and initialises normally; board 2 has not.

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

### Verified end to end

A HUK has been fused on one board and the whole chain works from the fuses up:

    slot blank in 4 of 4 words
    candidate usable in 4 of 4 words
    candidate passes every check a burn requires
    second draw differs, as it must
    HUK burn: committing 4 words at index 0x80 - irreversible
    HUK burn: done, verified

On the next boot `read_huk()` succeeds - no burn, no "No HUK" - and secure
storage initialises. Through PKCS#11, with `tee-supplicant` running:

    C_Initialize / C_GetSlotList (3 slots) / C_OpenSession
    C_GenerateKeyPair (EC P-256, CKA_TOKEN)
    C_SignInit / C_Sign -> 64-byte signature

The keypair is generated inside OP-TEE, persisted to secure storage encrypted
against the HUK, and used to sign. The private key never enters Linux. Storage
lands in `/data/tee`, on the app partition - it survives firmware updates and
does not survive a data wipe, so a factory reset means re-enrolment.


```sh
SECURE_WORLD=1 ./scripts/build-uboot.sh
```

One flag. It builds upstream OP-TEE inside TF-A and writes the result to
`uboot/u-boot-rockchip.bin` - the file fwup packages - so a normal firmware
build and flash carries it. There is no second bootloader to swap in.

That image fuses a HUK on the first boot of a part that has none, because a
secure world without one cannot store anything. It only ever writes a blank
slot, and only after the checks below pass, so booting it on a part that
already has a key does nothing.

`uboot/u-boot-rockchip.variant` records which build is in the binary, since a
diff of the binary itself says only "binary files differ".

`SECURE_WORLD_DEBUG=1` adds read-only diagnostics: an OTP survey, a search for
`RKRNG_S`, and a dry run reporting what a burn would do without writing. Each
is a build flag of its own — `CFG_RK3576_OTP_SURVEY`, `CFG_RK3576_TRNG_S_PROBE`
and `CFG_RK3576_HUK_DRY_RUN` — all defaulting to `n`. They answer questions
that are asked once per SoC, and a part with no HUK would otherwise print the
survey every time a key is derived.

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
operation is bounded at about 4 ms.

What the read path catches is an interruption *between* words: it refuses a slot
with an all-zero word, so a burn that stopped after one, two or three words is
rejected and the unit simply cannot hold a HUK at `0x80`.

What it does not catch is an interruption *inside* the last word. A word part
way through programming can read non-zero, which makes all four words non-zero
and the slot look complete. The next boot then accepts a key that is not the one
the writer verified, and derives secure storage from it. Nothing detects that
today - closing it needs a completion marker in a spare word, written only after
an exact read-back and required on every later read. Treat an interrupted burn
as suspect, not as guaranteed-rejected.

The fuses that can brick an RK3576 are the secure boot status at `0x008` and the
RSA hash at `0x184` - enable secure boot with no key burned and the boot ROM
demands a signature it cannot verify, which maskrom does not rescue. Neither is
touched.

There is also room: the whitelist reserves `0x80`-`0x8f` and OP-TEE uses four of
those words, leaving `0x84`, `0x88` and `0x8c` spare - which is where a
completion marker would go, and where a retry slot could. Neither is
implemented.

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

### Order of provisioning

Fuse the HUK first, confirm it works, and only then consider RPMB. Not because
RPMB is difficult, but because its key is *derived from the HUK*, so
provisioning it early commits a second one-shot resource to a root that has not
been shown good yet.

1. Burn the HUK. Confirm the readback, that it survives a power cycle, that
   secure storage initialises, and that PKCS#11 opens a session and can generate
   and use a key. Only after all of those is the HUK proven.
2. Decide whether rollback protection is actually wanted. For a device identity
   key it usually is not.
3. If it is, first find out whether the eMMC's RPMB key is already programmed -
   if a factory set it and you do not hold it, RPMB is unusable on that unit
   regardless. Ask OP-TEE, which reports it when it tries; do **not** probe with
   `mmc-utils`, which wedges the controller.
4. Then enable `CFG_RPMB_FS` and `CFG_RPMB_WRITE_KEY`, both off by default.

The failure modes are not symmetric, which is the real argument for the order. A
bad HUK costs that unit its HUK and leaves the eMMC untouched. A bad RPMB key
costs that eMMC's RPMB permanently, and the eMMC is soldered.

These build flags will likely collapse into a single provisioning switch once
the sequence is qualified. They are separate so each can be enabled alone.

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
is `n` by default - the same deliberate-act pattern as the HUK burn.

