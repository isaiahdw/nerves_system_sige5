# RK3576 firmware blobs: what we ship against what Rockchip publishes

Checked 2026-08-04 against [rockchip-linux/rkbin](https://github.com/rockchip-linux/rkbin),
which is the authoritative source, and its changelog `doc/release/RK3576_EN.md`.

## Current

| Component | Ours | rkbin latest | Collabora's fork |
| --- | --- | --- | --- |
| BL31 | v1.24 | **v1.24** | v1.20 |
| DDR init (in U-Boot) | v1.12 | **v1.12** | v1.09 |
| SPL | v1.08 | v1.08 | v1.08 |
| BL32 (OP-TEE) | not shipped | v1.08 | v1.06 |

`RKTRUST/RK3576TRUST.ini` pairs BL31 v1.24 with BL32 v1.08.

## The maskrom recovery loader is stale

`uboot/rk3576_spl_loader_v1.09.108.bin` is built from **DDR v1.09** plus SPL
v1.08. The current recipe (`RKBOOT/RK3576MINIALL.ini`) produces
`rk3576_spl_loader_v1.12.108.bin` from DDR v1.12.

The normal boot path already runs DDR v1.12, so this only affects maskrom
recovery - which is exactly the path that matters when a board is otherwise
unbootable. Both intervening DDR releases fix LPDDR4 signal integrity, and this
board is LPDDR4:

- **v1.10** - "Optimize SI of LPDDR4(X) CA": CA signal timing margins may be
  insufficient for some high-density LPDDR4(X) (Rockchip issues 551274, 556845).
  Also adds an LPDDR4(X) RZQI check, and warns that BL31 must be v1.20 or above.
- **v1.12** - "Optimize the MR configuration process during LP4(X)
  initialization": prevents insufficient LP4(X) CA signal margin during init.
  Also increases the delay after configuring the serial port clock, to prevent
  serial instability.

Worth rebuilding the loader at v1.12.108. Relevant to the maskrom recovery task.

## Which DDR rate this board runs, and why

The blob is `rk3576_ddr_lp4_2112MHz_lp5_2736MHz_v1.12.bin`: **2112 MHz is the
LPDDR4 rate, 2736 MHz the LPDDR5 rate**. The Sige5 is LPDDR4, which is why
`devfreq` tops out at 2112 while the vendor DMC OPP table lists 2736 - the table
covers both memory types and the fitted DRAM selects.

## BL31 and the PVTPLL table

BL31 v1.05 (2024-04-24) added "Add otp init", "Increase pvtpll length for middle
frequencies" and "Adjust pvtpll table by otp opp info". The per-die adjustment
matters here because this board's OTP `opp-info` cells are unprogrammed; see
`rk3576-gpu-clocks.md`.

BL31 v1.02 enabled the GPU counter that the clock measurements rely on.

Nothing in any BL31 release since v1.05 mentions PVTPLL, GPU clocking or SCMI
rates, so v1.24 behaves as v1.05 established for these purposes.

## A trust-ini `ADDR` is an offset, not an address

`RKTRUST/RK3576TRUST.ini` gives `[BL32_OPTION] ADDR = 0x08400000`. That is an
offset from `DRAM_BASE`, not an address. Rockchip's own
`arch/arm/mach-rockchip/fit_args.sh`:

```sh
-t)     TEE_LOAD_ADDR=$2
        # Compatible leagcy: Offset
        if ((TEE_LOAD_ADDR < DRAM_BASE));  then
                TEE_LOAD_ADDR="0x"$(echo "obase=16;$((DRAM_BASE+$2))"|bc)
```

With `CONFIG_SYS_SDRAM_BASE = 0x40000000` the real address is `0x48400000`.
Taken literally it lands 0.9 GB below DRAM, which starts at `0x40200000`, and
SPL hangs mid-FIT copying the TEE into nothing - after verifying `u-boot`,
`atf-2` and `atf-3`, before BL31 runs. The board then needs maskrom.

Check any trust-ini `ADDR` against the DRAM base before using it.

## Rockchip's BL32 blob is not usable here

It ships no PKCS#11 TA. `TEEC_OpenSession` on
`fd02c9da-306c-48c7-a49c-bbd827ae86ee` returns `ITEM_NOT_FOUND` on v1.08 and
v1.12, measured on hardware. It is not a filesystem TA either - no
`/lib/optee_armtz`, no `.ta` on the image - and rkbin ships a TA bundle for
rk3506 but none for rk3576. Authoring one needs Rockchip's signing key.

So its OTP-backed key machinery is reachable only through TAs that cannot
exist. `scripts/build-uboot.sh` builds upstream OP-TEE instead, which has
PKCS#11 and, with the patches in `optee/`, a per-device key.
