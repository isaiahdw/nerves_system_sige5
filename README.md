# Nerves System for the ArmSoM Sige5 / Banana Pi BPI-M5 Pro (Rockchip RK3576)

A [Nerves](https://nerves-project.org) system for the
[ArmSoM Sige5](https://docs.armsom.org/armsom-sige5), also sold as the
Banana Pi BPI-M5 Pro (identical hardware). Rockchip RK3576
(4x Cortex-A72 + 4x Cortex-A53, Mali-G52 MC3), LPDDR4X, soldered
32–128 GB eMMC, 2x Gigabit Ethernet, WiFi 6/BT, HDMI 2.1, M.2 NVMe,
USB 3.0, and a 40-pin GPIO header. Built on a mainline LTS kernel
(6.18.y) and mainline U-Boot. The IEx console is on `ttyS0`, the
1.5 Mbaud debug UART on header pins 8/10/6.

Derived from the
[nerves_system_rock_4d](https://github.com/isaiahdw/nerves_system_rock_4d)
mainline branch (same SoC). Verified on a Sige5 v1.2 board: eMMC boot,
OTA updates with validation, both network interfaces, onboard WiFi, GPU,
NPU, HDMI console, watchdog, RTC, and audio devices.

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

## Secure world (OP-TEE) — not built in, opt-in

There is no BL32 in the image. The boot chain runs BL31 (TF-A) and nothing
in TrustZone above it, so `/dev/tee0` never appears even though the kernel
carries the OP-TEE driver.

Turning it on is worth understanding, because it is the only way this board
can keep a secret. Suppose the device holds a private key to authenticate
to NervesHub. Today that key is a file on the data partition: anyone who
unsolders the eMMC, or gets root once, reads it and can impersonate the
device forever. With a secure world the key is generated and sealed inside
OP-TEE, stored in the eMMC's RPMB partition, and the application never sees
it - it asks the secure world to sign a challenge and gets a signature
back. Pulling the eMMC yields ciphertext.

RPMB is what makes that real, and this board already has it: a
replay-protected partition (`/dev/mmcblk0rpmb`) authenticated by a key the
eMMC itself holds, with a counter so writes cannot be rolled back to an
older state.

```
BL31 (TF-A)  ── loads ──▶  BL32 (OP-TEE) @ 0x08400000
                              │
       Linux  ── /dev/tee0 ──▶│ signs with a key it never hands over
                              └─ sealed in /dev/mmcblk0rpmb
```

To build a bootloader with it:

```sh
WITH_BL32=1 ./scripts/build-uboot.sh
```

That wraps Rockchip's `rk3576_bl32_v1.08.bin` in an ELF at the load address
from `RKTRUST/RK3576TRUST.ini`, because binman only accepts an ELF or a
binary with an `optee_v1_header` and the blob has neither.

The rest is not built yet. What it needs:

- **Reserve the memory**, or Linux allocates over OP-TEE's DRAM and crashes -
  rkbin's blob publishes no reservation. The node has to cover `0x08400000`,
  the address in `RK3576TRUST.ini`. Do not copy the number out of upstream
  OP-TEE: its rk3576 flavor puts TZDRAM at `0x70000000`, nowhere near where
  this blob loads. The two are not interchangeable in either direction.
- **Userspace**, both already packaged: `BR2_PACKAGE_OPTEE_CLIENT` for
  `tee-supplicant` (RPMB access goes through it), and
  `BR2_PACKAGE_OPTEE_TEST` for `xtest`, which is how you prove secure
  storage actually works rather than assuming.
- **Provision per device.** The RPMB key is written once and cannot be
  rewritten. Check first whether the factory already burned one - if it did
  and you do not hold it, RPMB is unusable on that unit.

The kernel side needs nothing: `CONFIG_OPTEE` is already in, which is why
`/sys/bus/tee` exists on a running board with no `/dev/tee0` behind it.

Use Rockchip's blob rather than building upstream OP-TEE, which looks like
the cleaner option but is not. Upstream has a `plat-rockchip` rk3576, but it
only configures the DDR firewall; with no `tee_otp_get_hw_unique_key()`
behind it OP-TEE falls back to a default hardware key compiled into public
source. RPMB derives its authentication key from that, so secure storage
would be sealed with a constant anyone can look up. Rockchip's blob is the
one with real OTP backing.

Worth being clear about the limit: this protects a key from being
*extracted*. It does not stop someone booting their own image on the board
and asking the secure world to sign for them. Closing that means verified
boot, which is more fuses.

## Hardware support

Verified on a Sige5 v1.2, 2026-08-05.

> **Proprietary firmware.** The WiFi firmware, its CLM regulatory blob and the
> Bluetooth patch RAM in `package/brcmfmac43752-firmware` are Broadcom/Infineon
> binaries. They are redistributed unmodified and are not covered by this
> repository's licence; Buildroot marks the package `PROPRIETARY`, so
> `make legal-info` reports it. Neither of the upstreams these come from
> (armbian/firmware, Rockchip's `rkwifibt`) ships the vendor licence text
> alongside the binaries, so this repository cannot state their terms - if you
> are shipping a product, get the applicable licence from Infineon or your
> module vendor rather than inferring it from here.

| Feature | Status | Notes |
| --- | --- | --- |
| eMMC boot, A/B firmware slots | Yes | Boot ROM reads the bootloader from eMMC directly; HS400ES. App partition grows to fill the eMMC on first boot |
| OTA updates (`mix upload`) | Yes | Delta updates supported (fwup >= 1.12 on device); validation + automatic revert verified |
| Ethernet x2 | Yes | gmac0 + gmac1, RTL8211F each. `eth0` verified with DHCP + internet; `eth1` detected but not tested with a cable |
| WiFi roaming | Disabled | `roamoff=1` in `rootfs_overlay/etc/modprobe.d/brcmfmac.conf`. The firmware's internal roaming engine is a known source of unexpected disassociation and has nothing to roam to at a fixed site. Remove it for multi-AP or mobile installations |
| WiFi (onboard, v1.2+ only) | Yes | BCM43752 (AP6275S) on SDIO via in-kernel brcmfmac; firmware from `package/brcmfmac43752-firmware`. Verified connected with DHCP. `linux/0028` names the module so brcmfmac takes the out-of-band host wake rather than signalling in band over SDIO. v1.0/1.1 boards (RTL8852BS) have no mainline driver |
| microSD boot | Fallback | `mix burn` the same firmware to a card; it boots when the eMMC has no valid loader at sector 64, verified with the eMMC bootloader cleared. With a loader present the eMMC's U-Boot boots the eMMC and does not check the card |
| Bluetooth | Enumerates, untested | `linux/0028` enables uart4 and adds the `brcm,bcm43438-bt` serdev child, from mainline's own v1.2 overlay. The arm64 defconfig already carries `BT_HCIUART_BCM`. `hci0` appears and the controller identifies as BCM4362A2; `brcm/BCM4362A2.hcd` patchram ships in `package/brcmfmac43752-firmware`. Nothing has been paired yet |
| HDMI display + console | Yes | VOP + dw-hdmi-qp, framebuffer console verified on a display. No GL/EGL userspace yet (see GPU row) |
| GPU (Mali G52 MC3) | Yes | Kernel panfrost + Mesa (OpenGL ES 3.1, EGL/GBM, no X11/Wayland); kmscube runs vsync-locked at 60 fps on HDMI. devfreq drives 300-900 MHz off BL31's PVTPLL over SCMI (see GPU). Mesa is built without the LLVM draw module and the orphaned libLLVM is pruned from the image (see external.mk and post-build.sh), so the GL stack costs ~18 MB |
| M.2 NVMe (PCIe 2.1) | Untested | pcie0 + NVMe drivers built in; no drive was fitted during bring-up |
| USB | Yes | 2x Type-C (one PD power input only, one USB 2.0 OTG/maskrom) + USB3 host; verified with a CDC-ACM device (Zigbee coordinator) through the onboard hub. SuperSpeed not yet exercised |
| Audio | Yes | HDMI audio + onboard analog ES8388, both register as ALSA cards; playback not yet exercised |
| Watchdog | Yes | dw-wdt enabled by the board patch; armed by `nerves_heart`, NOWAYOUT |
| RTC | Yes | HYM8563; keeps time with a battery on the board connector |
| CPU frequency scaling | Yes | schedutil via SCMI; A53 cluster to 2.016 GHz, A72 cluster to 2.208 GHz |
| Thermal | Yes | tsadc zones with cpufreq cooling; the NPU zone gets a passive trip and devfreq cooling from `linux/0011` |
| LEDs | Yes | Green heartbeat + red status + mmc activity triggers |
| Hardware RNG | Yes | Two instances. `/dev/hwrng` (`rng@2a410000`) feeds the kernel entropy pool; a second, secure-only block at `0x2a440000` seeds OP-TEE. 1 MB sampled: 7.99981 bits/byte, chi-square 278.9 on 255 df |
| Secure world (OP-TEE) | Opt-in | `SECURE_WORLD=1 ./scripts/build-uboot.sh` builds upstream TF-A + OP-TEE 4.10 in place of rkbin's BL31. Fuses a per-device key on first boot of an unprovisioned part. Verified: key burned, survives power cycles, secure storage initialises |
| PKCS#11 key storage | Opt-in | With the secure world: EC P-256 generated inside OP-TEE, persisted encrypted against the fused key, signed with. The private key never enters Linux. Needs `tee-supplicant` running |
| RPMB | Kernel side only | 4 MiB. `CONFIG_RPMB=y` and the OP-TEE driver binds to it, but `CFG_RPMB_FS` is off, so secure storage uses the app partition instead. Adds rollback protection only. Its key is one-shot and derived from the HUK, so provision it only after the HUK is settled |
| ADC (SARADC) | Yes | Enabled by `linux/0013` (upstream leaves it disabled); header ADC inputs, vref from vcca_1v8_s0 |
| CAN | No | RK3576 CAN-FD has no mainline driver or dts nodes |
| GPIO/I2C/SPI/UART header | Expected | Via [Circuits.*](https://elixir-circuits.github.io/) |
| NPU (6 TOPS) | Yes | Vendor rknpu driver built out-of-tree against the mainline kernel (`package/rknpu-driver`) + librknnrt 2.3.2. IOMMU-backed pageable buffers (no CMA cap), devfreq across 300-900 MHz. Both cores usable together. Verified with MobileNetV2 (250 inf/s, top-5 matching Rockchip's reference exactly), Qwen3-0.6B W4A16 through rkllm 1.3.0 at 17.8 tok/s, and an int8 matmul checked against the CPU. Same results with and without the secure world. Models are built on a host with rknn-toolkit2 |
| Video decode | No | rkvdec2 for RK3576 lands in kernel 7.0 |
| PWM / fan header | Yes | RK3576 has a fourth-generation PWM block that mainline 6.18 does not know; `linux/0024`-`0027` add the driver, the binding, the fourteen channels of pwm1 and pwm2, and a `pwm-fan` on PWM2 channel 7 (GPIO3_D7, mux m3) at 20 kHz. The fan steps 0/50/100/150/200/255 at 50, 55, 60, 65 and 70 °C off the package sensor, and is a normal hwmon device the rest of the time |
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

The image is smaller than the eMMC; on the first boot the system grows
the app partition to fill the disk automatically.

Alternative with no tools: `mix burn` the same firmware to a microSD and
boot from the slot — useful for first bring-up and recovery.

OTA upgrades are the standard Nerves flow (`mix upload`); upgrades write
the inactive slot only and revert automatically unless validated.

## Kernel

Mainline LTS from kernel.org (6.18.40) with the upstream
`rk3576-armsom-sige5` device tree and fourteen patches, each commented
inline: board fixups (`linux/0001`: mmc aliases so the eMMC is
`/dev/mmcblk0`, watchdog enable), SARADC (`0005`), the vendor RKNPU node
with its OPP table and thermal trip (`0002`), power-domain fixes the NPU
needs on mainline (`0003`/`0004`), and the NPU MMU work - the iommu node
itself (`0009`) plus six rockchip-iommu fixes it depends on (`0006`,
`0007`, `0008`, `0010`, `0012`, `0013`) and the binding update describing
its extra banks (`0011`), and a binding for the NPU OPP table's
read-margin properties (`0014`). Configuration is the arm64
`defconfig` plus `linux/nerves.config`, documented inline.

## NPU

The vendor RKNPU driver (v0.9.8) runs on the mainline kernel, with the
vendor-only devfreq/OPP integration reimplemented on generic APIs and the
NPU MMU driven by mainline's rockchip-iommu. Both cores are usable, and
`librknnrt` 2.3.2 and `rkllm` 1.3.0 run unmodified.

- **Memory.** Buffers go through the IOMMU, so they are ordinary pageable
  memory rather than CMA - 1 GB single allocations verified. Vision output
  is bit-exact against Rockchip's reference outputs on both cores.
- **Both cores.** Usable individually or together. Two processes pinned to
  different cores reach 381 inferences/s against 145 for one core alone.
- **Frequency scaling.** devfreq drives 300-900 MHz through the mainline OPP
  core. The clock comes from BL31's PVTPLL over SCMI, which only works while
  `PCLK_NPUTOP_ROOT` is held; mainline gates it, so `linux/0011` claims it.
- **Per-chip selection.** An OTP cell picks the OPP set for the part, so an
  RK3576S gets its 500 MHz ceiling and the J and M parts their higher
  voltages. Unreadable OTP falls back to a restricted table rather than the
  full one.
- **Thermal.** A passive trip at 85 C drives a devfreq cooling device; the
  upstream zone has only a critical trip. Sustained four-thread load at
  900 MHz peaks around 60 C, so the trip is headroom rather than a limit.

### Benchmarks

MobileNet v1 (int8) via `rknn_bench`, `userspace` pinned at each rate:

| Frequency | One thread | Four threads |
| --- | --- | --- |
| 300 MHz | 113.4 inf/s | 309.2 inf/s |
| 600 MHz | 136.7 inf/s | 400.3 inf/s |
| 900 MHz | 146.3 inf/s | 433.1 inf/s |

Tripling the clock buys about 1.3x, because only a fraction of each inference
is NPU time — roughly 2.3 ms of a 9.1 ms four-thread pipeline. The rest is
input conversion on the CPU.

Soak: 140,000 inferences over 313 s of continuous four-thread load at 900 MHz,
447.9 inf/s, no failures, peaking at 60-62 C against an 85 C trip.

Qwen3-0.6B (W4A16) through rkllm 1.3.0 decodes at 17.8 tokens/s.

Governor behaviour, the two `dvfs_*` module knobs, and where this differs from
the 6.1 vendor BSP are in
[docs/research/rk3576-npu.md](docs/research/rk3576-npu.md).

## GPU

panfrost drives the Mali-G52 through the mainline OPP core at 300-900 MHz. The
rate comes from BL31's PVTPLL over SCMI rather than the CRU, whose dividers
cannot produce the upper rates.

**The OPP rates are nominal.** BL31 accepts and echoes back every rate exactly,
but that is not what the shader cores run at. Measured with panfrost's own
cycle counter:

| Requested | Delivered |
| --- | --- |
| 300 MHz | 430 MHz |
| 400 MHz | 510 MHz |
| 500 MHz | 646 MHz |
| 600 MHz | 772 MHz |
| 700 MHz | 802 MHz |
| 800 MHz | 802 MHz |
| 900 MHz | 821 MHz |

An OPP rate names an operating point — a PVTPLL ring length and a voltage — not
a frequency, so the real ceiling is ~821 MHz and 700/800 MHz land on the same
rate. Throughput follows the delivered clock to within 2%, the figures are
stable per chip, and rkbin's BL31 and upstream TF-A produce identical results.
Why, and how to measure it again, is in
[docs/research/rk3576-gpu-clocks.md](docs/research/rk3576-gpu-clocks.md).

- **Three clocks.** SCMI `CLK_GPU` carries the rate; `PCLK_GPU_ROOT` and CRU
  `CLK_GPU` reach the registers BL31 programs PVTPLL through. The DT names all
  three, which is what keeps the clock framework from gating them at boot.
- **Runtime PM.** A rate request only reaches PVTPLL with the power domain up,
  so the clock parks at 200 MHz before suspend and the requested rate is
  reapplied on resume.
- **Per-chip OPP set.** An OTP cell picks it: 900 MHz on the RK3576, 800 MHz on
  the S, J and M parts. Unreadable OTP falls back to a restricted table.
- **The OPP table stops at 900 MHz**, which table 3-2 of the datasheet
  (rev 1.5) gives as the GPU maximum.

### Benchmarks

`glmark2-es2-drm` off-screen through GBM, 1920x1080, fragment-bound scene,
`userspace` pinned at each rate:

| Frequency | FPS | vs 300 MHz |
| --- | --- | --- |
| 300 MHz | 20.0 | 1.00x |
| 600 MHz | 36.0 | 1.80x |
| 900 MHz | 38.0 | 1.90x |

Throughput tracks the requested rate to 600 MHz and then flattens, which is the
delivered clock saturating near 820 MHz rather than a workload ceiling — frame
rate per actual GPU cycle is constant throughout. Seven scenes covering
fragment, ALU, vertex, texture and two real workloads all return 1.02-1.06x
from 600 to 900 MHz.

Sustained load at 900 MHz holds 38.0 FPS with no falloff, peaking at 82 C
against a 115 C critical trip, unchanged with eight CPU workers alongside.

## Debug UART

40-pin header: pin 8 (TX), pin 10 (RX), pin 6 (GND), 1500000 8N1,
3.3 V TTL.
