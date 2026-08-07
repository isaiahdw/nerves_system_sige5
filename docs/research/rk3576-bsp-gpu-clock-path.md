# RK3576 GPU clock and DVFS path in Rockchip's Linux 6.1 BSP

Source examined: `projects/rk3576-linux6.1-20251118/kernel-6.1` at commit `560f5768ae5240cbd3baeb2078598c17752638c4`. The BSP uses Arm's vendor Bifrost driver, not Panfrost (`arch/arm64/configs/rk3576.config:1-4`).

## Bottom line

The BSP does scale the GPU through `SCMI_CLK_GPU`, but it does **not** treat that SCMI clock as sufficient by itself. Its GPU node has two functional clocks: SCMI as `clk_mali`, followed by CRU `CLK_GPU` as `clk_gpu`. The separate 198 MHz assigned rate applies to the CRU clock, not SCMI (`arch/arm64/boot/dts/rockchip/rk3576.dtsi:2763-2781`). Its OPP table then names **both** CRU `CLK_GPU` and `PCLK_GPU_ROOT` as helper clocks (`rk3576.dtsi:2833-2845`). Rockchip's OPP callback bulk-enables both helpers around every `clk_set_rate()` on the SCMI clock (`drivers/soc/rockchip/rockchip_opp_select.c:2403-2428`).

That is the material difference from the current mainline experiment: patch 0016 keeps SCMI and `PCLK_GPU_ROOT`, but removes CRU `CLK_GPU`. The BSP explicitly keeps all three roles:

| Role | BSP clock | Use |
|---|---|---|
| Rate selected by OPP/devfreq | SCMI `CLK_GPU` / `clk_mali` | Request sent to BL31 |
| Final GPU clock gate / normal source | CRU `CLK_GPU` / `clk_gpu` | Regular GPU clock and an OPP helper |
| Register-access helper | CRU `PCLK_GPU_ROOT` | OPP helper for PVTPLL/read-margin work |

The likely explanation for the reset on the first 200-to-300 MHz OPP change is therefore that BL31's rate-changing sequence requires the downstream CRU `CLK_GPU` gate as well as the APB clock. This is a strong inference from the BSP lifecycle and the SoC clock topology, **not a proved firmware fact**: BL31 is binary, and the kernel source does not expose its internal switching algorithm.

## Device-tree and clock topology

The BSP GPU node declares:

```dts
clocks = <&scmi_clk CLK_GPU>, <&cru CLK_GPU>;
clock-names = "clk_mali", "clk_gpu";
assigned-clocks = <&cru CLK_GPU>;
assigned-clock-rates = <198000000>;
power-domains = <&power RK3576_PD_GPU>;
```

Thus the pre-probe assigned rate is a CRU operation, not the first SCMI rate request (`rk3576.dtsi:2775-2780`). The OPP descriptor adds PVTPLL, read-margin, and two auxiliary clocks (`rk3576.dtsi:2822-2845`). The vendor binding describes `rockchip,opp-clocks` as clocks used to access PVTPLL and read margin (`Documentation/devicetree/bindings/opp/rockchip-opp.txt:56-73`).

The CRU driver exposes a normal PLL/divider source, final `CLK_GPU` gate, and `PCLK_GPU_ROOT` (`drivers/clk/rockchip/clk-rk3576.c:916-924`). Rockchip's first-party RK3576 register definitions show an inner mux choosing the normal GPU source or GPU PVTPLL, another mux choosing deepslow or PVTPLL, and distinct gates for final GPU clock, GPU PVTPLL source, PCLK root, PVTPLL reference, and PVTPLL APB (`hal/lib/CMSIS/Device/RK3576/Include/rk3576.h:6562-6571,7836-7859,27113-27118`). Linux exposes only part of that topology; secure firmware owns the SCMI-controlled portion.

`intermediate-threshold-freq = <300000>` is **not evidence of a 300 MHz normal/PVTPLL switch threshold**. The vendor binding defines it as an optimization for intermediate-rate sequencing, and the OPP code parses it in the read-margin setup path (`rockchip-opp.txt:65-67`; `rockchip_opp_select.c:1750-1760`). The exact firmware source-selection threshold is not recoverable from the supplied kernel source.

## Probe and first rate transition

The vendor driver acquires all DT clocks by index and calls `clk_prepare()` on each before initializing Rockchip's OPP layer (`drivers/gpu/arm/bifrost/mali_kbase_core_linux.c:4528-4578`). On a non-atomic SCMI transport, the SCMI clock provider implements state enable/disable in `.prepare`/`.unprepare`, while `.set_rate` remains a separate SCMI request (`drivers/clk/clk-scmi.c:69-88,105-132`). This agrees with the UART diagnostic: preparing/enabling SCMI can succeed even though the later rate request resets the board.

The RK3576 platform passes `"clk_mali"` to `rockchip_init_opp_table()`, so the OPP core's rate clock is the first, SCMI clock (`drivers/gpu/arm/bifrost/platform/rk/mali_kbase_config_rk.c:702-747`). The OPP extension:

