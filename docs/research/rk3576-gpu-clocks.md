# RK3576 GPU clocking

How the Mali-G52's clock actually works on this SoC, what it delivers, and what
this firmware does about it.

## An OPP rate is an operating point, not a frequency

This is the whole thing, and everything below follows from it.

Above roughly 600 MHz the GPU is not clocked by a PLL with a divider. It is
clocked by a PVTPLL - a ring oscillator whose length sets a *delay target*. BL31
owns it, and a rate request selects a `(ring length, voltage)` pair from a table.
What frequency comes out depends on that pair, the die, and its temperature.

Rockchip labels each pair with what it is nominally worth. Linux's OPP framework
reads that label as a real frequency. They are not the same thing, and the gap is
not small.

`clk_scmi_gpu_get_rate()` returns the requested rate, so `cur_freq` in devfreq is
an echo, not a measurement. Nothing in the clock path reads back what the ring
produced.

## The table

`plat/rockchip/rk3576/scmi/rk3576_clk.c`:

| rate | ring length |
| --- | --- |
| 900 MHz | 20 |
| 800 MHz | 21 |
| 700 MHz | 21 |
| 600 MHz | 23 |
| 500 MHz | 32 |
| 400 MHz | 48 |
| 300 MHz | 63 |
| 200 MHz | 0 |

The lookup is an exact match and returns `NULL` otherwise - no rounding,
clamping or interpolation. Length zero means "not PVTPLL": that path divides
GPLL, `div = DIV_ROUND_UP(1188 MHz, rate)`, so the 200 MHz park is really
1188/6 = 198 MHz.

Rockchip's prebuilt BL31 carries the same table. It is data in the ELF -
`struct pvtpll_table` is 28 bytes - and v1.20 and v1.25 both match upstream at
every rate. The one difference is `length_low = 19` on the 900 MHz row, which
upstream leaves zero.

`length_low` is a low-**temperature** ring length. The vendor DT binding gives
the tuple as `<freq_khz ring length low_temp_ring low_temp_length>`, alongside
`rockchip,pvtm-ref-temp`, `pvtm-temp-prop` and `pvtm-thermal-zone`. Reaching it
means putting a length in the requested rate - `OPP_LENGTH_MASK` is four bits at
bit 2 - and such a request is a mode switch that swaps the table and returns
without setting a rate. Every rate in the OPP table is a multiple of 64, so bits
2 to 5 are clear, and mainline has no temperature-triggered switch to send. It
is unreachable here, and upstream leaving it zero costs nothing reachable.

## What it delivers

Measured through panfrost's cycle counters, which the GPU increments itself:

```
MHz = delta(drm-cycles-fragment) / delta(drm-engine-fragment ns) * 1000
```

Enable `/sys/devices/platform/soc/27800000.gpu/profiling`, pin the governor to
`userspace`, run a fragment-bound load, and sample `/proc/<pid>/fdinfo/<fd>`
twice.

Board 2, with the GPU supply alongside:

| target | vdd_gpu | measured | MHz/mV |
| --- | --- | --- | --- |
| 300 | 700 mV | 422.7 | 0.604 |
| 400 | 700 mV | 502.6 | 0.718 |
| 500 | 700 mV | 638.8 | 0.913 |
| 600 | 700 mV | 765.0 | 1.093 |
| 700 | 725 mV | 795.0 | 1.097 |
| 800 | 775 mV | 794.9 | 1.026 |
| 900 | 825 mV | 813.7 | 0.986 |

Board 1, same bin, reads 430 / 510 / 646 / 772 / 802 / 802 / 821 - the same
shape a few MHz apart.

Three consequences:

**The low OPPs overshoot because they share one voltage.** 700 mV is the DT
floor at and below 600 MHz, and at fixed voltage the ring delivers whatever its
length gives. Requesting 300 MHz yields 423. They remain real DVFS points, since
dynamic power scales with frequency at constant voltage, but the numbers on them
are fiction.

**700 and 800 MHz are the same operating point on this silicon.** They share
ring length 21 and differ only in voltage, which is how the vendor gets more
frequency out of the same delay target on a die where 725 mV cannot sustain it.
Here it can, so both measure 795 MHz and the extra 50 mV buys nothing.

**900 MHz is not reached.** Ring length 20 at 825 mV and 60 C gives 814 MHz, and
the table has no shorter ring to offer.

## Why the labels are not adjusted per die

BL31 is supposed to adjust the table for the individual part. Rockchip's
changelog records it against BL31 v1.05:

```
2. Add otp init.
3. Increase pvtpll length for middle frequencies.
4. Adjust pvtpll table by otp opp info.
```

The vendor device tree names the cells that feed it - `gpu-opp-info@48`,
`cpub-opp-info@30`, `cpul-opp-info@36`, `npu-opp-info@42`, `logic-opp-info@4e`,
six bytes each. **Every one reads zero on these boards**, and the same OTP makes
the vendor kernel log `Failed to get leakage` on all four domains.

