# Changelog

## v0.1.0 (unreleased)

Initial bring-up, derived from nerves_system_rock_4d's mainline branch
(same RK3576 SoC, mainline U-Boot v2026.01 + kernel 6.18.y).

- Boot: everything on the soldered eMMC — U-Boot at sector 64 (written
  by the factory `complete` task; the boot ROM reads it directly),
  Nerves env at 15 MB, FAT boot partition, rootfs A/B, expanding f2fs
  app partition. The same image boots from microSD for bring-up and
  recovery. Factory flash over USB maskrom with rkdeveloptool.
- Kernel: mainline 6.18.40, upstream rk3576-armsom-sige5 device tree,
  one board patch (mmc aliases so the eMMC is /dev/mmcblk0 + watchdog
  enable), arm64 defconfig + Nerves fragment.
- Ethernet: 2x GbE (gmac0/gmac1, RTL8211F).
- WiFi (board v1.2+): BCM43752/SYN43752 on SDIO via in-kernel brcmfmac
  with linux-firmware blobs. Bluetooth not brought up (uart4 disabled
  in the mainline dts).
- GPU: kernel panfrost driver; no Mesa userspace yet. No NPU, video
  codecs, or PWM on mainline 6.18.

Not yet verified on hardware.
