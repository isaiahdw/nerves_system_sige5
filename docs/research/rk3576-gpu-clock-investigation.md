# RK3576 GPU clock: what the OPP rates actually deliver

The GPU's OPP frequencies are nominal. On the SCMI path they name a PVTPLL
ring length rather than a rate, and the rate that ring produces on this die
does not match the label at any point. This records what was measured, how,
what was excluded, and what is still open.

## Summary

- The rate labels are wrong on the SCMI path. A 300 MHz OPP runs at 430 MHz;
  a 900 MHz OPP runs at 821 MHz.
- 700 MHz and 800 MHz share a ring length and both measure 802 MHz. They are
  duplicate clock configurations but distinct OPPs, since the vendor pairs
  that one length with two voltages deliberately.
- The 802 MHz reading is the BSP's own silicon speed score. It puts this die
  at voltage grade L5, whose 900 MHz voltage is 825 mV - the value mainline
  already ships, by coincidence rather than measurement.
- Peak GPU is 821 MHz over SCMI against 787 MHz over the CRU, a real +4.3%.
  The intermediate OPPs run 29-45% faster than the CRU path.
- Linux requests the intended nominal OPP, supplies the programmed rail
  voltage, and BL31 writes the table's corresponding ring length. The
  measured-rate discrepancy originates below that interface. That is not the
  same as the series being correct - see "What Linux is missing".
- 950 MHz, which mainline's DTS lists, does not exist in the firmware rate
  table at all.

## Measuring it

Every rate source Linux offers is a report of what was *asked for*:

| Source | Why it cannot be used |
| --- | --- |
| `devfreq/cur_freq` | panfrost has no `get_cur_freq`, so this is `previous_freq` |
| `clk_get_rate()`, `clk_summary` | The clock carries `CLK_GET_RATE_NOCACHE`, so the read is a real `CLOCK_RATE_GET` round trip - but BL31's `clk_scmi_gpu_get_rate()` returns `sys_clk_info.gpu_rate`, the value saved by the last `set_rate`. Even its GPLL branch carries the comment "Make the return rate is equal to the set rate" |
| SCMI rate readback | Same call, same stored value |

Two sources do measure hardware:

**Panfrost's GPU cycle counter.** Enable
`/sys/bus/platform/devices/27800000.gpu/profiling`, then per DRM fd take
`delta(drm-cycles-fragment) * 1e9 / delta(drm-engine-fragment)` from
`/proc/<pid>/fdinfo/<fd>`. `GPU_CYCLE_COUNT_LO/HI` is a hardware register in
the GPU's own clock domain; both accumulators cover the same window, so
interrupt latency inflates numerator and denominator alike and the ratio
holds.

**The PVTPLL block's own counter.** `GCK_CNT_AVG` at offset 0x54 of the GPU
PVTPLL syscon reports an averaged measurement in MHz. This is the register
the vendor kernel reads in `rockchip_pvtpll_get_rate()`. It is not reachable
from userspace - `/dev/mem` returns `EFAULT` under `STRICT_DEVMEM` - so it
needs a debugfs reader in panfrost. One was used for this investigation and
deliberately not carried in the series: it leaked a runtime-PM reference per
read, and being registered on the common panfrost debugfs list it would have
mapped an RK3576 physical address on every other panfrost SoC. Anything
revived from it needs both fixed, plus the block obtained from a described
resource rather than a hardcoded address.

Three traps cost time here:

- glmark2 opens more than one DRM fd and only one carries the counters. Pick
  the one with the largest `drm-cycles-fragment`.
- The counters read zero unless `profiling` was set to 1 first.
- Any register or voltage read taken while the GPU is idle shows the 200 MHz
  park, not the OPP. Everything must be sampled under load.

The cycle counter was calibrated against the CRU path, where rates derive
from real divider registers rather than a firmware claim:

| Requested | CRU registers | Cycle counter | Ratio |
| --- | --- | --- | --- |
| 300 MHz | 297.0 | 299.0 | 1.007 |
| 500 MHz | 500.0 | 502.5 | 1.005 |
| 700 MHz | 594.0 | 595.0 | 1.002 |
| 800 MHz | 786.4 | 787.5 | 1.001 |
| 900 MHz | 786.4 | 787.4 | 1.001 |
| 950 MHz | 786.4 | 787.2 | 1.001 |

