# RK3576 NPU on a mainline kernel

The vendor RKNPU driver (v0.9.8) built out-of-tree against mainline, with the
vendor-only devfreq/OPP integration reimplemented on generic APIs and the NPU
MMU driven by mainline's rockchip-iommu. This is what that costs and where it
differs from the 6.1 BSP.

Measured on a Sige5 v1.2 on a 12 V/3 A supply.

## Frequency scaling and governors

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
cleared it settles at 300 MHz. `package/rknpu-driver/0015` exposes the raw counters at
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

## Differences from the 6.1 vendor BSP

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
