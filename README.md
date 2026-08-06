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

## What works

Exercised on hardware:

- eMMC boot, A/B firmware slots, automatic revert
- OTA updates
- Ethernet x2, onboard WiFi
- HDMI with a framebuffer console
- GPU under Mesa — OpenGL ES 3.1, no X11 or Wayland
- NPU with vision and language models
- CPU, GPU and NPU frequency scaling
- Thermal management and the fan header
- Watchdog, RTC, ADC, USB, audio devices, hardware RNG

Opt-in, one build flag: a secure world. OP-TEE with a per-device key fused into
the SoC, and PKCS#11 for keys that never leave it.

Not working:

| | Why |
| --- | --- |
| Bluetooth | uart4 is disabled in the mainline dts |
| CAN | No mainline driver |
| Video decode | rkvdec2 for RK3576 lands in kernel 7.0 |
| MIPI CSI/DSI | Not wired up in mainline for this board |

Untested: M.2 NVMe. The drivers are built in; no drive was fitted.

Two behaviours that look like bugs and are not:

**GPU and NPU frequency labels are nominal.** Ask the GPU for 300 MHz and you
get about 423; ask for 900 and you get about 815. The clock is a PVTPLL that
tracks the silicon rather than a divider, so an OPP names an operating point,
not a frequency —
[docs/research/rk3576-gpu-clocks.md](docs/research/rk3576-gpu-clocks.md).

**Secure-world keys are bound to the board.** They are encrypted against a fuse
in the SoC and stored on the app partition, so a data wipe loses them and the
device re-enrols. Moving the eMMC to another board loses them too.

## Boot architecture

Everything lives on the soldered eMMC. The RK3576 boot ROM on this board
(no SPI NOR fitted) loads U-Boot directly from the eMMC at sector 64;
fwup's factory `complete` task writes the bootloader there as part of the
disk image, and upgrades never touch it.

A microSD card boots when the eMMC has no valid loader at sector 64 — verified
by clearing the eMMC bootloader first. With one present, the eMMC's U-Boot runs
and boots the eMMC; it does not check the card. Maskrom is the recovery path
when neither will boot:

```sh
# board in maskrom, USB-C on the OTG port
fwup -a -d nerves.img -i firmware.fw -t complete   # raw disk image
rkdeveloptool db rk3576_spl_loader_v1.09.108.bin
rkdeveloptool wl 0 nerves.img
```

That restores a board with a blank or broken eMMC without a card or a running
system.

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

## Secure world (OP-TEE) — opt-in, not in the shipped image

The default image has no BL32. The boot chain runs BL31 and nothing in
TrustZone above it, so `/dev/tee0` never appears even though the kernel carries
the OP-TEE driver.

One thing is not opt-in: the device tree reserves the secure world's memory
unconditionally, because the same image has to boot on either bootloader and a
reservation that disagrees with the running BL32 is worse than an unused one.
That costs 50 MB of 8 GB whether or not a secure world is present — 36 MB for
upstream OP-TEE and 14 MB for a Rockchip BL32 that is no longer built here and
should be dropped.

There is no separate secure element on this board, so TrustZone is the only
place a key can live that survives root or a desoldered eMMC.

Take a private key for NervesHub. Without a secure world it is a file on the
data partition: anyone who gets root once, or unsolders the eMMC, reads it and
can impersonate the device forever. With one, the key lives inside OP-TEE
sealed by a per-device hardware key and the application never sees it — it asks
the secure world to sign a challenge and gets a signature back. Pulling the
eMMC yields ciphertext.

```
BL31 (TF-A)  ── loads ──▶  BL32 (OP-TEE)
                              │
       Linux  ── /dev/tee0 ──▶│ signs with a key it never hands over
                              └─ sealed against a key fused into the SoC
```

### Building it

```sh
SECURE_WORLD=1 ./scripts/build-uboot.sh
```

That needs a TA signing key, which the build refuses to run without:

```sh
mkdir -p ~/.config/nerves_system_sige5
openssl genrsa -out ~/.config/nerves_system_sige5/ta-sign.pem 2048
chmod 600 ~/.config/nerves_system_sige5/ta-sign.pem
```

