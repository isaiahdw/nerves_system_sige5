# RK3576 vendor OPP tables, read from a running BSP kernel

## What was applied from this

- `0019` no longer raises the GPU `opp-microvolt` values. Upstream's numbers are
  already the vendor's worst-case column, so the patch now only adds
  `opp-supported-hw` and the SCMI clock trio. The per-variant entries keep their
  higher voltages: the vendor publishes no per-variant GPU table, so there is
  nothing to check them against and erring upwards is the safe direction.
- `0020` added the top CPU OPP each cluster was missing against the vendor -
  2208 MHz on cluster0 and 2304 MHz on cluster1 - and was **reverted after
  measuring it**. Neither is reachable on this board. See "Why the top CPU OPPs
  were dropped" below.

Not applied: PVTM grade selection. Doing it correctly means driving the GPU to
800 MHz at 750000, sampling `GCK_CNT_AVG`, and restoring, during boot. Sampling
at any other operating point yields a wrong grade, which is worse than no grade.
The register bases are recorded below if this is picked up later.

The whole series applies to pristine 6.18.40 with zero rejects.

Source: ArmSoM Debian 12 XFCE image (`armsom-sige5-rk3576-debian12-xfce-20251218`),
Armbian-unofficial 24.11.0 Bookworm, kernel `6.1.75-vendor-rk35xx`, booted on the
Sige5 from an SD card via our own mainline U-Boot on the eMMC. Values come from
`dtc -I fs -O dts /proc/device-tree` on the live system, so they are what the
vendor kernel actually runs with, not what a DTS in some tree claims.

Hex in the device tree is decoded to decimal here. Voltages are µV, frequencies Hz.

## Per-die grading on this board

The vendor kernel measures each domain at boot and picks a voltage grade:

```
cpu cpu0:            bin=0  pvtm=2018  pvtm-volt-sel=3
cpu cpu4:            bin=0  pvtm=2154  pvtm-volt-sel=3
RKNPU 27700000.npu:  bin=0  pvtm=868   pvtm-volt-sel=4
rockchip-dmc dmc:    bin=0
```

`Failed to get leakage` on every domain - this board's OTP has no leakage cell
programmed, so grading falls back to PVTM alone.

The GPU line is absent because the vendor `mali` driver never probed (see below),
and the grade is printed by the driver that owns the OPP table.

## Voltage-grade buckets

`rockchip,pvtm-voltage-sel` is a flat list of `<min max grade>` triples.

GPU (`gpu-opp-table`):

| pvtm | grade |
| --- | --- |
| 0-800 | L0 |
| 801-820 | L1 |
| 821-840 | L2 |
| 841-860 | L3 |
| 861-9999 | L4 |

NPU (`npu-opp-table`):

| pvtm | grade |
| --- | --- |
| 0-796 | L0 |
| 797-816 | L1 |
| 817-836 | L2 |
| 837-856 | L3 |
| 857-9999 | L4 |

The NPU's measured `pvtm=868` falls in the last bucket and the kernel printed
`pvtm-volt-sel=4`, which confirms the decoding.

This board's GPU PVTM score, measured through `GCK_CNT_AVG` and recorded in
`rk3576-gpu-clocks.md`, is **802**. In the table above that is the
801-820 bucket, so this die's **GPU grades L1**.

That supersedes the grade in the earlier document, which read 802 as **L5** from
a table of eleven grades with a `785 804 5` bucket. No such table governs the
RK3576 GPU: the live `gpu-opp-table` defines five grades, L0-L4, with different
boundaries. The 802 measurement stands; the grade derived from it did not.

The consequence is small but real. At L5 the earlier note concluded mainline's
825000 at 900 MHz was correct for this die by coincidence. At L1 the vendor has
no L1 override for 900 MHz at all, so the base 825000 applies - the same number,
now for a stated reason rather than a coincidence.

## GPU OPP table

`opp-microvolt` is `<target min max>`; only the target differs between grades, and
the ceiling is 850000 everywhere. Blank means the grade has no override and
inherits the base row.

| MHz | base (L0) | L1 | L2 | L3 | L4 |
| --- | --- | --- | --- | --- | --- |
| 300 | 700000 | | | | |
| 400 | 700000 | | | | |
| 500 | 700000 | | | | |
| 600 | 700000 | | | | |
| 700 | 725000 | 712500 | 700000 | 700000 | 700000 |
| 800 | 775000 | 762500 | 750000 | 737500 | 725000 |
| 900 | 825000 | | 812500 | 800000 | 787500 |
| 950 | 850000 | | 837500 | 825000 | 812500 |

