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
`rk3576-gpu-clock-investigation.md`.

BL31 v1.02 enabled the GPU counter that the clock measurements rely on.

Nothing in any BL31 release since v1.05 mentions PVTPLL, GPU clocking or SCMI
rates, so v1.24 behaves as v1.05 established for these purposes.