1. parses `rockchip,opp-clocks` in DT order (`rockchip_opp_select.c:1632-1686`);
2. bulk-enables CRU `CLK_GPU` and `PCLK_GPU_ROOT` while initializing PVTPLL/read-margin metadata (`rockchip_opp_select.c:1697-1777`);
3. recognizes the selected clock as SCMI and installs its custom clock callback (`rockchip_opp_select.c:1536-1562`);
4. bulk-enables both helpers around every SCMI `clk_set_rate()` and refuses a PVTPLL rate change when the device is runtime-inactive (`rockchip_opp_select.c:2403-2428`).

After devfreq registration, Rockchip's system monitor immediately calls `rockchip_opp_check_rate_volt()` (`drivers/gpu/arm/bifrost/backend/gpu/mali_kbase_devfreq.c:625-638`; `drivers/soc/rockchip/rockchip_system_monitor.c:1374-1438`). That routine reads the SCMI rate, rounds it to an enabled OPP with `dev_pm_opp_find_freq_ceil()`, bulk-enables both helper clocks, and then applies the SCMI rate if runtime-active (`rockchip_opp_select.c:2432-2538`). With the firmware clock near 200 MHz and the lowest GPU OPP at 300 MHz, this is the BSP's first normalization to 300 MHz.

This differs in two ways from the observed mainline diagnostic: mainline performs its first `dev_pm_opp_set_opp()` while runtime PM reports suspended, and only `PCLK_GPU_ROOT` accompanies SCMI. The BSP explicitly conditions PVTPLL changes on runtime-active state and supplies both helper clocks.

## Runtime PM and suspend/resume

The BSP does not rely on `opp-suspend` for this GPU. Its runtime-off callback explicitly sets the SCMI clock to 200 MHz before the domain is powered down, then invalidates the cached read margin (`drivers/gpu/arm/bifrost/platform/rk/mali_kbase_config_rk.c:229-238`). Runtime-on bulk-enables both OPP helper clocks, restores read margin, restores `current_nominal_freq` through SCMI, and disables the helpers (`mali_kbase_config_rk.c:204-227`). The generic runtime suspend/resume callbacks invoke those platform hooks around the genpd transition (`drivers/gpu/arm/bifrost/mali_kbase_core_linux.c:5903-5975`).

Normal power-on enables the vendor driver's regular DT clocks before `pm_runtime_get_sync()` (`mali_kbase_config_rk.c:240-289,376-395`). Separately, the platform bus attaches and powers the GPU domain before calling driver probe (`drivers/base/platform.c:1375-1403`). This sequencing is why copying only the DTS clock identities into Panfrost does not reproduce the BSP lifecycle.

## Firmware boundary

SCMI `clk_set_rate()` serializes the clock ID and 64-bit requested rate into a standard `CLOCK_RATE_SET` message (`drivers/firmware/arm_scmi/clock.c:349-394`). Rockchip's OPP layer also queries PVTPLL capability and can pass voltage-bin/table adjustments through the Rockchip `SIP_PVTPLL_CFG` SMC interface (`rockchip_opp_select.c:1373-1459,2080-2102`; `drivers/firmware/rockchip_sip.c:304-329`).

What cannot be established from these sources is BL31's precise normal-PLL/PVTPLL selection rule, its threshold, or exactly which missing gate causes the reset. Those remain firmware-internal.

## Mainline implication and next test

Current patch 0016's model—SCMI as Panfrost `core`, `PCLK_GPU_ROOT` as `bus`, and no CRU `CLK_GPU`—is not BSP parity. The BSP's rate path always has **both** CRU `CLK_GPU` and `PCLK_GPU_ROOT` enabled when it asks SCMI to change rate.

The cleanest one-variable diagnostic is:

- keep the existing SCMI core and `PCLK_GPU_ROOT` bus arrangement;
- additionally retain and force-enable CRU `CLK_GPU` before the first OPP rate set;
- make no other lifecycle or rate changes for that boot.

A temporary `CLK_IS_CRITICAL`/forced-enable diagnostic is sufficient to test the hypothesis without first designing a permanent Panfrost interface. If the first 200-to-300 MHz SCMI request then returns and the GPU reaches high OPPs, the missing final gate is causal. If it still resets, the result is inconclusive about the whole BSP lifecycle because mainline still attempts the transition while runtime PM reports suspended.

A production-quality parity fix would need Panfrost (or a Rockchip-specific OPP layer) to represent two auxiliary prerequisites, hold both around SCMI rate/voltage/read-margin transitions, reject or defer transitions while the GPU domain is inactive, and perform the BSP's explicit 200 MHz pre-power-off / restore-after-power-on sequence. Simply reversing two clock enables is not what the BSP does.

## Teardown note

For completeness, the vendor driver's generic teardown uninitializes OPP state, then unprepares its regular clocks in forward DT order—SCMI before CRU (`mali_kbase_core_linux.c:4603-4629`). That documents the BSP behavior but should not be treated as proof that the same ordering is correct for a new Panfrost implementation; the meaningful safety property in the BSP is its runtime-off transition to 200 MHz before power loss.