Two things worth noting against our mainline table:

- The vendor ships a **950 MHz** OPP, which we dropped. Note it cannot come from
  the SCMI path: TF-A's `rk3576_gpu_pvtpll_table` has no 950 entry and its lookup
  is exact-match, so a 950 request is rejected outright. The vendor node carries
  both an SCMI clock and the CRU `clk_gpu`, so 950 has to be a CRU rate. That is
  a reason to keep the two-clock arrangement, not to re-add 950 to the SCMI path.
- 700 and 800 are a deliberate voltage pairing, not a silicon boundary.

At L2, this die is entitled to 800 MHz at 750000 rather than the 775000 a
worst-case part needs.

## NPU OPP table

| MHz | base (L0) | L1 | L2 | L3 | L4 |
| --- | --- | --- | --- | --- | --- |
| 300 | 725000 | | | | |
| 400 | 725000 | | | | |
| 500 | 725000 | | | | |
| 600 | 725000 | | | | |
| 700 | 750000 | 737500 | 725000 | 725000 | 725000 |
| 800 | 775000 | | 750000 | 737500 | 725000 |
| 900 | 800000 | 787500 | 775000 | 762500 | 750000 |
| 1000 | 850000 | | | 837500 | 825000 |

This die grades L4, so its NPU is entitled to 1000 MHz at 825000 and 900 MHz at
750000.

## Shared PVTM properties

Identical in both the GPU and NPU tables:

| property | value | meaning |
| --- | --- | --- |
| `rockchip,pvtm-freq` | 800000 | reference OPP for the measurement, kHz |
| `rockchip,pvtm-volt` | 750000 | voltage held during the measurement |
| `rockchip,pvtm-offset` | 0x54 | PVTPLL `GCK_CNT_AVG` register |
| `rockchip,pvtm-sample-time` | 1100 | µs |
| `rockchip,pvtm-ref-temp` | 35 | °C |
| `rockchip,pvtm-temp-prop` | 0, 0 | no temperature compensation |
| `rockchip,low-temp` | 15000 | 15 °C |
| `rockchip,low-temp-min-volt` | 750000 | cold voltage floor |
| `rockchip,temp-hysteresis` | 5000 | 5 °C |
| `intermediate-threshold-freq` | 300000 | kHz |
| `rockchip,pvtm-pvtpll` | present | take PVTM from the PVTPLL, not a standalone PVTM block |
| `nvmem-cell-names` | leakage, opp-info | |

`volt-mem-read-margin` (both tables) sets the SRAM read margin by voltage:

| voltage at or above | margin |
| --- | --- |
| 855000 | 1 |
| 765000 | 2 |
| 675000 | 3 |
| 495000 | 4 |

This is the read-margin item left unimplemented in our port; these are the real
thresholds.

## Delivered clocks under the vendor kernel

From `clk_summary` with the GPU idle:

```
scmi_clk_ddr     2112000000
scmi_clk_gpu      297000000
scmi_clk_npu      200000000
clk_gpu           297000000
aclk_dma2ddr      786431991
```

`scmi_clk_gpu` reads **297 MHz**, which is `1188000000 / 4` - a divider-derived
value, not an echo of the 300000000 the devfreq table asks for. The vendor GPU
node carries both an SCMI clock and the CRU `clk_gpu`, the same two-clock
arrangement our patch adopted.

DDR runs at **2112 MHz**. Available DMC OPPs: 528, 1068, 1560, 2112 MHz.

## CPU cluster OPP tables

Both clusters measure at 1800000 kHz / 850000 uV, and both apply temperature
compensation (`pvtm-temp-prop` 890 for cluster0, 920 for cluster1) which the GPU
and NPU tables do not.

cluster0 (A53) buckets: `0-1939 L0, 1940-1969 L1, 1970-1999 L2, 2000-2029 L3,
2030-2059 L4, 2060+ L5`. This die reads **2018 → L3**, matching the kernel's
printed `pvtm-volt-sel=3`.