OP-TEE embeds the public half and loads only trusted applications signed by
the private half. Its own default key is published in their repository, so
building with that would let anyone sign a TA carrying the PKCS#11 UUID — and
because a TA's secure-storage key is derived from the HUK and its UUID, that
TA reads the device key. The key is separate from secure boot: secure boot
decides what firmware may run, this decides what the secure world will load.

Keep it, and keep it off devices. A rebuilt core will not load TAs signed with
a different key, so losing it means reflashing every device with a matched
pair.

That writes `uboot/u-boot-rockchip.bin` — the file fwup packages — so rebuild
the system, build firmware, and flash normally. There is no second bootloader
and nothing to swap in at flash time.
`uboot/u-boot-rockchip.variant` records which build is in the binary.

An image built this way **fuses a hardware unique key on the first boot of a
part that has none**, because a secure world without one cannot store anything.
It only ever writes a blank slot and only after its checks pass, so booting it
on a part that already has a key does nothing.

It replaces BL31 as well. The GPU measurements below were taken on Rockchip's
BL31; both firmwares deliver identical rates, verified rather than assumed.

Rockchip's own BL32 blob is not an option here; see
[docs/research/rk3576-firmware-versions.md](docs/research/rk3576-firmware-versions.md).

### What the patches add

Thirteen patches, applied to a pinned optee_os by `scripts/build-uboot.sh`:

- a HUK read from the secure OTP at the confirmed index, rejecting a slot with
  an all-zero word rather than accepting a short key
- `hw_get_random_bytes()` driving RKRNG, and PRNG seeding from it — a TRNG that
  cannot be read is fatal rather than silently degraded
- a secure-world console, without which OP-TEE's own diagnostics go nowhere and
  a TA that will not start looks identical to one that is missing
- read-only diagnostics: an OTP survey, a search for the secure TRNG, and a dry
  run reporting what a burn would do
- the burn itself, off by default

Working on hardware: upstream TF-A v2.15.0 + OP-TEE 4.10 boot, the PKCS#11 TA
loads from the rootfs signed with the key the core was built with, and
`/dev/tee0` appears. Secure storage initialises once a HUK is fused; the part
ships without one, so an unprovisioned board gets one on first boot — see
below.

The full investigation is in
[docs/research/rk3576-secure-world.md](docs/research/rk3576-secure-world.md):
where the HUK lives and how that was confirmed against Rockchip's own driver,
that the secure OTP really is unreachable from the normal world, where the
secure TRNG is, what the burn checks before committing anything, and what a
power cut during it actually costs.

### The per-device key

The RK3576 secure OTP ships with no hardware unique key — Rockchip expects the
OEM to burn one, which is why `trusty_write_oem_huk` exists in their stack.
Without it secure storage cannot initialise, so a `SECURE_WORLD=1` image fuses
one on the first boot of an unprovisioned part.

It writes four words at index `0x80`, and only after the slot is confirmed
blank, the candidate passes a set of sanity checks, and a second independent
draw from the TRNG differs from the first. It then reads the value back and
refuses to use it unless it matches. A later boot on a provisioned part does
nothing.

Fusing is irreversible, but it is not a brick risk: the four words are written
in a short window, and the fuses that can brick an RK3576 are the secure-boot
control word and the RSA hash, neither of which this touches.

Losing power mid-write is not fully guarded, though. The read path rejects a
slot with an all-zero word, which catches an interruption between words but not
one inside the last word — a partly programmed word that happens to be non-zero
reads as a complete key. Treat an interrupted burn as suspect rather than as
guaranteed-rejected.

Verified end to end on hardware: key fused and read back, surviving power
cycles, secure storage initialising, and an EC P-256 keypair generated inside
OP-TEE, persisted, and used to sign — with the private key never entering
Linux.

### What it does not protect against

The boot chain is not verified, so the boundary is narrower than "the key
cannot be extracted". What holds is:

> Protects against compromised normal-world software, and against storage
> removed from the board, for as long as the bootloader and BL32 are intact.