Within 0.7%, worst at low rates where a job's fixed interrupt latency is the
largest share. The CRU numbers are themselves integer divisions of the PLLs:
297 = GPLL/4, 594 = GPLL/2, 500 = CPLL/2, 786.432 = AUPLL undivided, which is
why 800, 900 and 950 all collapse onto one rate there.

## What the SCMI path delivers

All values sampled in one window under a fragment-bound load:

| Requested | Ring length | Cycle counter | `GCK_CNT_AVG` | Rail |
| --- | --- | --- | --- | --- |
| 300 MHz | 63 | 429.7 MHz | 430 MHz | 700 mV |
| 400 MHz | 48 | 509.6 MHz | 510 MHz | 700 mV |
| 500 MHz | 32 | 645.4 MHz | 645 MHz | 700 mV |
| 600 MHz | 23 | 772.3 MHz | 772 MHz | 700 mV |
| 700 MHz | 21 | 801.6 MHz | 802 MHz | 725 mV |
| 800 MHz | 21 | 801.5 MHz | 801 MHz | 775 mV |
| 900 MHz | 20 | 820.6 MHz | 820 MHz | 825 mV |

Two independent instruments, one of them Rockchip's own, agree within 1 MHz
at every point.

## The mechanism

RK3576 is upstream in TF-A, so the firmware's design is readable even though
the boot chain runs rkbin's prebuilt BL31.
`plat/rockchip/rk3576/scmi/rk3576_clk.c` maps each rate to a PVTPLL ring
oscillator length:

```c
static struct pvtpll_table rk3576_gpu_pvtpll_table[] = {
	ROCKCHIP_PVTPLL(900000000, 0, 20, 0),
	ROCKCHIP_PVTPLL(800000000, 0, 21, 0),
	ROCKCHIP_PVTPLL(700000000, 0, 21, 0),
	ROCKCHIP_PVTPLL(600000000, 0, 23, 0),
	ROCKCHIP_PVTPLL(500000000, 0, 32, 0),
	ROCKCHIP_PVTPLL(400000000, 0, 48, 0),
	ROCKCHIP_PVTPLL(300000000, 0, 63, 0),
	ROCKCHIP_PVTPLL(200000000, 0, 0, 0),
	{ /* sentinel */ },
};
```

The lookup is an exact match and returns `NULL` otherwise, so there is no
rounding, clamping or interpolation. `clk_gpu_set_rate()` writes the length,
arms the calibration counter, enables and starts the PVTPLL, switches two
muxes, and returns. It never reads back what the ring produced.

The length-zero entry means "not PVTPLL": that path divides GPLL instead,
`div = DIV_ROUND_UP(1188 MHz, rate)`, so the 200 MHz park is really
1188/6 = 198 MHz.

The lengths read back from hardware are exactly the table's - 63, 48, 32, 23,
21, 21, 20 - so the shipped rkbin BL31 carries this table.

## What was excluded

Each of these was a working hypothesis, tested and dropped:

| Hypothesis | Evidence against |
| --- | --- |
| DDR or fabric backpressure | Cycles per frame is constant at 21.1-21.3M across the range. A memory wall would inflate it, since the GPU would burn idle clock cycles stalling |
| Job submission starvation | Engine occupancy is 98%+ at every OPP |
| Workload not GPU-bound | Seven scenes - fragment, pure ALU, vertex-heavy, texture, shading, terrain, refract - all return 1.02-1.06x from a 600 to a 900 MHz request |
| Temperature | Pinned at 900 MHz across a 53.6-57.3 C soak the rate moved 821.0 to 820.6 MHz, 0.05% |
| Voltage | 62.5 mV between the 700 and 800 MHz OPPs, which share a ring length, changes the rate 0.13 MHz. 50 mV at length 20 changes it 0.9 MHz. Adopting the vendor's voltages throughout (712.5/750/812.5/875 mV) moved every point 0.1-0.7% |
| Regulator stuck or unconfigured | The rail tracks each OPP under load: 700, 725, 775, 825 mV on mainline values, 712.5, 750, 812.5, 875 on the vendor's |
| BL31 not programming the length | Read back from `GCK_LEN`, exact match to the table at all seven rates |
| A divider taking the difference | `GCK_DIV` reads 0 |
| Measurement error | The GPU cycle counter and the PVTPLL's own counter agree within 1 MHz; the cycle counter tracks CRU divider registers within 0.7% |

