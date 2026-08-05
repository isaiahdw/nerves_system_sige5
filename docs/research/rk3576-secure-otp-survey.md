# RK3576 secure OTP: what is actually in it

Measured on an ArmSoM Sige5, upstream TF-A v2.15.0 + OP-TEE 4.10, with the
survey in `optee/0003`. The survey reads the secure OTP through
`rockchip_otp_read_secure()` and prints a FNV-1a hash per 4-word group; it never
prints contents, because the secure console is shared with U-Boot and Linux.

## Result

Sweeping the whole readable range, `0..0x300` words, three groups are non-zero:

    otp[0x008..0x00b] non-zero, fnv1a=0x9d164c95
    otp[0x064..0x067] non-zero, fnv1a=0x8f784b0d
    otp[0x1c8..0x1cb] non-zero, fnv1a=0x9d164c95

Everything else reads back as zero.

The survey ran against RK3588's indices, which is what the port had inherited.
RK3576's real map is confirmed further down; the two do not agree, and only
`0x008` happens to hold the same field on both.

| RK3588 index | holds on RK3588 | RK3576 board |
| --- | --- | --- |
| `0x104` | HUK | empty (RK3576 keeps it at `0x80`) |
| `0x270` | RSA public-key hash | empty (RK3576 keeps it at `0x184`) |
| `0x008` | secure-boot status | non-zero (same field on RK3576) |

An empty `0x270` is consistent with secure boot being off on this board, which
it is.

## The duplicate hash is not aliasing

`0x008` and `0x1c8` hash identically. FNV-1a over four words collides by chance
at about 2^-32, so the two groups hold the same content. Two ways that could
happen without the data really being stored twice, both ruled out by the sweep
itself:

- Address masking. If the array were 64 words and the index were masked with
  `0x3f`, physical group `0x08` would appear at `0x048`, `0x088`, `0x0c8`,
  `0x108` and so on - twelve times across the range. It appears twice.
- A repeat period of `0x1c0`, the gap between the two hits. Then `0x064` would
  repeat at `0x224`. Nothing is there.

So both are real, distinct locations holding equal contents: a mirrored
config word, the redundancy OTP normally uses for values that must survive a
bit flip. Keys are not stored twice in the clear.

`rockchip_otp_read_secure()` puts the index in `OTP_S_AUTO_CTRL` bits 31:16 and
does no masking of its own, so any aliasing would have to come from the
hardware. None is visible.

## Index units

`ROCKCHIP_OTP_HUK_SIZE` is `0x4` and OP-TEE's `HW_UNIQUE_KEY_LENGTH` is 16
bytes, so the count - and therefore the index - is in 32-bit words, not bytes.
`MAX_INDEX 0x300` is a software cap carried over from RK3588.

## The normal-world OTP, for contrast

`otp@2a580000` is exposed to Linux as `rockchip-otp0`, 256 bytes:

    +0x000: 52 4B 35 76 22 00 FF 00 01 01 4E 59 37 55 33 00
    +0x010: 00 00 00 00 00 00 00 0D 0C 12 00 00 00 00 05 04
    +0x020: 05 06 0D 00 21 01 21 01 21 01 22 01 21 01 1E 00
    +0x060: 00 00 00 00 21 01 00 00 00 00 00 00 00 00 00 00
    +0x0F0: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 0F

Chip ID, a serial (`NY7U3` in ASCII at +0x0a), and the `21 01` run at +0x24
that is PVTM/leakage data - the same family of values behind the voltage grades
in `rk3576-gpu-clock-investigation.md`. It is a separate array from the secure
one at `0x2a480000`, and everything in it is readable by any process on the
board, so nothing here can serve as a HUK.

## Second board: all three groups identical

The same image, surveyed on a second Sige5:

| group | board 1 | board 2 |
| --- | --- | --- |
| `0x008..0x00b` | `0x9d164c95` | `0x9d164c95` |
| `0x064..0x067` | `0x8f784b0d` | `0x8f784b0d` |
| `0x1c8..0x1cb` | `0x9d164c95` | `0x9d164c95` |

Nothing in the secure OTP varies between devices.

### The dies really are different

