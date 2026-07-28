# U-Boot boot chain

The Sige5 has no SPI NOR: the RK3576 boot ROM loads U-Boot straight from
the eMMC (or from the microSD slot, or over USB in maskrom mode). U-Boot
therefore ships inside the fwup image and lands on the eMMC with
everything else:

| Blob                  | Location                  | Contents                                    |
| --------------------- | ------------------------- | ------------------------------------------- |
| `u-boot-rockchip.bin` | eMMC, sector 64           | idblock (DDR init + SPL) + FIT (U-Boot proper + BL31) at 8 MB |
| `uboot-env.bin`       | eMMC, 15 MB (0xF00000)    | Nerves environment (built from `uboot.env`) |

The committed `u-boot-rockchip.bin` is mainline U-Boot v2026.01
(`sige5-rk3576_defconfig`) plus the Rockchip rkbin blobs (RK3576 DDR init
v1.12, BL31 v1.24 — there is no open-source DRAM init or BL31 for this
SoC), with the Nerves environment support added:
`CONFIG_ENV_IS_IN_MMC=y`, `CONFIG_ENV_OFFSET=0xF00000`,
`CONFIG_ENV_SIZE=0x20000`, `CONFIG_SYS_MMC_ENV_DEV=0` (the eMMC —
U-Boot's mmc0 is the eMMC on this board, mmc1 the microSD).

Rebuild it reproducibly with `scripts/build-uboot.sh` (Docker; pinned
U-Boot tag + rkbin commit).

Licensing of the committed binaries: U-Boot itself is GPL-2.0-or-later
(corresponding source: the pinned tag at https://source.denx.de/u-boot/u-boot
plus this script). The DDR-init and BL31 components inside
`u-boot-rockchip.bin` and all of `rk3576_spl_loader_v1.09.108.bin` are
Rockchip proprietary blobs from
[rockchip-linux/rkbin](https://github.com/rockchip-linux/rkbin),
redistributable per `LICENSES/LICENSE.rockchip-rkbin`.

## Boot and automatic revert

U-Boot runs the `nerves_init`/`nerves_boot` scripts from the environment
block and boots the active slot's `Image.<slot>` + dtb directly
(`root=PARTUUID=<slot GUID>`). Automatic revert follows the standard
Nerves model:

- U-Boot: with `nerves_fw_autovalidate=0`, new firmware boots once
  (`booted` 0→1) leaving `nerves_fw_validated=0`. If the next boot still
  sees `validated=0`, U-Boot boots the other slot.
- Application: calls `Nerves.Runtime.validate_firmware()` once healthy
  (sets `validated=1`), or the update reverts.

If `nerves_boot` ever fails, `bootcmd` falls through to `bootflow scan -lb`,
which boots `extlinux/extlinux.conf` (from the eMMC, then the SD). A
missing or corrupt environment block makes U-Boot fall back to its
compiled-in default environment, which does the same.

## uboot.env — the shared firmware/boot environment

`uboot.env` is compiled into `uboot-env.bin` and written raw at 15 MB
(`UBOOT_ENV_OFFSET`, see `fwup_include/fwup-common.conf` and
`rootfs_overlay/etc/fw_env.config`). It is a single fw_env block shared by
three parties: U-Boot reads it to pick the boot slot, `nerves_runtime`/
`fwup` read and write the `nerves_fw_*` firmware metadata, and `boardid`
reads the serial number.

## Flashing the eMMC (maskrom over USB)

Generate a raw disk image and write it in maskrom mode:

```sh
fwup -a -d disk.img -t complete -i <firmware>.fw
```

The board has two identical-looking Type-C ports with different jobs:
one is PD power input only (9-20 V USB-PD supply required), the other
is the USB 2.0 OTG port where maskrom enumerates — check the silkscreen
before plugging in. Connect the OTG port to the host with a USB-C data
cable, hold the MASKROM button while connecting the PD supply, and keep
holding until `rkdeveloptool ld` lists a maskrom device. Then, from
this directory:

```sh
rkdeveloptool db rk3576_spl_loader_v1.09.108.bin  # bootstrap the loader
rkdeveloptool cs 1                                # storage: eMMC
rkdeveloptool wl 0 disk.img                       # write the whole image
rkdeveloptool rd                                  # reboot
```

The disk image is much smaller than the eMMC. That's fine: the app
partition is marked expandable, and on the first boot the system rewrites
the GPT and grows the filesystem to fill the disk
(`/usr/sbin/expand-app-fs`, run from erlinit).

`rk3576_spl_loader_v1.09.108.bin` is the RK3576 maskrom download loader,
built from the Rockchip vendor SDK's U-Boot (`./make.sh rk3576` packs it
from the rkbin SPL/DDR components). The SoC is the same across RK3576
boards, so the loader is board-agnostic.

No-tools alternative: write the same fwup firmware to a microSD
(`mix burn`) and boot from the slot — the boot ROM boots SD on this
board. Useful for first bring-up and as a recovery path when the eMMC
contents are broken.

Maskrom is also the unbrickable floor: the boot ROM always runs first,
so a bad eMMC image can always be recovered over USB.

Serial console for watching the boot: UART0 on the 40-pin header — pin 8
(TX), pin 10 (RX), pin 6 (GND), 1500000 8N1, no flow control.