The output moved very little over the voltage and temperature ranges tested.
Whether that is an internal correction loop, some other mechanism, or simply
a stable oscillator is not established here.

## What the 800 MHz reading actually means

`GCK_CNT_AVG` is not only a diagnostic counter. The BSP uses it as the
silicon speed score: it requests 800 MHz at 750 mV, reads offset 0x54, and
maps the result onto eleven voltage grades.

```
rockchip,pvtm-freq   = <800000>;      /* measure at an 800 MHz request */
rockchip,pvtm-volt   = <750000>;      /* at 750 mV                     */
rockchip,pvtm-offset = <0x54>;        /* read GCK_CNT_AVG              */
        785     804     5             /* a score of 785-804 is grade L5 */
```

At that reference condition - ring length 21, 750 mV - this die reads 802,
which is grade **L5**. The BSP's L5 voltage for the 900 MHz OPP is
`opp-microvolt-L5 = <825000 825000 875000>`, which is exactly what mainline's
DTS supplies. So 825 mV at 900 MHz is the correct grade voltage for this
particular die, arrived at by coincidence rather than by measurement.

This reframes two earlier readings of the data:

- **700 and 800 MHz are not a contradiction.** They share a ring length, but
  an OPP is a frequency *and* a voltage, and the BSP knowingly pairs one
  length with two voltages. They are duplicate clock configurations, not
  duplicate operating points, and there is no basis here for calling either
  entry erroneous.
- **821 MHz at length 20 is plausibly expected for an L5 die**, not evidence
  of a calibration step that failed to run. The BSP measures the oscillator
  to choose a safe voltage; it does not force the output to equal the label.

## What is unprogrammed

Of the registers TF-A defines, `clk_gpu_set_rate()` writes only `GCK_LEN`,
`GCK_CAL_CNT` and `GCK_CFG`. These are defined upstream, written by no code
path, and read zero here:

```
RING_EN (0x00), RING0..RING3_LENGTH (0x04-0x10), GCK_DIV (0x28),
GCK_REF_VAL (0x30), GCK_CFG_VAL (0x34), GCK_THR (0x38),
GFREE_CON (0x3c), ADC_CFG (0x40)
```

`GCK_DIV` at zero rules out a hidden divider. The rest being zero shows only
that TF-A does not write them; without register documentation it says nothing
about what hardware paths exist. `VERSION` reads 0x20230710.

**Most of the block is never programmed.** Of the registers TF-A defines,
`clk_gpu_set_rate()` writes only `GCK_LEN`, `GCK_CAL_CNT` and `GCK_CFG`.
These are defined upstream, written by no code path, and read zero here:

```
RING_EN (0x00), RING0..RING3_LENGTH (0x04-0x10), GCK_DIV (0x28),
GCK_REF_VAL (0x30), GCK_CFG_VAL (0x34), GCK_THR (0x38),
GFREE_CON (0x3c), ADC_CFG (0x40)
```

`GCK_DIV` at zero rules out a hidden divider. `GCK_REF_VAL` and `GCK_THR` -
the reference and threshold a closed loop would work against - being
unprogrammed means whatever correction they configure is not happening.
`VERSION` reads 0x20230710.

## Not caused by the NPU work

The NPU series drives its own SCMI clock and PVTPLL, writes NPU_GRF, and
patches the shared pmdomain and rockchip-iommu drivers, so it is a fair
suspect. It is not the cause:

- No NPU patch touches a GPU register, the GPU clock or the GPU PVTPLL. The
  only occurrences of "gpu" are comments citing panfrost as precedent, and
  the read-margin patch writes NPU_GRF alone.
- Of the kernel-core patches, which no amount of unloading reverses, the
  pmdomain settle delay is 0 for the GPU domain - only `nputop`, `npu0` and
  `npu1` take 15 us - and the iommu patches contain no GPU reference at all.
- A build carrying the whole NPU series and none of the GPU series drives the
  GPU over the CRU and delivers exactly what its divider registers say:
  297, 500, 594 and 786 MHz, matching the cycle counter within 0.7%.
- `rmmod rknpu` leaves the ring length and `GCK_CNT_AVG` unchanged, 21/802
  and 20/820-821.