An all-identical result only means something if per-device data would have
shown up had it existed, so the boards have to be shown to be distinct dies.
U-Boot derives `cpuid#` from the normal-world OTP:

    board 2  cpuid#         = 4e5937553300000000000000000e2612
    board 1  NS OTP +0x0a  -> 4e5937553300000000000000000d0c12

Same `NY7U3` lot prefix, different tail. Board 2's `serial#` and `ethaddr`
(`35f37dba56be723b`, `1e:7f:8a:9a:62:c2`) are derived from that value.

So per-device identity exists on these parts, lives in the normal-world OTP
where any process can read it, and has no secret counterpart in the secure OTP.

## Conclusion

The RK3576 secure OTP ships blank apart from a config word and its mirror.
There is no HUK to find. This matches how Rockchip's own stack treats it -
`trusty_write_oem_huk` and `trusty_oem_otp_key_is_written` exist because the
HUK is OEM-written, not factory-burned.

The question is therefore not which index to read but whether to burn one, which
is a permanent, unrepeatable write.

The index itself was open while this section was first written and is not any
more: the HUK lives at word `0x80`, per Rockchip's own U-Boot driver, and the
survey read `0x104` only because that value had been inherited from RK3588. See
"The HUK index, confirmed" below. Both read empty on every board seen, which
the confirmed map explains as an unprovisioned part rather than a wrong guess.

What the survey settles about the write is that the slot is blank, so burning
there destroys nothing. The remaining exposure is a collision with whatever
Rockchip's own BL32 expects, which cannot be checked from outside the blob.

Reading the wrong index would have been the more insidious failure, and the
survey shows why: a read landing on non-zero, non-secret data would be accepted
as a key and secure storage would appear to work while protecting nothing.
`0x008` is a config word and `0x1c8` mirrors it, and both would have passed any
"is it non-zero" check.

## Is the secure OTP actually secret?

Worth establishing before considering putting a key in it. U-Boot runs
non-secure, after TF-A, so it can be used to ask directly. Reading the OTP_S
controller from the U-Boot prompt:

    => md.l 0x2a480000 4
    "Synchronous Abort" handler, esr 0x96000010, far 0x2a480000
    ... Resetting CPU ...

External abort on the first access - the normal world cannot reach the secure
OTP controller at all, let alone drive a read sequence. So the region is
genuinely firewalled and is a legitimate place for per-device key material.
(`esr 0x96000010` is a data abort, external, on a translation-table-clean
address; the board resets and comes back normally.)

The normal-world OTP at `0x2a580000` is the opposite: fully readable by any
process, which is where the chip ID and serial live.

## Writes are permanent and one-way

`rockchip_otp_write_secure()` makes the fuse semantics explicit:

    if (~*value & old_val) { EMSG("OTP_S Program fail"); return TEE_ERROR_GENERIC; }
    new_val = *value & ~old_val;   /* only 0 -> 1 */

Bits only ever go 0 to 1. A word cannot be rewritten, and a second write at the
same index only succeeds if the new value's bits are a superset of what is
already there. There is no erase.

The failure modes of a wrong write, worst first:

- Setting bits in a secure-boot control word. RK3588 keeps
  `SECURE_BOOT_STATUS` at index `0x8` with an enable pattern of `0x00ff`, and
  RK3576 has non-zero data at `0x008`. If a stray write turned secure boot on
  while the RSA hash area is still blank, the boot ROM would require a signed
  image it has no key to verify. That is unrecoverable - maskrom also requires
  signed loaders once secure boot is on. This is reasoned from the RK3588
  layout, not verified on RK3576, and it is the reason not to write at a
  guessed index.
- Burning a low-entropy key (below). Permanent, and silent.
- Burning at an index nothing else uses. Harmless to us, since the same build
  reads and writes the same index, but it may collide with what Rockchip's own
  BL32 expects.
- Writing where bits are already set. Refused by the driver - a safe failure.

## What we do not have: entropy

RK3588's port generates its HUK with `hw_get_random_bytes()`, backed by a
secure TRNG at `TRNG_S_BASE`. For RK3576:

- there is no `TRNG_S_BASE`, in OP-TEE's `platform_config.h` or in TF-A's
  `rk3576_def.h`, which does define `OTP_S_BASE`, `KEYLADDER_BASE` and
  `CRYPTO_S_BASE`
