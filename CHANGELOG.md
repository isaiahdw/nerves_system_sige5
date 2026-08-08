# Changelog

## v0.2.0 - 2026-08-07

Verified on an ArmSoM Sige5 v1.2.

### NPU

- The NPU scales its clock now. devfreq runs on mainline's OPP APIs instead of
  the vendor's Rockchip-only infrastructure, and covers 300 to 800 MHz -
  MobileNet drops from 6.23 ms to 2.84 ms across that range. LLM decode is
  memory-bound and stops improving above 600 MHz.
- Its MMU runs on mainline rockchip-iommu on both cores, so NPU buffers are
  ordinary pageable memory and the 256 MB CMA ceiling is gone. Single 400 MB
  allocations work, and inference still matches Rockchip's reference output
  exactly.
- Large mappings no longer wedge the interconnect on an 8 GB board: the v2
  page tables are kept in the DMA32 zone.
- A failed probe can no longer leave a half-registered device behind. The DRM
  node appears only once everything behind it is built.
- Qwen3-0.6B runs through rkllm at 17 to 18 tokens a second.

### GPU

- The Mali G52 is driven from the SCMI clock and scales with load. Be aware
  that the OPP labels are not the rates you get - measure with panfrost's
  cycle counter if it matters.

### WiFi

- The BCM43752 on board v1.2 works.
- The SDIO wake handshake is allowed to finish rather than being abandoned
  after a handful of access errors, which is what had been producing timeouts
  under load. About one wake in five thousand needs a retry.

### Peripherals

- The fan header works. RK3576 has a fourth-generation PWM block that mainline
  did not recognise, so nothing described the controllers; the fan now steps up
  between 50 and 70 degrees.
- Optional power-domain resets are cycled during transitions.

### Secure world (OP-TEE)

- `SECURE_WORLD=1 ./scripts/build-uboot.sh` builds upstream TF-A and OP-TEE in
  place of rkbin's BL31, which gets you a device key that never leaves the SoC:
  an EC P-256 keypair inside a PKCS#11 trusted application, sealed against a
  hardware key fused on the first boot of an unprovisioned part. Off by
  default, and the default bootloader fuses nothing.
- Treat it as a worked example rather than a security design. There is no
  verified boot, so the key cannot be extracted but it is not exclusively
  yours either. The README covers the rest.

### Build

- Editing a kernel patch actually rebuilds the kernel. Buildroot only applies
  `linux/*.patch` when it first extracts the tree, so anything added later was
  quietly ignored while the build still claimed success.
- The bootloader builds with `container` instead of `docker`.
- Builds clean up after themselves, rather than leaving every previous system
  artifact in the build volume.

## v0.1.0 - 2026-07-29

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
- GPU: panfrost with Mesa userspace (OpenGL ES 3.1 over EGL/GBM, no
  X11/Wayland in the image), verified with kmscube at a vsync-locked
  60 fps on HDMI. Buildroot's Kconfig requires LLVM in the target Mesa
  for panfrost, but nothing in this driver set uses it at runtime:
  Mesa is built with draw-use-llvm=false and post-build.sh removes the
  orphaned LLVM libraries (readelf-gated), keeping them out of the
  image. No video codecs or PWM on mainline 6.18.
- SARADC enabled (upstream leaves it disabled with no board enable);
  ADC inputs on the 40-pin header.
- Auto-revert of unvalidated firmware, watchdog recovery, CPU DVFS,
  thermal throttling, LEDs, and the hardware RNG are all verified on
  hardware.
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