Someone who can replace BL32 runs code at S-EL1 and can read the same secure
OTP words OP-TEE reads, so they can take the key itself, not merely use it.
Closing that means verified boot, which is more fuses — and the ones that
enable it are the ones that can brick a part.

Keys are also sealed against a fuse in this SoC and stored on the app
partition, so a data wipe or a different board means re-enrolment.

### RPMB

`CONFIG_RPMB` is on and enumerates `/sys/class/rpmb/rpmb0`, which the OP-TEE
driver binds to — the secure world reaches the replay-protected partition
through the kernel rather than proxying every frame through `tee-supplicant`.

The RPMB key is one-shot and OP-TEE derives it from the HUK, so the order
matters: fuse the HUK, validate it, and only then provision RPMB — otherwise
one mistake strands the eMMC as well.

[docs/research/rk3576-secure-world.md](docs/research/rk3576-secure-world.md)
has the secure address map, the OTP layout, how this compares to a Trust&GO
ATECC608, and why RPMB is optional rather than required.

## Hardware support

Verified on a Sige5 v1.2, 2026-08-05.

| Feature | Status | Notes |
| --- | --- | --- |
| eMMC boot, A/B firmware slots | Yes | Boot ROM reads the bootloader from eMMC directly; HS400ES. App partition grows to fill the eMMC on first boot |
| OTA updates (`mix upload`) | Yes | Delta updates supported (fwup >= 1.12 on device); validation + automatic revert verified |
| Ethernet x2 | Yes | gmac0 + gmac1, RTL8211F each. `eth0` verified with DHCP + internet; `eth1` detected but not tested with a cable |
| WiFi (onboard, board v1.2+) | Yes | BCM43752 (AP6275S) on SDIO via in-kernel brcmfmac; firmware from `package/brcmfmac43752-firmware`. Verified connected with DHCP. v1.0/1.1 boards (RTL8852BS) have no mainline driver |
| microSD boot | Fallback | `mix burn` the same firmware to a card; it boots when the eMMC has no valid loader at sector 64, verified with the eMMC bootloader cleared. With a loader present the eMMC's U-Boot boots the eMMC and does not check the card |
| Bluetooth | No | uart4 is deliberately disabled in the mainline dts; needs a serdev node + bring-up |
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
| RPMB | Available, unused | 4 MiB, reached through the kernel RPMB subsystem. Adds rollback protection only; not needed for key storage. Its key is one-shot and derived from the HUK, so provision it only after the HUK is settled |
| ADC (SARADC) | Yes | Enabled by `linux/0013` (upstream leaves it disabled); header ADC inputs, vref from vcca_1v8_s0 |
| CAN | No | RK3576 CAN-FD has no mainline driver or dts nodes |
| GPIO/I2C/SPI/UART header | Expected | Via [Circuits.*](https://elixir-circuits.github.io/) |
| NPU (6 TOPS) | Yes | Vendor rknpu driver built out-of-tree against the mainline kernel (`package/rknpu-driver`) + librknnrt 2.3.2. IOMMU-backed pageable buffers (no CMA cap), devfreq across 300-900 MHz. Both cores usable together. Verified with MobileNetV2 (250 inf/s, top-5 matching Rockchip's reference exactly), Qwen3-0.6B W4A16 through rkllm 1.3.0 at 17.8 tok/s, and an int8 matmul checked against the CPU. Same results with and without the secure world. Models are built on a host with rknn-toolkit2 |
| Video decode | No | rkvdec2 for RK3576 lands in kernel 7.0 |
| PWM / fan header | Yes | RK3576 has a fourth-generation PWM block that mainline 6.18 does not know; `linux/0023`-`0026` add the driver, the binding, the fourteen channels of pwm1 and pwm2, and a `pwm-fan` on PWM2 channel 7 (GPIO3_D7, mux m3) at 20 kHz. The fan steps 0/50/100/150/200/255 at 50, 55, 60, 65 and 70 °C off the package sensor, and is a normal hwmon device the rest of the time |
| MIPI CSI/DSI | No | Not wired up in mainline for this board |

## Building

Linux (or the Nerves Docker build environment) is required:

```sh
mix deps.get
mix compile
```

Editing anything in `linux/` costs a full kernel rebuild. Buildroot applies
`linux/*.patch` only when it first extracts the kernel, so `external.mk` keeps
a hash of the patch set inside the extracted tree and discards the tree when
the hash stops matching. Without that, an added or edited patch is ignored and
the build still succeeds — the change is simply absent from the image.

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

Factory flash goes to the eMMC over USB maskrom. Connect the OTG Type-C port
to the host, then hold MASKROM while connecting power to the other (PD-only)
Type-C port:

```sh
fwup -a -d disk.img -t complete -i <firmware>.fw   # raw image on the host
rkdeveloptool db uboot/rk3576_spl_loader_v1.09.108.bin
rkdeveloptool wl 0 disk.img
rkdeveloptool rd
```

- The bootloader is inside that image. There is no separate write at sector 64.
- 88 seconds for the whole 1.8 GB image, measured.
- `db` is load-bearing: it puts Rockchip's SPL loader in RAM and everything is
  written through that. U-Boot's own `rockusb` gadget takes the same commands
  and runs about 35× slower — the same image did not finish in 3000 s.
  `rkdeveloptool ld` prints `Maskrom` for both, so a crawling write means the
  wrong transport.
- The image is smaller than the eMMC. First boot grows the app partition to
  fill the disk.

Other routes — over the network, from a U-Boot prompt, from maskrom —
are in [docs/flashing.md](docs/flashing.md). `mix burn` to a microSD needs no
tools at all, and boots when the eMMC has no valid loader.

Bring-up notes are in [docs/research/](docs/research/README.md): the secure
OTP, the GPU clock path, the vendor OPP tables and the BSP artifacts they were
decoded from.

OTA upgrades are the standard Nerves flow (`mix upload`); upgrades write
the inactive slot only and revert automatically unless validated. The
bootloader is not part of an A/B update — changing it is a separate write at
sector 64, with one copy and no revert.

## Kernel

Mainline LTS from kernel.org (6.18.40) with the upstream
`rk3576-armsom-sige5` device tree and twenty-six patches, each commented
inline.

NPU and board (`0001`-`0014`): bindings for the NPU MMU and the RKNPU OPP
table (`0001`, `0002`), six rockchip-iommu fixes the NPU MMU depends on
(`0004`-`0009`), a per-domain power-on settle delay (`0003`), board fixups
(`0010`: mmc aliases so the eMMC is `/dev/mmcblk0`, watchdog enable), the
RKNPU node with its OPP table and thermal trip (`0011`), RKNN clocks held
across NPU power transitions (`0012`), SARADC (`0013`), and the NPU MMU
node itself (`0014`).

GPU (`0015`-`0019`): a binding conditional requiring the RK3576 clock trio
(`0015`), a devfreq fix so the monitor does not start on a suspended device
(`0016`), the clocks BL31 needs held by panfrost (`0017`), DVFS coordinated
with runtime PM (`0018`), and the SCMI clock with its per-variant OPP
selection (`0019`).

Configuration is the arm64 `defconfig` plus `linux/nerves.config`,
documented inline.

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

Other things worth knowing:

- **Three clocks.** SCMI `CLK_GPU` carries the rate; `PCLK_GPU_ROOT` and CRU
  `CLK_GPU` reach the registers BL31 programs PVTPLL through. BL31 does not
  enable those two itself and nothing else claims the gate, so without naming
  them in the DT the clock framework disables them as unused during boot.
- **Runtime PM.** A rate request only reaches PVTPLL with the power domain up,
  so the clock parks at 200 MHz before suspend and the requested rate is
  reapplied on resume.
- **Per-chip OPP set.** An OTP cell picks it: 900 MHz on the RK3576, 800 MHz on
  the S, J and M parts. Unreadable OTP falls back to a restricted table.
- **950 MHz is dropped.** BL31's rate table has no entry for it and table 3-2
  of the datasheet (rev 1.5) gives 900 MHz as the GPU maximum.

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
