# Nerves System for the ArmSoM Sige5 / Banana Pi BPI-M5 Pro (Rockchip RK3576)

A [Nerves](https://nerves-project.org) system for the
[ArmSoM Sige5](https://docs.armsom.org/armsom-sige5), also sold as the
Banana Pi BPI-M5 Pro (identical hardware). Rockchip RK3576
(4x Cortex-A72 + 4x Cortex-A53, Mali-G52 MC3), LPDDR4X, soldered
32–128 GB eMMC, 2x Gigabit Ethernet, WiFi 6/BT, HDMI 2.1, M.2 NVMe,
USB 3.0, and a 40-pin GPIO header. Built on a mainline LTS kernel
(6.18.y) and mainline U-Boot. The IEx console is on `ttyS0`, the
1.5 Mbaud debug UART on header pins 8/10/6.

Status: bring-up. Derived from the hardware-verified
[nerves_system_rock_4d](https://github.com/isaiahdw/nerves_system_rock_4d)
mainline branch (same SoC); Sige5-specific parts are untested until the
first board boot.

## Boot architecture

Everything lives on the soldered eMMC. The RK3576 boot ROM on this board
(no SPI NOR fitted) loads U-Boot directly from the eMMC at sector 64;
fwup's factory `complete` task writes the bootloader there as part of the
disk image, and upgrades never touch it. The boot ROM also boots from the
microSD slot, so the same fwup image written to an SD card is a
no-special-tools bring-up and recovery path.

```
RK3576 boot ROM
  └─ eMMC sector 64: u-boot-rockchip.bin  (TPL/DDR init + SPL, U-Boot FIT + BL31 @ 8 MB)
      └─ bootcmd = run nerves_init nerves_boot   (env @ 15 MB)
          └─ Image.<slot> + rk3576-armsom-sige5.<slot>.dtb from p1 (FAT)
              └─ squashfs rootfs on p2 (A) or p3 (B), root=PARTUUID=<slot GUID>
```

A/B slots with automatic revert: new firmware boots once, and unless the
application validates it (`Nerves.Runtime.validate_firmware/0`), U-Boot
boots the previous slot on the next reboot. If the env is missing or the
nerves boot path fails, `bootflow scan` falls back to
`extlinux/extlinux.conf` (eMMC first, then SD).

## Hardware support (expected — pre-hardware-verification)

| Feature | Status | Notes |
| --- | --- | --- |
| eMMC boot, A/B firmware slots | Expected | Boot ROM reads the bootloader from eMMC directly; HS400ES |
| microSD boot | Expected | Boot ROM path; use for bring-up/recovery with the same image |
| OTA updates (`mix upload`) | Expected | Delta updates supported (fwup >= 1.12 on device) |
| Ethernet x2 | Expected | gmac0 + gmac1, RTL8211F each (`eth0`/`eth1`) |
| WiFi (onboard, board v1.2+) | Expected | SYN43752/BCM43752 on SDIO via in-kernel brcmfmac; linux-firmware blobs included; board NVRAM may be needed. v1.0/1.1 boards (RTL8852BS) have no mainline driver |
| Bluetooth | No | uart4 is deliberately disabled in the mainline dts; needs a serdev node + bring-up |
| HDMI display + console | Expected | VOP + dw-hdmi-qp; framebuffer console enabled |
| GPU (Mali G52 MC3) | Partial | Kernel panfrost driver ships (=m); no Mesa userspace yet (Buildroot's panfrost requires LLVM) |
| M.2 NVMe (PCIe 2.1) | Expected | pcie0 + NVMe drivers built in |
| USB | Expected | 2x Type-C (one PD power input only, one USB 2.0 OTG/maskrom) + USB3 host |
| Audio | Expected | ES8388 codec + HDMI audio via ALSA |
| Watchdog | Expected | dw-wdt enabled by the board patch; armed by `nerves_heart`, NOWAYOUT |
| RTC | Expected | HYM8563-compatible; battery connector on board |
| GPIO/I2C/SPI/UART header | Expected | Via [Circuits.*](https://elixir-circuits.github.io/) |
| NPU (6 TOPS) | No | No mainline RK3576 NPU driver |
| Video decode | No | rkvdec2 for RK3576 lands in kernel 7.0 |
| PWM / fan header | No | No RK3576 PWM nodes in mainline 6.18 |
| MIPI CSI/DSI | No | Not wired up in mainline for this board |

## Building

Linux (or the Nerves Docker build environment) is required:

```sh
mix deps.get
mix compile
```

### Using in an application

```elixir
@all_targets [:sige5]

# in deps():
{:nerves_system_sige5,
 path: "../nerves_system_sige5",
 runtime: false, nerves: [compile: true], targets: :sige5}
```

Then `export MIX_TARGET=sige5` for every mix command.

## Flashing

Factory flash goes to the eMMC over USB maskrom (see
[uboot/README.md](uboot/README.md) for details):

```sh
fwup -a -d disk.img -t complete -i <firmware>.fw   # raw image on the host
# OTG Type-C port to the host, hold MASKROM while connecting the PD
# power supply to the other (PD-only) Type-C port:
rkdeveloptool db uboot/rk3576_spl_loader_v1.09.108.bin
rkdeveloptool cs 1                                  # storage: eMMC
rkdeveloptool wl 0 disk.img
rkdeveloptool rd
```

Alternative with no tools: `mix burn` the same firmware to a microSD and
boot from the slot — useful for first bring-up and recovery.

OTA upgrades are the standard Nerves flow (`mix upload`); upgrades write
the inactive slot only and revert automatically unless validated.

## Kernel

Mainline LTS from kernel.org (6.18.40) with the upstream
`rk3576-armsom-sige5` device tree. One board patch (`linux/0001`): pin
the mmc aliases (eMMC = `/dev/mmcblk0`, SD = `/dev/mmcblk1` — mainline
defines none, so numbering would follow probe order) and enable the
watchdog node for `nerves_heart`. Configuration is the arm64 `defconfig`
plus `linux/nerves.config`, documented inline.

## Debug UART

40-pin header: pin 8 (TX), pin 10 (RX), pin 6 (GND), 1500000 8N1,
3.3 V TTL.