- `hw_get_random_bytes()` is implemented only in `platform_rk3588.c`
- the only Rockchip driver in `core/drivers` is `rockchip_otp.c`

So `CFG_WITH_SOFTWARE_PRNG` (default `y`) applies, and its seeding is OP-TEE's
weak default:

    /*
     * Override this in your platform code. This default implementation only
     * seeds the random number generator from an easily predictable timestamp
     * value or a constant value. It is not suitable for a secure environment.
     */

A key generated from that and then fused would be permanently weak, and would
look perfectly healthy from the outside.

The silicon does have a TRNG - `rng@2a410000`, `compatible = "rockchip,rkrng"`
in the vendor device tree. The gap is that nothing in the secure world drives
it. Wiring that up, and checking its output on hardware, has to come before any
burn.

## Where the secure TRNG is

The seeding in `optee/0004` uses `rng@2a410000`, which the CRU calls
`HCLK_TRNG_NS`. A secure instance exists, and TF-A's firewall header names it
outright:

    FW_SLV_ID_NSCRYPTO	FW_SLV_ID(FW_SLV_TYPE_TOP, 26)   /* 0x2a400000 */
    FW_SLV_ID_RKRNG_NS	FW_SLV_ID(FW_SLV_TYPE_TOP, 27)   /* 0x2a410000 */
    FW_SLV_ID_SCRYPTO	FW_SLV_ID(FW_SLV_TYPE_TOP, 28)   /* 0x2a430000 */
    FW_SLV_ID_KEYLAD	FW_SLV_ID(FW_SLV_TYPE_TOP, 29)   /* 0x2a420000 */
    FW_SLV_ID_RKRNG_S	FW_SLV_ID(FW_SLV_TYPE_TOP, 30)   /* ?          */

So `RKRNG_S` is a distinct firewalled slave, not a guess. Its base is the one
address in the group `rk3576_def.h` leaves out. The secure block runs
`0x2a420000` KEYLADDER, `0x2a430000` CRYPTO_S, then nothing until `0x2a480000`
OTP_S, `0x2a490000` DCF, `0x2a4a0000`/`0x2a4b0000` STIMER, `0x2a4c0000` WDT_S,
`0x2a4d0000` OTP_MASK.

That leaves `0x2a440000` through `0x2a470000` unaccounted. No public source
gives the value: GitHub code search finds no RK3576 reference to any of the
four, and the Rockchip BL32 blob is packed, so the address-constant search that
finds `0x2a4d0000` in BL31 v1.20 finds nothing there.

**It is `0x2a440000`**, found by probing from the secure world one candidate at
a time, each announced before it was touched so that a fault would still name
the address that caused it. The first answered:

    I/TC:   about to touch 0x2a440000
    I/TC:   0x2a440000: RKRNG_S - fresh data on each request

Answering the RKRNG sequence is not by itself proof, since a mirror of the
published block would do the same. The other half is that the normal world
cannot reach it. From the U-Boot prompt:

    => md.l 0x2a410014 1
    2a410014: 00000000                       non-secure instance, reads
    => md.l 0x2a440014 1
    "Synchronous Abort" ... far 0x2a440014   secure, barred

So it is the secure block, not an alias. Seeding uses it as of `optee/0010`,
which retires what sharing the non-secure instance cost: its data registers are
no longer readable from the normal world, Linux no longer owns the reset and
gate of the block being seeded from, and the mode selector is no longer
normal-world writable - the last mattering most, since a warm reboot could
otherwise carry a degraded setting into the next boot's seeding.

The probe is kept behind `CFG_RK3576_TRNG_S_PROBE`, off by default, so the
result can be re-run on another board or a later revision rather than resting
on one measurement.

## The HUK index, confirmed

`0x80` is right, and it comes from Rockchip. Their own U-Boot ships
`drivers/misc/rk3576-secure-otp.S` (in `rockchip-linux/u-boot`) - generated
assembly rather than the `.c`, but the range checks survive intact:

    sub  w1, w23, #512      ; offset - 512
    cmp  w1, 63             ; ... <= 63   -> [512, 575]
    sub  w0, w23, #32       ; offset - 32
    ccmp w0, 1, 0, hi       ; ... <= 1    -> [32, 33]
    bls  .L22               ; accept
    sub  w0, w23, #1552     ; offset - 1552
    cmp  w0, 15
    bhi  .L14               ; > 15 -> reject -> [1552, 1567]