| MHz | base | L1 | L2 | L3 | L4 | L5 |
| --- | --- | --- | --- | --- | --- | --- |
| 408-1200 | 700000 | | | | | |
| 1416 | 725000 | 712500 | 700000 | 700000 | 700000 | 700000 |
| 1608 | 750000 | 750000 | 737500 | 737500 | 725000 | 712500 |
| 1800 | 825000 | 825000 | 812500 | 800000 | 787500 | 775000 |
| 2016 | 900000 | 887500 | 875000 | 862500 | 850000 | 837500 |
| 2208 | 950000 | 937500 | 925000 | 912500 | 900000 | 887500 |

cluster1 (A72) buckets: `0-2065 L0, 2066-2095 L1, 2096-2125 L2, 2126-2155 L3,
2156-2185 L4, 2186+ L5`. This die reads **2154 → L3**, again matching.

| MHz | base | L1 | L2 | L3 | L4 | L5 |
| --- | --- | --- | --- | --- | --- | --- |
| 408-1200 | 700000 | | | | | |
| 1416 | 712500 | 700000 | 700000 | 700000 | 700000 | 700000 |
| 1608 | 737500 | 725000 | 712500 | 700000 | 700000 | 700000 |
| 1800 | 800000 | 787500 | 775000 | 762500 | 750000 | 737500 |
| 2016 | 862500 | 850000 | 837500 | 825000 | 812500 | 800000 |
| 2208 | 925000 | 912500 | 900000 | 887500 | 875000 | 862500 |
| 2304 | 950000 | 937500 | 925000 | 912500 | 900000 | 887500 |

Three domains - cpu0, cpu4 and the NPU - each print a grade the kernel computed
itself, and the buckets decoded here reproduce all three. That is the basis for
trusting the GPU grade, which no driver printed.

## DMC and VOP

DMC has no `pvtm-voltage-sel`; it grades on leakage/bin only, and this board's
OTP has no leakage cell.

| MHz | base | L1 |
| --- | --- | --- |
| 528 | 725000 | 700000 |
| 1068 | 725000 | 700000 |
| 1560 | 725000 | 725000 |
| 2736 | 800000 | 775000 |

The DT's top DMC entry is 2736 MHz, but `devfreq` offers `528, 1068, 1560, 2112`
and runs at **2112**, a rate absent from the table. The DDR firmware substitutes
its own list, so the DMC OPP labels are advisory in the same way the GPU's are.

VOP: 500 MHz at 700000; 594 and 702 MHz at 750000 base / 725000 L1.

## Regulator rails

From the vendor kernel's regulator registration:

| rail | range | boot value |
| --- | --- | --- |
| `vdd_gpu_s0` | 550-900 mV | 750 mV |
| `vdd_npu_s0` | 550-950 mV | 750 mV |
| `vdd_cpu_big_s0` | 550-950 mV | 850 mV |
| `vdd_cpu_lit_s0` | 550-950 mV | 850 mV |
| `vdd_ddr_s0` | 550-1200 mV | 850 mV |

The **GPU rail tops out at 900 mV**, lower than every other core rail. The GPU
OPP table's 850000 ceiling sits inside it with 50 mV of headroom.

## CPU and devfreq ranges

| domain | frequencies |
| --- | --- |
| policy0 (A53) | 408, 600, 816, 1008, 1200, 1416, 1608, 1800, 2016, 2208 MHz |
| policy4 (A72) | as above plus 2304 MHz |
| NPU devfreq | 300-1000 MHz in 100 MHz steps, governor `rknpu_ondemand` |
| GPU devfreq | 300-900 MHz in 100 MHz steps plus 950, governor `simple_ondemand` |
| DMC devfreq | 528, 1068, 1560, 2112 MHz, governor `dmc_ondemand` |

Thermal zones: `soc`, `bigcore`, `little-core`, `ddr`, `npu`, `gpu` - all ~51 °C idle.

Regulator idle voltages: `vdd_gpu_s0` 700000, `vdd_npu_s0` 725000,
`vdd_cpu_big_s0` 700000, `vdd_cpu_lit_s0` 700000, `vdd_logic_s0` 750000,
`vdd_ddr_s0` 800000.

## Why the vendor GPU driver did not probe

```
mali 27800000.gpu: Kernel DDK version g18p0-01eac0
mali 27800000.gpu: error -ENXIO: IRQ JOB not found
mali 27800000.gpu: error -ENXIO: IRQ MMU not found
mali 27800000.gpu: error -ENXIO: IRQ GPU not found
mali 27800000.gpu: Register window unavailable
mali: probe of 27800000.gpu failed with error -5
```

