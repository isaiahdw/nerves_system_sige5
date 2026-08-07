# Nerves System for the ArmSoM Sige5 / Banana Pi BPI-M5 Pro (Rockchip RK3576)

A [Nerves](https://nerves-project.org) system for the
[ArmSoM Sige5](https://docs.armsom.org/armsom-sige5), also sold as the
Banana Pi BPI-M5 Pro (identical hardware). Rockchip RK3576
(4x Cortex-A72 + 4x Cortex-A53, Mali-G52 MC3), LPDDR4X, soldered
32–128 GB eMMC, 2x Gigabit Ethernet, WiFi 6/BT, HDMI 2.1, M.2 NVMe,
USB 3.0, and a 40-pin GPIO header. Built on a mainline LTS kernel
(6.18.y) and mainline U-Boot. The IEx console is on `ttyS0`, the
1.5 Mbaud debug UART on header pins 8/10/6.

**Board revision v1.2 or later.** The device tree drives the v1.2 WiFi/BT
module (BCM43752 on SDIO, BCM4362A2 on uart4) directly, including its reset
and wake GPIOs. Earlier boards carry an RTL8852BS instead, which has no
mainline driver and does not share that wiring, so they are not supported
here - not merely untested.

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
| CAN | No mainline driver |
| Video decode | rkvdec2 for RK3576 lands in kernel 7.0 |
| MIPI CSI/DSI | Not wired up in mainline for this board |

Untested: M.2 NVMe. The drivers are built in; no drive was fitted.

**GPU and NPU frequency labels are nominal.** Ask the GPU for 300 MHz and you
get about 423; ask for 900 and you get about 815. The clock is a PVTPLL that
tracks the silicon rather than a divider, so an OPP names an operating point,
not a frequency —
[docs/research/rk3576-gpu-clocks.md](docs/research/rk3576-gpu-clocks.md).

**Secure-world keys are bound to the board.** They are encrypted against a fuse
in the SoC and stored on the app partition, so a data wipe loses them and the
device re-enrols. Moving the eMMC to another board loses them too.

**The SDIO bus produces access errors under load.** `linux/0029` retries
through them: across two hours of forced wakes plus sustained transfer, none
reached the network stack - no `-110`, no aborted frames, no interface errors.
A handshake that exhausts the retry budget would still surface, so this is
measured rather than guaranteed. Roughly one in 5000 needs a retry. The cause
is not established; the remaining lead is the phase-map support Rockchip added
upstream in 7.1 (commit cc1060a18e04), which is not in this kernel.

## Boot architecture

Everything lives on the soldered eMMC. The RK3576 boot ROM on this board
(no SPI NOR fitted) loads U-Boot directly from the eMMC at sector 64;
fwup's factory `complete` task writes the bootloader there as part of the
disk image, and upgrades never touch it.

A microSD card boots when the eMMC has no valid loader at sector 64. With one
present, the eMMC's U-Boot runs and boots the eMMC; it does not check the card.
Maskrom is the recovery path when neither will boot:

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

## Secure world (OP-TEE) — the bootloader is opt-in, the rest ships

> **A worked example, not a production configuration.** It shows TrustZone
> running on an RK3576 and every claim below is accurate, but the pieces
> working is not the same as a device being secure. See
> [What this does not give you](#what-this-does-not-give-you).

Only the BL32 is opt-in. The default bootloader has none, so the boot chain
runs BL31 and nothing in TrustZone above it and `/dev/tee0` never appears.

Everything else is in every image, so that one image boots on either
bootloader:

| always present | cost |
| --- | --- |
| The device tree's secure-world memory reservation | 36 MB of 8 GB |
| The kernel's OP-TEE and RPMB drivers | built in, unused with no BL32 |
| `optee-client` (`tee-supplicant`), `optee-key`, `libp11` | a few hundred KB of rootfs |

None of it does anything without a BL32 - the SMC goes unanswered, so the
driver never registers and `tee-supplicant` has nothing to talk to - but it is
not accurate to call the feature absent from the default build. Removing it
would mean a second defconfig and a second device tree.

### What this does not give you

Closing these is per-product work that cannot live in a board support package:

- **Verified boot.** The big one. Nothing checks the bootloader, so whoever can
  write the eMMC boots their own and asks the secure world to sign. The key
  stays unextractable; it does not stay exclusively yours. Fixing it means
  burning the RSA hash and the secure-boot word, after which maskrom needs
  signed loaders too.
- **A TA signing key you control.** A development key signs a TA that shares
  the PKCS#11 UUID, and therefore its stored objects.
- **A real PIN policy.** The token PIN is a constant here and cannot be
  otherwise on a device that boots unattended; the protection is the key being
  non-extractable, not the PIN.
- **Rollback protection.** The RPMB counter versions nothing, so old firmware
  and old secure storage can be put back.
- **Manufacturing provisioning.** The device fuses its own HUK on first boot.
  Fine for a bench board, not how a fleet should be keyed.
- **Attestation.** Nothing proves to a server that a key really is inside a
  given board.

Whether the RK3576 secure OTP has ECC, parity or lock bits is also
unestablished - measure it before relying on how a partly-written word reads.

What the example does establish, on two boards: the HUK reads back, the PKCS#11
TA loads against a matched core, and a CSR signed inside the secure world
verifies with stock `openssl`.

There is no separate secure element on this board, so TrustZone is the only
place a key can live that survives root or a desoldered eMMC. A NervesHub
private key lives inside OP-TEE, sealed by a per-device hardware key. The
application asks the secure world to sign and gets a signature back; it never
sees the key, and pulling the eMMC yields ciphertext.

```
BL31 (TF-A)  ── loads ──▶  BL32 (OP-TEE)
                              │
       Linux  ── /dev/tee0 ──▶│ signs with a key it never hands over
                              └─ sealed against a key fused into the SoC
```

### Three keys, and the one they protect

They are easy to conflate. Each answers a different question, at a different
moment, and only one of them is made on the device.

| | decides | lives | made by | here |
| --- | --- | --- | --- | --- |
| HUK | what can decrypt this device's stored secrets | fused in the SoC's OTP | the device, on first boot of a fusing image | yes |
| TA signing key | what code the secure world will load | `~/.config/nerves_system_sige5/ta-sign.pem` | you, once, by hand | yes |
| Secure boot key | what firmware may run at all | hash fused in OTP, private half yours | you | **no** |

The device key is the fourth thing, and the one the other three exist to
protect: an EC P-256 generated inside the trusted application and sealed
against the HUK.

The HUK is per-device and never leaves the chip. The other two are per-fleet
and yours to hold: neither can be rotated on a device already flashed, because
the TA key's public half is inside that device's OP-TEE core and the secure
boot hash is in its fuses. Losing either means reflashing every board with a
matched pair. Leaking the TA key means anyone can sign a trusted application
that reads stored keys on every device built against it.

The two that exist cover different gaps, which is why neither substitutes for
the other. Trusted applications are not part of the boot chain - the PKCS#11
TA is an ordinary file in the rootfs, loaded on demand long after boot - so
secure boot would say nothing about it, and its signature is the only thing
checked. In the other direction, without secure boot an attacker who can
replace BL32 supplies a core built against their own TA key, and the signature
check goes with it. That is the boundary
[described below](#what-it-does-not-protect-against).

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

OP-TEE embeds the public half and loads only trusted applications signed by the
private half. A TA's secure-storage key is derived from the HUK and the TA's
UUID, so this key controls what can read the device key. It is separate from
secure boot: secure boot decides what firmware may run, this decides what the
secure world will load.

Keep it, and keep it off devices. A rebuilt core will not load TAs signed with
a different key, so losing it means reflashing every device with a matched
pair.

Only the public half enters the build container. OP-TEE builds against
`TA_PUBLIC_KEY`; the trusted application is signed afterwards on this machine,
where the private key is.

That signing step runs as the user who owns the key, so what else it runs
matters. Every input is pinned: the OP-TEE commit by full SHA,
`scripts/sign_encrypt.py` checked against a recorded hash before it is
executed, and the Python it needs installed from
`scripts/signing-requirements.txt` with `--require-hashes`. Only `openssl` is
handed the key; the rest is pinned because it runs with permission to read it.

### What the patches add

Thirteen patches, applied to a pinned optee_os by `scripts/build-uboot.sh`:

- a HUK read from the secure OTP, rejecting a slot with an all-zero word
- `hw_get_random_bytes()` driving RKRNG, and PRNG seeding from it; an unreadable
  TRNG is fatal
- a secure-world console on the debug UART
- read-only diagnostics behind `SECURE_WORLD_DEBUG=1`, off by default: an OTP
  survey, a search for the secure TRNG, and a dry run reporting what a burn
  would do
- the burn itself, off by default

Upstream TF-A v2.15.0 with OP-TEE 4.10. The PKCS#11 TA loads from the rootfs
and `/dev/tee0` appears; secure storage initialises once a HUK is fused.

### The per-device key

The RK3576 secure OTP ships with no hardware unique key; the OEM burns one.
Without it secure storage cannot initialise, so a `SECURE_WORLD=1` image fuses
one on the first boot of an unprovisioned part.

It writes four words at index `0x80`, and only after the slot is confirmed
blank, the candidate passes a set of sanity checks, and a second independent
draw from the TRNG differs from the first. It then reads the value back and
refuses to use it unless it matches. A later boot on a provisioned part does
nothing.

On a blank part the console shows it happening, which is what to compare
against:

    E/TC:0 0 tee_otp_get_hw_unique_key:691 No HUK in secure OTP at index 0x80
    I/TC: HUK burn: committing 4 words at index 0x80 - irreversible
    I/TC: HUK burn: done, verified

A part that already has a key prints no burn line at all.

Fusing is irreversible, but it is not a brick risk: the four words are written
in a short window, and the fuses that can brick an RK3576 are the secure-boot
control word and the RSA hash, neither of which this touches.

An interrupted burn is mostly caught, not entirely. Rejecting a slot with an
all-zero word catches an interruption between words, and the burn reads the key
back and compares it exactly before trusting it, which catches a word that
programmed wrong. What neither catches is a power cut in the gap between the
last word landing and the compare running: a partly programmed word can read
non-zero, so all four read non-zero and the slot looks complete.

That window is left open on purpose. Recording completion in a second OTP word
would close it and cost more than it saves, for reasons set out in
docs/research/rk3576-secure-world.md.

### What it does not protect against

The boot chain is not verified, so the boundary is narrower than "the key
cannot be extracted". What holds is:

> Protects against compromised normal-world software, and against storage
> removed from the board, for as long as the bootloader and BL32 are intact.

Someone who can replace BL32 runs code at S-EL1 and can read the same secure
OTP words OP-TEE reads, so they can take the key itself, not merely use it.
Closing that means verified boot, which is more fuses — and the ones that
enable it are the ones that can brick a part. What that would take, stage by
stage, is in
[docs/research/rk3576-secure-boot-plan.md](docs/research/rk3576-secure-boot-plan.md).

Keys are also sealed against a fuse in this SoC and stored on the app
partition, so a data wipe or a different board means re-enrolment.

### RPMB

Three separate things. Only the first is on.

| | State |
| --- | --- |
| Kernel RPMB subsystem | `CONFIG_RPMB=y`. `/sys/class/rpmb/rpmb0` enumerates and the OP-TEE driver binds to it |
| OP-TEE storing anything there | Off. `CFG_RPMB_FS` defaults to `n` and nothing here sets it |
| The eMMC's RPMB key | Never programmed |

Secure storage runs on `CFG_REE_FS`: keys are encrypted against the HUK and
written to the app partition through `tee-supplicant`. That is what "sealed
against a fuse and stored on the app partition" above means. RPMB would add
rollback protection — stopping someone who holds the eMMC from restoring an
older copy of secure storage — and nothing else.

Enabling it means `CFG_RPMB_FS` and `CFG_RPMB_WRITE_KEY`. The key write is
one-shot and the key is derived from the HUK, so fuse the HUK and confirm it
works first; a key written from the wrong HUK strands the eMMC's RPMB
permanently.

[docs/research/rk3576-secure-world.md](docs/research/rk3576-secure-world.md)
has the secure address map, the OTP layout, and how this compares to a Trust&GO
ATECC608.

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

Editing anything in `linux/` costs a full kernel rebuild. `external.mk` hashes
the patch set and keeps the hash inside the extracted tree; a changed hash
discards the tree so the next step extracts and patches a fresh one. The same
guard covers `package/rknpu-driver/*.patch` and `package/optee-key/src/`.

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

- The bootloader is inside that image; there is no separate write at sector 64.
- `db` puts Rockchip's SPL loader in RAM and everything is written through it.
  88 seconds for the whole 1.8 GB image.
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
`rk3576-armsom-sige5` device tree and thirty patches, each commented
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

Power domains and the secure world (`0020`-`0023`): optional resets in the
Rockchip power-domain binding and driver (`0020`, `0021`), the NPU domains'
BIU resets (`0022`), and the OP-TEE firmware node with its memory reservation
(`0023`).

PWM (`0024`-`0027`): the RK3576 controller binding (`0024`), v4 support in
pwm-rockchip (`0025`), the pwm1 and pwm2 nodes (`0026`), and the fan on the
header (`0027`).

WiFi and Bluetooth (`0028`): the module named under the SDIO controller so
brcmfmac takes the out-of-band host wake, and uart4 enabled with its
Bluetooth child - the contents of mainline's
`rk3576-armsom-sige5-v1.2-wifibt.dtso`, which this system cannot apply as an
overlay because it builds a single device tree.

brcmfmac (`0029`-`0030`): the SDIO wake handshake keeps the retry budget its
loop already declares, and register access to a device that never woke is
refused.

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