Three whitelisted byte ranges, and those three subtractions are the only
range-check constants in the file - each appears twice, once on the read path
and once on the write path - so the list is exhaustive. Divided by four for
word indices:

| bytes | words | field |
| --- | --- | --- |
| 32-33 | `0x8` | secure boot status |
| 512-575 | `0x80`-`0x8f` | HUK key material |
| 1552-1567 | `0x184`-`0x187` | RSA key hash |

The HUK region is 16 words; OP-TEE uses the first four, which is the 16 bytes
`HW_UNIQUE_KEY_LENGTH` wants.

Three lines of evidence, and only the first two are about each other:

- **Rockchip's driver**, above. Primary, and the only one that is authoritative.
- **the-gabe/optee_os**, branch `rk3576`, documents the same three ranges and
  cites that driver. OP-TEE PR #7841 credits the-gabe, so the PR is not a third
  source - it is the same one at one remove.
- **This board.** The survey's results line up with the map: word `0x008`
  non-zero (a secure boot status word exists), `0x080` blank (no HUK burned),
  `0x184` blank (no RSA hash, so secure boot is off - consistent with the
  status word not being the `0x00ff` enable pattern). Nothing here was fitted
  to the map; the survey ran before it was found.

The survey's other two hits, `0x064` and `0x1c8`, fall outside all three
ranges. They are fields the vendor driver does not expose to the normal world,
which is why the whitelist says nothing about them.

That same tree also gives `RKRNG_S_BASE 0x2a440000`, matching what probing
found here by an unrelated method - behaviour on the bus rather than reading
someone's header.

### That driver is normal-world code

Worth being clear about what it implies, because it cuts against this build
rather than for it. `rk3576-secure-otp.S` lives in `drivers/misc/`, so it runs
in U-Boot proper, which is non-secure - and it contains no `smc` at all. It
reaches the OTP controller directly by MMIO. Its whitelist is therefore policy
in normal-world software, not a hardware boundary, and the same three ranges
are permitted on the read path as on the write path: `rk3576_secure_otp_read`
carries the identical `512`/`32`/`1552` checks that `rk3576_secure_otp_write`
does. Reading HUK material is allowed, not just provisioning it.

For that to work, Rockchip's firmware must leave `OTP_S` reachable from the
non-secure world. It is not reachable here - `md.l 0x2a480000` from the U-Boot
prompt on this build external-aborts - so upstream TF-A and upstream OP-TEE
keep the secure OTP secure-only where the vendor stack apparently does not.
This is inferred from the absence of an SMC in their driver rather than from
reading their BL31, but it is the only way that driver can function.

The practical consequence is narrow: nothing in this build exposes an OTP path
to the normal world, so a key fused here is not reachable the way it would be
under the vendor firmware. It does mean a key's secrecy should never be assumed
from its position in the map alone.

### One correction it forces

`OTP_S_MAX_INDEX` is `0x200` on RK3576, not the `0x300` inherited from RK3588,
which is what the survey scanned to. Everything found sits below `0x200`
(`0x1c8` is the highest), and the over-range reads turned up nothing, so no
result changes - but the bound was wrong and reads past the end of the array
could have aliased rather than failing.

## Rockchip's own model

From rkbin's RK3576 BL32 release notes, the vendor design is not a raw HUK at a
fixed index:

- v1.04 "Supports reading and writing OTP data for Non Protected OEM Zone"
- v1.04 "Support software TA encryption key, customers can use TA encryption
  function without burning the key"
- v1.06 "Add OEM OTP KEY hmac support for user ta"
- v1.07 "Support deriving CMAC KDF keys from OEM OTP KEY"
- v1.05 "check whether the rpmb key has been burned before changing security
  level"

An OEM key plus a key ladder (`KEYLADDER_BASE 0x2a420000`), with a Protected
OEM Zone that only TAs can reach, and an explicit option that avoids burning
anything. Key IDs resolve inside the blob, so none of the indices are public.

