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

Two RK3588 landmarks are empty here:

| RK3588 index | holds | RK3576 board |
| --- | --- | --- |
| `0x104` | HUK | empty |
| `0x270` | RSA public-key hash | empty |
| `0x008` | secure-boot status | non-zero |

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

## Reading

Nothing found is per-device secret material. The likely conclusion is that this
part has no HUK burned at all, which agrees with how Rockchip's own stack
treats it: `trusty_write_oem_huk` and `trusty_oem_otp_key_is_written` exist
because the HUK is OEM-written, not factory-burned. On that reading the
question stops being "which index" and becomes "do we burn one, and where".

This is not yet proven. A per-device secret differs between boards and a config
word does not, so the test is to run the same survey on a second board and
compare the three hashes:

- `0x064` differs between boards - a per-device value exists, and the index
  is found without writing anything.
- all three identical - nothing per-device is present; the part is
  unprovisioned.

Until that comparison exists, no index may be treated as the HUK. A wrong read
that lands on non-zero, non-secret data is the dangerous outcome: secure
storage would key on public data and appear to work. `0x008` is a config word
and `0x1c8` mirrors it - both would look perfectly plausible to code that only
checks for non-zero.