The node is `compatible = "arm,mali-bifrost"`, so the vendor driver binds, but the
`interrupt-names` were converted to mainline panfrost's lowercase `job`/`mmu`/`gpu`
while the vendor driver looks up uppercase `JOB`/`MMU`/`GPU`, and the `reg` window
is panfrost-sized. This is the hybrid-DTS hazard: vendor driver, mainline bindings,
broken in a way neither pure tree is.

This node tells us nothing about the vendor's own GPU bindings - it has a single
`clock-names = "core"` and no SCMI clock. For those, read the vendor DTS itself
(`rk3576-linux6.1-20251118/kernel-6.1/arch/arm64/boot/dts/rockchip/rk3576.dtsi`):

```
compatible = "arm,mali-bifrost";
interrupt-names = "GPU", "MMU", "JOB";
clocks = <&scmi_clk CLK_GPU>, <&cru CLK_GPU>;
clock-names = "clk_mali", "clk_gpu";
assigned-clocks = <&cru CLK_GPU>;
assigned-clock-rates = <198000000>;
```

So the vendor **does** drive the GPU from an SCMI clock and a CRU clock together,
which is the arrangement our patch adopted. Armbian's conversion collapsed that to
a single CRU clock and lowercased the interrupt names, which is exactly why the
vendor `mali` driver cannot bind to it.

Watch the clock IDs when moving between trees: the vendor header defines
`CLK_GPU = 456`, while mainline defines `CLK_GPU = 448` and `SCMI_CLK_GPU = 456`.
The same number means different clocks in the two trees.

## Running panfrost on the vendor kernel

The vendor config ships `CONFIG_DRM_PANFROST=m`, and the node's mainline
bindings are exactly what panfrost wants, so `modprobe panfrost` binds the GPU
that `mali` could not:

```
panfrost 27800000.gpu: mali-g52 id 0x7402 major 0x1 minor 0x0
panfrost 27800000.gpu: shader_present=0x7 l2_present=0x1
[drm] Initialized panfrost 1.2.0 for 27800000.gpu on minor 2
```

That gives a working GPU driver under the vendor kernel with the vendor OPP
table. Because the node is CRU-clocked this exercises the **CRU path**, not the
vendor SCMI path - but `clk_gpu` is a real CRU clock whose rate comes from
divider registers, so unlike the SCMI readback it is a measurement.

Sweeping every OPP with the `userspace` governor and reading `clk_gpu`:

| requested | `clk_gpu` |
| --- | --- |
| 300000000 | 297000000 |
| 400000000 | 396000000 |
| 500000000 | 500000000 |
| 600000000 | 594000000 |
| 700000000 | 594000000 |
| 800000000 | 786431991 |
| 900000000 | 786431991 |
| 950000000 | 786431991 |

Conclusions:

- **The CRU path caps at 786.4 MHz.** The vendor's 900 and 950 MHz OPPs are
  labels only; all three top entries deliver the same clock. 700 likewise
  collapses onto 600.
- 786.4 MHz matches the ~787 MHz measured on our mainline CRU path, from an
  independent kernel, driver and device tree. Two unrelated stacks agreeing on
  the same ceiling is strong evidence the number is the hardware's, not ours.
- Our SCMI path's measured 821 MHz peak is **faster than this vendor BSP image
  achieves**, by 4.4%. The SCMI/PVTPLL route is what makes rates above 786 MHz
  reachable at all.
- Rate labels not matching delivered clocks is not a defect we introduced on the
  SCMI path. The vendor's shipping CRU table has the same property, and its DMC
  table has it too (below).

What remains untested is the vendor's *own* GPU path: `mali` on a DTS with
vendor bindings and the SCMI `clk_mali` clock. That needs a device tree this
image does not contain.

## What the grading actually applied

`/sys/kernel/debug/opp/opp_summary` shows the voltage each OPP ended up with
after grade selection.

The NPU got its L4 column, matching the decoded table exactly:

| MHz | applied | base | L4 |
| --- | --- | --- | --- |
| 700 | 725000 | 750000 | 725000 |
| 800 | 725000 | 775000 | 725000 |
| 900 | 750000 | 800000 | 750000 |
| 1000 | 825000 | 850000 | 825000 |

The GPU got **base voltages only** - 700/725000, 800/775000, 900/825000,
950/850000 - because panfrost does not call Rockchip's PVTM grading helper. The
grade exists in the device tree and is never consulted.