So the mechanism meant to align delivered rates with their labels has no data to
work from, and BL31 falls back to an unadjusted default table. Delivered rates
measured here may be specific to dies with blank `opp-info`; a fused part could
track its labels more closely.

## Firmware makes no difference

The same numbers come out of upstream TF-A as out of Rockchip's BL31. Measured
on one board with only the bootloader at sector 64 changed:

| target | Rockchip BL31 | upstream TF-A | upstream, repeated |
| --- | --- | --- | --- |
| 300 | 422.8 | 422.8 | 423.7 |
| 400 | 502.7 | 506.5 | 503.7 |
| 500 | 638.9 | 642.9 | 639.9 |
| 600 | 765.0 | 769.0 | 766.2 |
| 700 | 794.9 | 798.4 | 796.1 |
| 800 | 794.9 | 797.9 | 796.0 |
| 900 | 813.7 | 816.2 | 814.7 |

The third column is a repeat on the same firmware. Run-to-run spread is up to
3 MHz, the same size as the apparent firmware difference, and the repeat lands
about 1 MHz from the Rockchip run. Nothing here is firmware-conditional.

## What the CRU path does instead

Without SCMI, panfrost drives `CLK_GPU` off the CRU, whose dividers cannot
produce the upper OPPs: 700 MHz runs at 594, and 800, 900 and 950 all collapse
onto 786. That is divider arithmetic, independent of the OTP, and reproduces on
the vendor BSP kernel at 786.4 MHz. Against it the SCMI path is +4.3% at the top
and 29-45% at the intermediate OPPs; the 14.5% the labels imply was never real.

## Throughput and thermals

`glmark2-es2-drm` off-screen through GBM at 1920x1080, fragment-bound,
`userspace` pinned:

| target | FPS | vs 300 MHz |
| --- | --- | --- |
| 300 | 20.0 | 1.00x |
| 400 | 24.0 | 1.20x |
| 500 | 30.0 | 1.50x |
| 600 | 36.0 | 1.80x |
| 700 | 37.0 | 1.85x |
| 800 | 37.0 | 1.85x |
| 900 | 38.0 | 1.90x |

Frame rate divided by the *delivered* clock is constant within 2% across the
range, so throughput follows the real rate exactly and nothing saturates except
the clock. Sustained load at 900 MHz holds 38.0 FPS with no falloff, peaking at
82 C against a 115 C critical trip, unchanged with eight CPU workers alongside.
The clock is stable rather than drifting: from 53.6 to 57.3 C the measured rate
moved 0.05%. 200 frequency changes during a run produce no errors.

## Device tree decisions

- **950 MHz is removed.** It does not exist in the firmware table, and the
  lookup is exact, so the request returns `SCMI_INVALID_PARAMETERS`. On the CRU
  path it silently delivered 786 MHz.
- **700 MHz stays.** It shares a ring length with 800 MHz but is a distinct
  operating point at a lower voltage, which is the pairing the vendor ships.
- **800 MHz stays, despite being dominated here.** Same frequency as 700 for
  50 mV more on both boards measured - but both are the same bin (`21 01` at OTP
  +0x24; an RK3576S smt1019 reads `23 01`), and the table has to be safe for
  slower dies where the extra voltage is what puts 800 above 700. Deciding this
  needs a die from a different bin.
- **Measured rates are not written into the DTS.** They are stable per die, not
  per SoC, and `rk3576.dtsi` is shared by every RK3576 board.
- **Voltages are the vendor's worst case**, not this die's: 712.5 mV to 600 MHz,
  750 at 700, 812.5 at 800, 875 at 900. That covers the slowest silicon rather
  than the part in hand.

The energy model and thermal governor still budget against the labels, so they
are working from frequencies the hardware does not run.

## Not implemented

The port does not reproduce the BSP's per-die voltage qualification. The BSP
measures the oscillator at a reference point, derives a grade L0-L10 and selects
the matching `opp-microvolt-Lx` for every OPP. This carries one fixed voltage per
OPP and never measures the grade. Implementing it would recover the efficiency
that costs on fast silicon; it would not change the delivered rate.

## Measuring it again

Add `/sys/kernel/debug/dri/27800000.gpu/pvtpll` to dump the block, refusing
unless the GPU is already awake - `pm_runtime_get_if_in_use()` - because
touching it with the power domain down hangs the CPU. Read it while a load runs,
and poll, because the guard also refuses during a runtime-PM transition. Release
the reference and the mapping on the success path, and gate it on the RK3576
compatible.

Of the registers TF-A defines, `clk_gpu_set_rate()` writes only `GCK_LEN`,
`GCK_CAL_CNT` and `GCK_CFG`. `RING_EN`, `RING0..RING3_LENGTH`, `GCK_DIV`,
`GCK_REF_VAL`, `GCK_CFG_VAL`, `GCK_THR`, `GFREE_CON` and `ADC_CFG` are written by
no code path and read zero. `GCK_DIV` at zero rules out a hidden divider.
`VERSION` reads `0x20230710`. The ring lengths read back from hardware are
exactly the table's.
