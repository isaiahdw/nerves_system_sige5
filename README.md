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

Verified on a Sige5 v1.2, 2026-08-01.

| Feature | Status | Notes |
| --- | --- | --- |
| eMMC boot, A/B firmware slots | Yes | Boot ROM reads the bootloader from eMMC directly; HS400ES. App partition grows to fill the eMMC on first boot |
| OTA updates (`mix upload`) | Yes | Delta updates supported (fwup >= 1.12 on device); validation + automatic revert verified |
| Ethernet x2 | Yes | gmac0 + gmac1, RTL8211F each. `eth0` verified with DHCP + internet; `eth1` detected but not tested with a cable |
| WiFi (onboard, board v1.2+) | Yes | BCM43752 (AP6275S) on SDIO via in-kernel brcmfmac; firmware from `package/brcmfmac43752-firmware`. Verified connected with DHCP. v1.0/1.1 boards (RTL8852BS) have no mainline driver |
| microSD boot | Untested | Boot ROM path; the same image should work for bring-up/recovery |
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
| Hardware RNG | Yes | /dev/hwrng feeds the kernel entropy pool |
| ADC (SARADC) | Yes | Enabled by `linux/0013` (upstream leaves it disabled); header ADC inputs, vref from vcca_1v8_s0 |
| CAN | No | RK3576 CAN-FD has no mainline driver or dts nodes |
| GPIO/I2C/SPI/UART header | Expected | Via [Circuits.*](https://elixir-circuits.github.io/) |
| NPU (6 TOPS) | Yes | Vendor rknpu driver built out-of-tree against the mainline kernel (`package/rknpu-driver`) + librknnrt 2.3.2. IOMMU-backed pageable buffers (no CMA cap), devfreq across 300-900 MHz. Both cores usable together. Verified with single, chained and dual-core int8 models and an LLM; models are built on a host with rknn-toolkit2 |
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

The image is smaller than the eMMC; on the first boot the system grows
the app partition to fill the disk automatically.

Alternative with no tools: `mix burn` the same firmware to a microSD and
boot from the slot — useful for first bring-up and recovery.

OTA upgrades are the standard Nerves flow (`mix upload`); upgrades write
the inactive slot only and revert automatically unless validated.

## Kernel

Mainline LTS from kernel.org (6.18.40) with the upstream
`rk3576-armsom-sige5` device tree and nineteen patches, each commented
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

MobileNet v1 (int8) via `rknn_bench`, Sige5 v1.2 on a 12 V/3 A supply. The
`userspace` governor pinned at each rate, with `dvfs_boost` cleared so the
floor does not clamp the low end:

| Frequency | One thread | Four threads |
| --- | --- | --- |
| 300 MHz | 113.4 inf/s, 8.78 ms | 309.2 inf/s, 12.86 ms |
| 400 MHz | 121.2 inf/s, 8.20 ms | 346.8 inf/s, 11.46 ms |
| 500 MHz | 127.9 inf/s, 7.78 ms | 377.5 inf/s, 10.53 ms |
| 600 MHz | 136.7 inf/s, 7.28 ms | 400.3 inf/s, 9.92 ms |
| 700 MHz | 139.9 inf/s, 7.10 ms | 411.7 inf/s, 9.65 ms |
| 800 MHz | 143.1 inf/s, 6.95 ms | 423.5 inf/s, 9.37 ms |
| 900 MHz | 146.3 inf/s, 6.80 ms | 433.1 inf/s, 9.16 ms |

Tripling the clock buys about 1.3x the throughput, because only a fraction of
each inference is NPU time - roughly 2.3 ms of a 9.1 ms four-thread pipeline,
derived from the driver's own busy counters. The rest is input conversion on
the CPU.

Soak: 140,000 inferences over 313 s of continuous four-thread load at 900 MHz,
447.9 inf/s, no failures. Package and DDR peaked at 60-62 C against an 85 C
trip, with the cooling device never engaging.

### Governors

The NPU comes up on `simple_ondemand`. Scaling is driven by a PM QoS minimum
frequency that the driver raises when it takes a power reference and drops
when the device powers down, so the rate is at maximum before the work runs:

| | Throughput (4 threads) | Cold submit, median |
| --- | --- | --- |
| Floor held (default) | 432.8 inf/s | 6.25 ms |
| Floor cleared, duty cycle only | 308.3 inf/s | 8.39 ms |
| `userspace` pinned at 900 MHz | 431.6 inf/s | - |

Cold-submit figures are 11 samples each with `runtime_status` verified
`suspended` before every submission.

Utilisation alone cannot drive this device: the busiest core measures about
50% at full load and the highest rate and about 64% at the lowest, never
reaching the 85% `simple_ondemand` needs to hold a rate. With the floor
cleared it settles at 300 MHz. `0015` exposes the raw counters at
`/sys/kernel/debug/rknpu/dvfs`.

The floor is held until the device powers down, so the rate stays at maximum
and then parks at 200 MHz suspended, with no intermediate step. The deferred
power-off worker is armed when the reference count falls to one and is not
re-armed by later releases, so `power_put_delay` (3000 ms) runs from that point
rather than from the last job: measured from the end of a run, 1275 ms after a
2 s run and 255 ms after a 55 s run.

Two knobs, both writable at runtime under `/sys/module/rknpu/parameters/`:

    dvfs_boost          hold the rate up from acquisition (default Y)
    dvfs_demand_metric  report demand instead of the measured duty cycle (default N)

Clearing `dvfs_boost` takes effect at the next power acquisition or power-down,
not at the write. It also has to be cleared before `userspace` can pin the NPU
*below* maximum - the floor is a minimum constraint and clamps `set_freq` up.

Thermal throttling overrides the floor. With the trip lowered to 55 C, the
cooling device rises through states 2 to 6 and `cur_freq` follows `max_freq`
down 700 → 600 → 500 → 400 → 300 MHz while the floor remains installed at
900.

### Differences from the 6.1 vendor BSP

- **Speed grade.** The vendor picks one of eleven per-OPP voltages from a
  PVTM measurement taken at runtime. That is not reimplemented, so each
  point carries the highest voltage the vendor lists for its bin - up to
  50 mV richer than a fast part needs at 700 MHz.
- **Cold voltage.** The vendor floors the rail at 750 mV below 15 C and
  releases it above. That floor is held unconditionally instead, so 300-600
  MHz all run at 750 mV. Those points are only reached under thermal
  throttling or with `dvfs_boost` cleared, since the rate is otherwise at
  maximum while powered and parked at 200 MHz when not.
- **950 MHz.** Unreachable: BL31 owns the PVTPLL and its rate table has no
  950 MHz entry, so it cannot be asked for even though the datasheet allows
  it.
- **Thermal model.** `step_wise` against a passive trip rather than the
  vendor's IPA power model: the DT sets no `dynamic-power-coefficient`, so
  there is nothing for `rockchip_ipa_power_model_init()` to work from. The
  vendor's system-monitor hooks, which adjust voltage with temperature, are
  not reimplemented either.
- **Load-based scaling.** The vendor's `rknpu_ondemand` governor measures
  nothing: its `get_dev_status()` returns without filling in a sample, and the
  governor hands back a frequency written through debugfs, so the 6.1 BSP runs
  the NPU at whatever rate it was left at. Here the rate is raised on power
  acquisition and dropped when the device suspends.
- **`rknn_server`.** Not packaged; RKNN-toolkit remote profiling is
  unavailable.

## GPU

panfrost drives the Mali-G52 through the mainline OPP core at 300-900 MHz.
The rate comes from BL31's PVTPLL over SCMI rather than the CRU, because the
CRU dividers off GPLL/CPLL/AUPLL/SPLL/LPLL cannot produce the upper rates.

- **Three clocks.** SCMI `CLK_GPU` carries the rate, `PCLK_GPU_ROOT` and CRU
  `CLK_GPU` reach the registers BL31 programs PVTPLL through. BL31 does not
  enable the latter two itself, and nothing else claims the gate, so without
  naming it here the clock framework disables it as unused during boot.
- **Rate accuracy — the OPP rates are nominal.** Over the CRU the top three
  OPPs all collapse onto 786 MHz. Over SCMI, BL31 accepts and reports back
  every rate exactly; the clock carries `CLK_GET_RATE_NOCACHE`, so that is a
  real `CLOCK_RATE_GET` round trip and not a cached value. It still is not
  what the shader cores run at. Panfrost's GPU cycle counter, read through
  fdinfo with `profiling=1`, gives that:

  | Requested | BL31 reports | Cycle counter |
  | --- | --- | --- |
  | 300 MHz | 300 MHz | 430 MHz |
  | 400 MHz | 400 MHz | 510 MHz |
  | 500 MHz | 500 MHz | 646 MHz |
  | 600 MHz | 600 MHz | 772 MHz |
  | 700 MHz | 700 MHz | 802 MHz |
  | 800 MHz | 800 MHz | 802 MHz |
  | 900 MHz | 900 MHz | 821 MHz |

  Frame rate divided by the counter's rate is constant to within 2% across
  the range, so throughput follows the real clock exactly and the ceiling is
  ~821 MHz rather than 900. 700 and 800 MHz land on the same rate, so the
  achievable set is quantised, and it is stable rather than drifting: pinned
  at 900 MHz from 53.6 to 57.3 C the measured rate moved 0.05%. The vendor
  calibrates this PVTPLL at 800 MHz and 750 mV, which is where the
  achievable points cluster.

  The mechanism is in BL31. TF-A carries RK3576 upstream, and its GPU table
  (`plat/rockchip/rk3576/scmi/rk3576_clk.c`) maps each rate to a PVTPLL ring
  oscillator length:

  | Requested | Ring length | Delivered here |
  | --- | --- | --- |
  | 900 MHz | 20 | 822 MHz |
  | 800 MHz | 21 | 803 MHz |
  | 700 MHz | 21 | 803 MHz |
  | 600 MHz | 23 | 775 MHz |
  | 500 MHz | 32 | 650 MHz |
  | 400 MHz | 48 | 513 MHz |
  | 300 MHz | 63 | 433 MHz |
  | 200 MHz | 0 | GPLL/6 = 198 MHz |

  `clk_gpu_set_rate()` writes the length, switches the mux and returns; it
  never reads back what the ring produced. The rate names are therefore
  labels on a ring length, and what that length oscillates at is a property
  of the individual die. PVTPLL is closed-loop against voltage and
  temperature - which is why the delivered rate here moves 0.1% for 50 mV
  and 0.05% across a 20 C swing - so these figures are stable, but they are
  stable *per chip* and should not be assumed for another board.

  The boot chain runs rkbin's BL31, not a TF-A build, so that source is
  evidence about the firmware's design rather than the binary in use. It
  holds up as a prediction: a ring oscillator's period should be linear in
  its stage count, and fitting the measured rates against those lengths
  gives `period = 0.0256*N + 0.710 ns` with a worst-case error of 0.71%
  across all six points. 700 and 800 MHz share length 21 and measure
  identically, which is what put the table on the trail to begin with.
  Reaching 900 MHz on this die would need a ring of about 16; the shortest
  the table offers is 20.

  The ring length BL31 programs can be read back with a debugfs reader
  (not carried in this series; see the investigation notes). Each OPP under
  load, rail sampled alongside:

  | Requested | Ring length | PVTPLL `cnt_avg` | Cycle counter | Rail |
  | --- | --- | --- | --- | --- |
  | 300 MHz | 63 | 430 MHz | 429.7 MHz | 700 mV |
  | 400 MHz | 48 | 510 MHz | 509.6 MHz | 700 mV |
  | 500 MHz | 32 | 645 MHz | 645.4 MHz | 700 mV |
  | 600 MHz | 23 | 772 MHz | 772.3 MHz | 700 mV |
  | 700 MHz | 21 | 802 MHz | 801.6 MHz | 725 mV |
  | 800 MHz | 21 | 801 MHz | 801.5 MHz | 775 mV |
  | 900 MHz | 20 | 820 MHz | 820.6 MHz | 825 mV |

  The lengths are the ones upstream TF-A lists, so the shipped rkbin BL31
  carries the same table. `cnt_avg` is the PVTPLL's own averaged
  measurement - the register the vendor kernel reads for this - and it
  agrees with panfrost's GPU cycle counter within 1 MHz at every point.

  Nothing in the integration is at fault: the rail tracks each OPP and BL31
  programs the length the table specifies. The table itself is the problem,
  and it is inconsistent on its own terms. 700 and 800 MHz produce byte
  identical programming - `GCK_LEN` reads 0x54 for both - so they are one
  operating point separated only by 50 mV of rail, which moves the ring
  0.13 MHz. Both entries cannot be right.

  Reading the whole block shows why that is hard to explain as ordinary part
  variation. Of the registers in it, BL31 writes only `GCK_LEN`,
  `GCK_CAL_CNT` and `GCK_CFG`. `RING_EN`, `RING0`-`RING3_LENGTH`, `GCK_DIV`,
  `GCK_REF_VAL`, `GCK_CFG_VAL`, `GCK_THR`, `GFREE_CON` and `ADC_CFG` are
  defined in the upstream source and never written by any path, and all of
  them read zero here. `GCK_DIV` at zero rules out a divider taking the
  difference, but `GCK_REF_VAL` and `GCK_THR` - the reference and threshold
  a closed loop would work against - are simply unprogrammed.

  So length 20 measures 821 MHz on this die, at 825 mV and equally at the
  vendor's 875 mV, and the block's own counter agrees with the GPU cycle
  counter. Whether that is this die, or a calibration step the open
  implementation never performs, is not established here. Settling it needs
  a second RK3576 board running Rockchip's own image for comparison.

  Three consequences. 900 MHz is not reachable: length 20 is the shortest
  ring in the table, and it delivers 821 MHz even at 875 mV, the vendor's
  own 900 MHz voltage. Against the CRU's 786 MHz the SCMI path is worth
  4.5%, not the 14.5% the OPP numbers imply. And the 700 MHz OPP is not a
  separate operating point at all - same ring length as 800 MHz, same 801
  MHz delivered, 62.5 mV lower. It is the same silicon behaviour at less
  than the voltage Rockchip qualifies for that rate.

- **Runtime PM.** A rate request only reaches PVTPLL with the power domain
  up, so the clock is parked at 200 MHz through the OPP core before suspend
  and the requested rate reapplied on resume. Requests arriving while the
  domain is down are recorded and applied on the way back up.
- **Per-chip selection.** An OTP cell picks the OPP set: 900 MHz on the
  RK3576, 800 MHz on the S, J and M parts, with the J and M carrying their
  own voltages. Unreadable OTP falls back to a restricted table.
- **950 MHz.** Dropped. BL31's rate table has no 950 MHz entry and table 3-2
  of the datasheet (rev 1.5) gives 900 MHz as the GPU maximum.

### Benchmarks

`glmark2-es2-drm` off-screen through GBM, `--frame-end=finish`, 1920x1080,
`userspace` governor pinned at each rate. Scene is a fragment-bound loop
(`fragment-steps=256`):

| Frequency | FPS | vs 300 MHz |
| --- | --- | --- |
| 300 MHz | 20.0 | 1.00x |
| 400 MHz | 24.0 | 1.20x |
| 500 MHz | 30.0 | 1.50x |
| 600 MHz | 36.0 | 1.80x |
| 700 MHz | 37.0 | 1.85x |
| 800 MHz | 37.0 | 1.85x |
| 900 MHz | 38.0 | 1.90x |

Throughput tracks the requested rate to 600 MHz and then flattens. Seven
scenes covering fragment, ALU, vertex, texture and two real workloads all
return 1.02-1.06x from 600 to 900 MHz. That is not a workload ceiling: the
cycle counter shows the real clock saturating near 820 MHz, and frame rate
per actual cycle is constant, so the GPU is doing the same work per cycle
throughout and simply stops receiving more cycles. Against what the CRU
delivers the measured gain is 3.8% at the 700 MHz OPP and 2.7% at the top.

Sustained load at 900 MHz holds 38.0 FPS with no falloff and peaks at
82 C against a 115 C critical trip, unchanged with eight CPU workers
alongside. `stress-ng --gpu 8` completes 153 bogo ops/s, 132 with
`--cpu 8` added, and 200 frequency changes during a run produce no errors.

## Debug UART

40-pin header: pin 8 (TX), pin 10 (RX), pin 6 (GND), 1500000 8N1,
3.3 V TTL.
