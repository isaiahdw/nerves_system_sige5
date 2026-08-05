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

At that reference condition - ring length 21, 750 mV - this die reads 802.

> **Correction.** The grade below was derived from a table of eleven grades
> whose `785 804 5` bucket would make 802 an L5 part. That table does not
> govern the RK3576 GPU. Read from a running vendor kernel, `gpu-opp-table`
> defines five grades and its buckets are `0-800 L0, 801-820 L1, 821-840 L2,
> 841-860 L3, 861+ L4`, which puts 802 at **L1**. The measurement is unaffected;
> only the grade changes. The vendor defines no L1 override at 900 MHz, so the
> base 825000 applies to this die - the same voltage the paragraph below reaches,
> for a different reason. See `rk3576-vendor-opp-tables.md`.

Superseded reading: this die reads 802, which is grade **L5**. The BSP's L5
voltage for the 900 MHz OPP is `opp-microvolt-L5 = <825000 825000 875000>`,
which is exactly what mainline's DTS supplies. So 825 mV at 900 MHz is the
correct grade voltage for this particular die, arrived at by coincidence rather
than by measurement.

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

### The OTP is unprogrammed too, and BL31 wanted it

Rockchip's own firmware changelog (`doc/release/RK3576_EN.md` in rkbin) records
this against **BL31 v1.05**, 2024-04-24:

```
2. Add otp init.
3. Increase pvtpll length for middle frequencies.
4. Adjust pvtpll table by otp opp info.
```

So the PVTPLL rate table is not a fixed constant: BL31 **adjusts it per die from
the OTP `opp-info` cells**. The vendor device tree names those cells -
`cpub-opp-info@30`, `cpul-opp-info@36`, `npu-opp-info@42`, `gpu-opp-info@48`,
`logic-opp-info@4e`, six bytes each.

Read from this board, every one of them is **zero**. The same OTP makes the
vendor kernel log `Failed to get leakage` on all four domains.

That gives a coherent account of the measurements in this document: the
mechanism meant to align delivered rates with their labels has no data to work
from on this die, so BL31 falls back to an unadjusted default table and the
rates land where they land (300 -> 430, 600 -> 772, 900 -> 821).

Treat this as a **hypothesis, not a result**. BL31's table cannot be read back,
only one board was available, and unprogrammed `opp-info` may simply be normal
for this part or production run. What it does establish is that the delivered
rates measured here may be specific to a die with blank `opp-info`, and a
properly fused part could track its labels more closely.

Note the scope: this affects only the SCMI path. The CRU collapse (700 -> 594,
800/900/950 -> 786) is divider arithmetic, independent of OTP, and was
reproduced on the vendor BSP kernel at 786.4 MHz.

The GPU cycle counter this document relies on for measurement is itself a BL31
feature, enabled in v1.02 - well before the v1.24 in use here.


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
The DTS now carries the highest voltage the vendor lists for each rate -
712.5 mV to 600, 750 at 700, 812.5 at 800 and 875 at 900 - so it covers the
slowest silicon rather than only this die. It previously carried 825 mV at
900 MHz, which is the L5 value and happened to suit this part. Implementing
the grade selection would recover the efficiency that costs on fast silicon;
it would not change the delivered rate.

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

## Upstream TF-A gives the same clocks as Rockchip's BL31

Every measurement above was taken with Rockchip's BL31 owning the PVTPLL. The
`USE_OPENSOURCE_TEE=1` build replaces it with upstream TF-A, which is a
different implementation of the same job, so none of it carried over on its
own.

It does carry over. Two lines of evidence.

### The tables are the same

`plat/rockchip/rk3576/scmi/rk3576_clk.c` in upstream TF-A holds
`rk3576_gpu_pvtpll_table`, and Rockchip's BL31 holds the same table as data -
`struct pvtpll_table` is 28 bytes (`rate`, `length`, `length_frac`,
`length_low`, `length_low_frac`, `ring_sel`, `volt_sel_thr`), so it can be
scanned straight out of the ELF. Both v1.20 and v1.25 agree with upstream at
every rate:

| rate | Rockchip BL31 | upstream TF-A |
| --- | --- | --- |
| 900 | 20 (`length_low` 19) | 20 |
| 800 | 21 | 21 |
| 700 | 21 | 21 |
| 600 | 23 | 23 |
| 500 | 32 | 32 |
| 400 | 48 | 48 |
| 300 | 63 | 63 |

The only difference is `length_low = 19` at 900 MHz, and it is unreachable
here: `clk_scmi_gpu_set_rate()` only consults it when `OPP_LENGTH_LOW`, which
is `BIT(2)` of the requested rate, is set. Every rate in the OPP table is a
multiple of 8, so bit 2 is always clear.

### The hardware agrees