The discrepancy appears only when the GPU is driven from SCMI, which is where
the firmware's ring-length table is consulted.

Worth following up separately: `package/rknpu-driver/0011`'s own commit
message records that the NPU's SCMI clock reports a CRU-derived rate rather
than its PVTPLL rate. The NPU has its own PVTPLL block at 0x27270000 and its
own length table, so the NPU frequencies in this repo's benchmarks may carry
the same kind of label error. Reading that block would say whether the skew
is specific to the GPU table or systemic on this die.

## What performance this is worth

`glmark2-es2-drm` off-screen through GBM at 1920x1080, `--frame-end=finish`,
fragment-bound scene, `userspace` governor pinned:

| Requested | FPS | vs 300 MHz |
| --- | --- | --- |
| 300 MHz | 20.0 | 1.00x |
| 400 MHz | 24.0 | 1.20x |
| 500 MHz | 30.0 | 1.50x |
| 600 MHz | 36.0 | 1.80x |
| 700 MHz | 37.0 | 1.85x |
| 800 MHz | 37.0 | 1.85x |
| 900 MHz | 38.0 | 1.90x |

Frame rate divided by the *delivered* clock is constant within 2% across the
whole range, so throughput follows the real rate exactly and nothing is
saturating except the clock itself.

Against the CRU path, measured with the same instrument: 821 MHz against
787 MHz at the top, +4.3%, and 29-45% at the intermediate OPPs. The 14.5%
the OPP labels imply was never real.

Sustained load at 900 MHz holds 38.0 FPS with no falloff, peaking at 82 C
against a 115 C critical trip, unchanged with eight CPU workers alongside.
`stress-ng --gpu 8` passes 8/8 at 153 bogo ops/s, 132 with `--cpu 8` added.
200 frequency changes during a run produce no errors, against 5348
`dvfs failed` messages before the driver fixes in this series.

## Open

- Whether 821 MHz at length 20 is normal for an L5 die or evidence of
  something missing below Linux.
- Whether the ring-length table is characterised for production silicon.

The next test is not a second board. Run the 6.1 BSP on *this* board and
capture `pvtm=`, `pvtm-volt-sel=`, `GCK_LEN`, `GCK_CNT_AVG` and the exact
rkbin version. That isolates the Linux stack against a known-good one on
identical silicon and firmware. A second board changes silicon, firmware and
kernel at once.

## What Linux is missing

The port does not reproduce the BSP's per-die voltage qualification. The BSP
measures the oscillator at its reference point, derives a grade L0-L10, and
selects the matching `opp-microvolt-Lx` for every OPP. This series carries one
fixed voltage per OPP and never measures the grade.

Those fixed values are not the conservative choice they are documented to be.
At 900 MHz the DTS supplies 825 mV where the BSP fallback for base RK3576 is
875 mV; the 825 mV figure is the L5 value, which suits this die and is not
safe to assume for arbitrary silicon. Either the PVTM selection needs
implementing, or the DTS should carry the BSP fallback voltages.

## Consequences for the device tree

- **The 700 MHz OPP should stay for now.** It shares a ring length with
  800 MHz but is a distinct operating point at a lower voltage, which is the
  pairing the vendor ships. Removing it needs a same-board BSP comparison or
  characterisation across dies, neither of which has been done.
- **950 MHz is not merely unreachable, it does not exist.** The firmware
  rate table stops at 900 MHz and the lookup is exact, so a 950 MHz request
  returns `SCMI_INVALID_PARAMETERS`. On the CRU path it silently delivers
  786 MHz. It is dropped in this series.
- **Measured rates must not be written into the DTS.** They are stable per
  die, not per SoC, and `rk3576.dtsi` is shared by every RK3576 board.
- The energy model and thermal governor budget against the labels, so they
  are working from frequencies the hardware does not run.

## Reproducing

The reader adds `/sys/kernel/debug/dri/27800000.gpu/pvtpll`, dumping the
block and refusing unless the GPU is already awake -
`pm_runtime_get_if_in_use()` - because touching it with the power domain down
hangs the CPU. Read it while a load runs; poll, because the guard also
refuses during a runtime-PM transition. It must release the reference and the
mapping on the success path, and be gated on the RK3576 compatible.