That is the practical form of the unimplemented grade-selection item: on mainline
panfrost the GPU always runs the worst-case column, whatever the die. On this
board that costs 12500-50000 uV per OPP against the L1 entitlement.

## Our voltages against the vendor's

Upstream mainline's `rk3576.dtsi` already carries the vendor's base column
exactly. Our `0019` patch raised all of them:

| MHz | upstream = vendor base | our patch |
| --- | --- | --- |
| 600 | 700000 | 712500 |
| 700 | 725000 | 750000 |
| 800 | 775000 | 812500 |
| 900 | 825000 | 875000 |
| ceiling | 850000 | 875000 |

**No GPU OPP in the vendor table exceeds 850000 at any frequency or any grade**,
and the vendor rates 900 MHz at 825000. Our 875000 at 900 MHz is 50000 above the
vendor's worst-case for that rate and 25000 above their table's global ceiling. It
is inside the rail's 900 mV limit so it is not unsafe, but it is unjustified by
vendor data: the experiment that motivated it moved delivered rate by 0.1-0.7%.

The raise was made believing those were "the vendor's voltages". They are not.
Together with the eleven-grade bucket table, both errors trace to the same cause -
figures taken from a different Rockchip SoC.

Recommendation: revert the GPU `opp-microvolt` values to upstream, which is to say
to the vendor base column.

## Variant selection: the vendor does not use opp-supported-hw

`opp-supported-hw` appears **zero times in the entire vendor device tree**, and
there is no `rockchip,*supported*` or `bin` property anywhere either. On RK3576
the vendor ships one OPP table per domain and differentiates only by per-die PVTM
voltage grade.

Our variant masks (`0xf9`, `0xf1`, `0x08`, `0x06`, fail-closed `0x100`) are a
mainline-only construct with no counterpart in the vendor tree. That does not make
them wrong - the S/J/M frequency caps come from part documentation, not from the
BSP - but nothing in the vendor kernel corroborates them.

## OTP contents on this board

The vendor DT names the cells, which makes the raw OTP readable:

| cell | offset | size | value here |
| --- | --- | --- | --- |
| `cpu-code` | 0x02 | 2 | 0x3576 |
| `cpu-version` | 0x05 | 1 | 0x00 |
| `id` | 0x0a | 16 | `NY7U3...` |
| `cpub-leakage` | 0x1e | 1 | 5 |
| `cpul-leakage` | 0x1f | 1 | 4 |
| `npu-leakage` | 0x20 | 1 | 5 |
| `gpu-leakage` | 0x21 | 1 | 6 |
| `cpub/cpul/npu/gpu/logic-opp-info` | 0x30-0x53 | 6 each | **all zero** |

The factory `opp-info` cells are unprogrammed, which is why every domain grades
from a live PVTM measurement rather than stored data. The kernel also logs
`Failed to get leakage` on every domain even though the leakage bytes are present.

Our `speed-bin` cell is offset 0x08 bits 0-5, which reads **0x01** here. That is
not one of the M/J/S codes (0x0d/0x0a/0x13), so our decode falls through to
`RK3576_BIN_BASE` - correct for a plain RK3576. The vendor kernel does not read
this cell at all.

## CPU headroom upstream leaves on the table

Upstream `rk3576.dtsi` matches the vendor's base voltage column exactly for every
CPU OPP it ships, but stops one step short in both clusters:

| cluster | upstream max | vendor max |
| --- | --- | --- |
| cluster0 (A53) | 2016 MHz @ 900000 | 2208 MHz @ 950000 |
| cluster1 (A72) | 2208 MHz @ 925000 | 2304 MHz @ 950000 |

That is +9.5% on the little cluster and +4.3% on the big one, with vendor-backed
voltages. 950000 is exactly the `vdd_cpu_big_s0` / `vdd_cpu_lit_s0` rail ceiling
(550-950 mV), so the top step runs the rail at its limit; at this die's L3 grade
the vendor would use 912500 instead.

Whether to add them is a judgement call - upstream may have omitted them for
validation or thermal reasons, and our thermal design starts throttling at 85 C
where the vendor starts at 75 C. But the vendor BSP ships them for this exact
board.

## Why the top CPU OPPs were dropped

Added, flashed and measured. `stress-ng --cpu 8`, both clusters pinned to
`performance`, five minutes:

| | |
| --- | --- |
| peak temperature | **98 C** |
| A53 range | 1200000 - 2016000 (added OPP 2208000 never reached) |
| A72 range | 1800000 - 2208000 (added OPP 2304000 never reached) |
| stress-ng | passed 8/8, 0 failures |
| BUG/Oops/lockup | 0 |

Temperature crossed the 85 C passive trip within 40 seconds and settled in the
mid 90s. Under sustained load both clusters throttled *below* the maximum
upstream already ships - A53 to 1608000, A72 to 1800000.

The board therefore has no thermal headroom for these rates. They are not merely
useless: an OPP the hardware never occupies still raises the maximum capacity the
energy model attributes to each cluster, which skews scheduling and thermal
budgeting.

Worth revisiting only with fan control (the rk3576 PWM driver is still only
posted upstream, and the Sige5 PWM node is not in mainline) or the vendor's more
conservative thermal tuning.

Note also this is a measurement of the *system*, not the silicon: the vendor
ships these rates, and a board with active cooling may well hold them.

## The DDR rate mystery, resolved

The vendor DMC table's top entry is 2736 MHz while `devfreq` offers 2112. The
loader recipe in Rockchip's rkbin explains it - the DDR blob is
`rk3576_ddr_lp4_2112MHz_lp5_2736MHz_v1.09.bin`: **2112 MHz is the LPDDR4 rate
and 2736 MHz the LPDDR5 rate**. The Sige5 is LPDDR4, so 2112 is its ceiling and
2736 belongs to LPDDR5 boards. The DT table covers both and the fitted DRAM
selects. This supersedes the earlier reading that the firmware "substitutes its
own list".

## Thermal design differs from the vendor's

| zone | upstream mainline | vendor |
| --- | --- | --- |
| bigcore | passive 85 C, 1 cooling device | critical only |
| littlecore | passive 85 C, 1 cooling device | passive 75 C and 85 C, 3 cooling devices |
| gpu | passive 85 C, 1 cooling device | critical only |
| npu, ddr | critical 115 C only | critical 115 C only |

The vendor drives CPU, CPU and **GPU** throttling from the single
`little-core-thermal` zone with equal contribution (1024 each), and its first
passive trip is 75 C. Mainline throttles each domain from its own sensor at 85 C.
Mainline's per-domain approach is arguably the better design; the vendor's is more
conservative about when it starts. No change is required, but the 10 C difference
is worth knowing when interpreting sustained-load results.

The board also has a `pwm-fan` node.

## Energy model

`dynamic-power-coefficient`: GPU 1625, NPU 2570, cpu@0 120, cpu@100 320. Our
GPU value already matches the vendor's exactly.

## DDR

DDR was trained by **our** bootloader, not the vendor's - this is the vendor
kernel on our U-Boot - so this says nothing about vendor DDR init. It does show
our training produced a working 2112 MHz configuration that the vendor's DMC
driver drives without complaint.

`devfreq/dmc/trans_stat` after ~49 minutes of uptime:

| MHz | time (ms) |
| --- | --- |
| 528 | 1613 |
| 1068 | 0 |
| 1560 | 0 |
| 2112 | 2940370 |

Six transitions, so DDR DVFS is live but the `dmc_ondemand` governor keeps it
pinned at the top rate. The 1068 and 1560 steps were never used.

## RKNPU

`/sys/kernel/debug/rknpu/version` reports **v0.9.7**. Our mainline port carries
v0.9.8, so we are ahead of this BSP image, not behind it. The driver is live here
(`freq` 300000000, `volt` 725000, both cores idle).

## Booting this image on our bootloader

Recorded because it was not obvious:

- Our U-Boot cannot read Armbian's ext4 partition, so `sysboot`/`load` on
  `mmc 1:1` fails silently. Copying `Image`, the DTB and `uInitrd` onto the FAT
  `bootfs` that U-Boot already reads works.
- `root=/dev/mmcblk1p1` fails - MMC controller numbering differs between the
  vendor kernel and mainline. `root=UUID=...`, which is what Armbian's own
  `extlinux.conf` uses, is numbering-independent and works.
- Armbian's `extlinux.conf` passes no `console=`; the console follows the DTB's
  `stdout-path` to HDMI. Serial stays silent on a fully successful boot. Adding
  `console=ttyS0,1500000` puts the console and the getty on the debug UART.
- First boot runs an interactive setup wizard (root password, shell, user, locale)
  before any shell is reachable.
