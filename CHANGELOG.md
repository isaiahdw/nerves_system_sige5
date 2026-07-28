# Changelog

## v0.1.0 (unreleased)

Initial release, derived from nerves_system_rock_4d's mainline branch
(same RK3576 SoC, mainline U-Boot v2026.01 + kernel 6.18.y). Verified on
a Sige5 v1.2 board 2026-07-27: eMMC boot, OTA updates with validation,
ethernet, onboard WiFi, GPU probe, watchdog, RTC, ALSA devices.

- Boot: everything on the soldered eMMC — U-Boot at sector 64 (written
  by the factory `complete` task; the boot ROM reads it directly),
  Nerves env at 15 MB, FAT boot partition, rootfs A/B, expanding f2fs
  app partition. The same image boots from microSD for bring-up and
  recovery. Factory flash over USB maskrom with rkdeveloptool.
- The app partition grows to fill the eMMC on the first boot after a
  maskrom/image flash (ops.fw `expand` task + `expand-app-fs` from
  erlinit), so flashed images work the same on any eMMC size.
- Kernel: mainline 6.18.40, upstream rk3576-armsom-sige5 device tree,
  one board patch (mmc aliases so the eMMC is /dev/mmcblk0 + watchdog
  enable), arm64 defconfig + Nerves fragment.
- Ethernet: 2x GbE (gmac0/gmac1, RTL8211F).
- WiFi (board v1.2+): BCM43752 (AP6275S) on SDIO via in-kernel brcmfmac.
  The firmware/CLM/NVRAM are not in upstream linux-firmware;
  package/brcmfmac43752-firmware installs them from the Armbian firmware
  collection. Bluetooth not brought up (uart4 disabled in the mainline
  dts).
- GPU: kernel panfrost driver; no Mesa userspace yet. No video codecs
  or PWM on mainline 6.18.
- NPU: the vendor rknpu driver (0.9.8) built out-of-tree against the
  mainline kernel with a small API-port patch series, plus the matching
  librknnrt 2.3.2 runtime. Runs without an IOMMU (CMA buffers, 256 MB)
  at a fixed 600 MHz. Two kernel patches make the NPU power domains
  work on mainline: run all RKNN clocks during domain transitions
  (without them the first register access stalls the interconnect) and
  a 15 us settle delay before QoS restore (both adapted from the
  RK3576 rocket bring-up by Jiaxing Hu). Verified on hardware with
  single-op and chained int8 models, including across idle
  power-off/on cycles.