Same board, same script, only the bootloader at sector 64 changed. Measured
with panfrost fdinfo, `delta(drm-cycles-fragment) / delta(drm-engine-fragment)`:

| target | Rockchip BL31 | upstream TF-A | again, upstream |
| --- | --- | --- | --- |
| 300 | 422.8 | 422.8 | 423.7 |
| 400 | 502.7 | 506.5 | 503.7 |
| 500 | 638.9 | 642.9 | 639.9 |
| 600 | 765.0 | 769.0 | 766.2 |
| 700 | 794.9 | 798.4 | 796.1 |
| 800 | 794.9 | 797.9 | 796.0 |
| 900 | 813.7 | 816.2 | 814.7 |

The third column is a repeat on the same firmware, and it is there because the
second column alone looked like a consistent `+3` to `+4 MHz` shift. Repeating
gives a run-to-run spread of up to 3 MHz, which is the same size, and the
repeat lands about 1 MHz from the Rockchip run rather than 4. So the shift is
measurement spread, not the firmware.

Note 700 and 800 measuring the same, on both firmwares, to within 0.1 MHz.
That is the tables' most distinctive prediction - they share ring length 21 -
and it is a fingerprint rather than a coincidence.

### What this does not excuse

The labels are still wrong, and the same on both firmwares. Requesting 300 MHz
yields about 423, and 900 yields about 815. The usable span is roughly 423 to
816 MHz across seven OPPs, two of which are the same operating point. That is
the subject of the open question about what the OPP table should say; swapping
the firmware neither caused it nor fixed it.

## Why the labels are wrong, and why `length_low` exists

### `length_low` is a low-*temperature* entry

`struct pvtpll_table` carries `length_low`/`length_low_frac`, and Rockchip's
BL31 sets `length_low = 19` on the GPU's 900 MHz row while upstream TF-A leaves
it zero. The vendor DT binding names the field:

    rockchip,pvtpll-table: ... each item consists frequency and pvtpll config
    like <freq_khz ring length low_temp_ring low_temp_length>

So "low" is temperature, not voltage. Cold silicon switches faster, so holding a
frequency target needs a different ring length, and the vendor stack carries a
second one for it - alongside `rockchip,pvtm-ref-temp`, `pvtm-temp-prop` and
`pvtm-thermal-zone`, which convert a PVTM reading at the current temperature
back to the reference temperature.

Reaching it means setting a length field in the requested rate:
`OPP_LENGTH_MASK` is `GENMASK_32(5, 2)`, four bits at bit 2, of which
`OPP_LENGTH_LOW` is the lowest value. `clk_scmi_gpu_set_rate()` treats such a
request as a mode switch - it swaps the table to the low lengths and returns
without setting any rate.

Nothing here does that. Every rate in the OPP table is a multiple of 64, so bits
2 through 5 are clear, and mainline has no temperature-triggered switch to send
it. The entry is unreachable, and its absence upstream costs nothing that is
reachable either.

### The frequencies are adaptive, and the floor holds them up

PVTPLL is not a PLL asked for a frequency. The ring length sets a delay target
and the delivered clock is whatever the silicon manages at the current voltage
and temperature, so the OPP rate is nominal. Measured with voltage alongside:

| target | vdd_gpu | temp | measured | MHz/mV |
| --- | --- | --- | --- | --- |
| 300 | 700 mV | 55 C | 422.7 | 0.604 |
| 400 | 700 mV | 56 C | 502.6 | 0.718 |
| 500 | 700 mV | 57 C | 638.8 | 0.913 |
| 600 | 700 mV | 59 C | 765.0 | 1.093 |
| 700 | 725 mV | 60 C | 795.0 | 1.097 |
| 800 | 775 mV | 60 C | 794.9 | 1.026 |
| 900 | 825 mV | 60 C | 813.7 | 0.986 |

The first four rows share one voltage - 700 mV is the floor in the DT for
everything at or below 600 MHz - which is why they all overshoot their label.
At a fixed 700 mV the ring simply runs at whatever that length gives, and 300
MHz worth of ring length yields 423 MHz. They are still real DVFS points, since
dynamic power scales with frequency at constant voltage, but the numbers on them
are fiction.

### 800 MHz is strictly worse than 700

700 and 800 share ring length 21, so they are the same operating point - 795.0
against 794.9 MHz. The DT gives 800 MHz 775 mV against 700 MHz's 725 mV. Fifty
millivolts for nothing, and leakage and dynamic power both rise with it.

900 MHz is a weaker version of the same: 813.7 MHz for 825 mV, 2.4 percent more
performance than 700 MHz for 100 mV more.

Efficiency peaks at 600-700 MHz and falls off above it - the MHz/mV column is
monotonic up to 700 and drops after. Whatever the OPP table ends up saying, 800
MHz should not be in it.
