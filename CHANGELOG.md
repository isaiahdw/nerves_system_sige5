# Changelog

## Unreleased

- PWM: RK3576 carries a fourth-generation PWM block that mainline does
  not know, so nothing described the controllers and the fan header did
  nothing. Adds `rockchip,rk3576-pwm` to the driver and the binding, the
  fourteen channels of pwm1 and pwm2 to `rk3576.dtsi`, and a `pwm-fan` on
  PWM2 channel 7 for the Sige5's fan header, stepping through
  0/50/100/150/200/255 at 50, 55, 60, 65 and 70 degrees off the
  package thermal sensor.
- Build: a changed kernel patch set now re-extracts the kernel. Buildroot
  applies `linux/*.patch` only when it first extracts the tree, so a patch
  added or edited afterwards was silently ignored while the build still
  reported success. `external.mk` hashes the patches into the extracted
  tree and discards the tree when the hash stops matching.

- NPU frequency scaling: devfreq reimplemented on mainline OPP APIs
  (the vendor version depends on Rockchip-only infrastructure). Rate
  changes are deferred while the NPU is unpowered, since its clock and
  rail sit inside the NPU power domains. MobileNet scales 6.23 ms at
  297 MHz to 2.84 ms at 786 MHz; LLM decode is memory-bound and flat
  above 600 MHz. The OPP table lists only rates the CRU can
  produce (300-800 MHz); the vendor's 900/950 need BL31's PVTPLL over
  SCMI, which the NPU does not survive - the SoC boots on it and then
  dies on the first inference.
- LLM inference validated on hardware: Qwen3-0.6B (W4A16) through
  rkllm 1.3.0 at 17-18 tokens/s, 17-run soak with no failures and no
  memory growth, multi-turn conversations with history retention.
  Thermals settle at 66 C under sustained load with no throttling.
  All at the fixed 600 MHz clock.
- IOMMU: force v2 page tables into the DMA32 zone. The RK3576 v2 walker
  was believed to reach above 4 GB, but that was concluded on a 4 GB
  board; on this 8 GB board large NPU mappings wedge the interconnect
  without it. Also take all DT clocks in the IOMMU driver so the MMU's
  CBUF gates run during resume.

## v0.2.0 - 2026-07-29

The NPU's MMU now runs on mainline's rockchip-iommu, on both cores:
NPU buffers are ordinary pageable memory, the 256 MB CMA cap is gone
(400 MB single allocations verified on hardware), and inference is
bit-exact against Rockchip's reference outputs through the IOMMU.
Soak-tested: 91/91 runs across alternating-core, parallel dual-core,
idle power-cycle, and allocation-cycling phases with no faults and no
leaks.

What it took: a rockchip-iommu patch attaching all of a node's power
domains with runtime-PM device links (the NPU MMU's bank pairs live in
the two NPU core domains; upstream submission planned), two IOTLB/fault
robustness patches adapted from the RK3576 rocket bring-up, a fix for
the rknpu driver's mirror of the kernel-private iommu_dma_cookie
layout (changed in 6.14), and a power-off settle replacing a
vendor-only API poll.

Still fixed at 600 MHz (devfreq on mainline OPP APIs is the remaining
NPU gap).


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
