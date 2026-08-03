# How public RK3576 systems use Panfrost

Research date: 2026-08-02. This note distinguishes a GPU driver successfully probing/rendering from safe runtime DVFS through RK3576's secure PVTPLL path.

## Conclusion

I found no public RK3576 Panfrost implementation or hardware report that demonstrates safe runtime switching of the GPU through `SCMI_CLK_GPU` to a verified 900 MHz. The public implementations I found use Panfrost's ordinary `core` clock as **CRU `CLK_GPU`**. SCMI is assigned an initial 198 MHz rate, but Panfrost does not own or scale that SCMI clock.

That public configuration can probe Panfrost and run a desktop, but it is the CRU-only path already measured locally: devfreq can request the OPP table's 700–950 MHz entries while the clock framework supplies only the rates the CRU parent/divider can synthesize. None of the public test reports inspected measured `clk_gpu` against devfreq, exercised every OPP, demonstrated PVTPLL source switching, or validated suspend/resume after a high SCMI rate.

The only complete SCMI/PVTPLL lifecycle found remains Rockchip's vendor Mali Bifrost stack, documented separately in [rk3576-bsp-gpu-clock-path.md](rk3576-bsp-gpu-clock-path.md). It is not Panfrost: it wraps SCMI rate changes with CRU `CLK_GPU` and `PCLK_GPU_ROOT`, gates changes on runtime-active state, drops to 200 MHz before power-off, and restores after power-on.

## Upstream Linux: CRU is Panfrost's rate clock

Current upstream `rk3576.dtsi` declares the GPU's assigned clock as SCMI at 198 MHz, but gives the GPU device only CRU `CLK_GPU` named `core`. The same upstream table advertises OPPs through 950 MHz. Therefore generic Panfrost/devfreq calls `clk_set_rate()` on CRU `CLK_GPU`, not on SCMI. [Upstream RK3576 DTS](https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/rockchip/rk3576.dtsi#L1266-L1282) and [GPU OPP table](https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/rockchip/rk3576.dtsi#L345-L387).

The upstream topology originated in the RK3576 base-DT series and is still visible in current Linux. A later Rockchip-authored fix likewise shows `assigned-clocks = <&scmi_clk SCMI_CLK_GPU>` together with `clocks = <&cru CLK_GPU>`, confirming this is not a downstream presentation artifact. [Rockchip-authored RK3576 GPU DTS fix](https://lists.infradead.org/pipermail/linux-arm-kernel/2025-December/1090657.html).

This design does not disable devfreq; it supplies a normal OPP table to generic Panfrost. Its limitation is subtler: the OPP frequencies describe desired performance points, but Panfrost scales the CRU clock. Nothing in this public path calls `clk_set_rate()` on SCMI during runtime.

## Armbian's RK3576 Panfrost enablement: deliberate CRU-only conversion

Armbian's 6.1 vendor-kernel Panfrost change explicitly converted Rockchip's vendor node:

- from two vendor-driver clocks, SCMI `clk_mali` plus CRU `clk_gpu`;
- to one Panfrost clock, CRU `CLK_GPU` named `core`;
- while adding CRU `CLK_GPU` and `PCLK_GPU_ROOT` to the GPU power-domain clock list.

The exact diff is in [armbian/linux-rockchip PR #249](https://github.com/armbian/linux-rockchip/pull/249/files#diff-1f84c4879541bb86625a81227549532644923247261a53917811e40c4745af5f): the power domain gains both CRU clocks, while the GPU device drops SCMI from its `clocks` property and retains only CRU `CLK_GPU` as `core`. The PR description says the DTS came from the then-mainline patch and that the result made Panfrost work; it does not claim SCMI/PVTPLL DVFS or high-rate validation. [PR conversation](https://github.com/armbian/linux-rockchip/pull/249).

Armbian then removed `MODULES_BLACKLIST="panfrost"` for the ArmSoM CM5-IO and Sige5 boards. Its stated hardware test was that a GNOME desktop started with Panfrost. It also recorded a load-order caveat: manually loading a blacklisted module could panic, whereas automatic boot-time loading worked. [Armbian build PR #7307](https://github.com/armbian/build/pull/7307) and [its board-config diff](https://github.com/armbian/build/pull/7307/files).

Those are useful proofs of probe/render viability. They are not proofs of rate correctness: the test provides no actual clock rate, residency, transition, thermal, or runtime-PM measurements.

## Public user reports do not close the DVFS question

The Ubuntu Rockchip development discussion initially showed the GPU deferred on a missing regulator, then showed the Rockchip Mali DDK probing, and finally linked the Armbian Panfrost conversion with the statement that Panfrost worked. No report in that thread measures a Panfrost clock or high OPP. [RK3576 development discussion](https://github.com/Joshua-Riek/ubuntu-rockchip/discussions/959).

Recent mainline RK3576 boot logs can even leave the GPU unresolved, with genpd `sync_state()` pending on `27800000.gpu`; these logs are evidence that an RK3576 kernel boots, not that Panfrost or GPU DVFS works. [ROCK 4D linux-next boot log](https://gist.github.com/gahingwoo/7543c1be83c8b8ec15727a8f11a4873c).

I found no direct public log containing all of the evidence needed for a stronger claim: Panfrost renderer active, devfreq transition requests, independently read hardware clock rates, successful entry above the CRU ceiling, repeated runtime suspend/resume, and thermal clamping.

## Upstream explicitly documents the SCMI/runtime-PM failure

The absence of a public high-rate Panfrost example is not accidental. In May
2025, Jonas Karlman described the same PVTPLL constraint while adding RK3528 GPU
support: the PVTPLL requires the GPU power domain, regulators, and clocks to be
enabled, and an SCMI rate change while the GPU is runtime-suspended can freeze
the machine or cause an SError. He explicitly said the same issue exists on
RK3576 and RK3588 when `SCMI_CLK_GPU` is used for devfreq, and that a separate
mitigation series would follow. [RK3528 GPU series cover
letter](https://lists.infradead.org/pipermail/linux-arm-kernel/2025-May/1029151.html).

I did not find that promised RK3576/RK3588 mitigation in the public mailing-list
archives or current upstream tree. Current upstream still gives Panfrost CRU
`CLK_GPU` as its sole `core` clock. This is evidence that full SCMI/PVTPLL DVFS
has not landed upstream, not proof that no unpublished implementation exists.

## Classification of the available approaches

| Source / system | Panfrost clock | SCMI runtime scaling | What is actually demonstrated |
|---|---|---:|---|
| Upstream Linux RK3576 DTS | CRU `CLK_GPU` | No | Supported DT shape; OPP table present |
| Armbian 6.1 RK3576 Panfrost patch | CRU `CLK_GPU` | No | Driver loads automatically; GNOME starts |
| Public Ubuntu/ArmSoM reports | Armbian CRU path when using Panfrost | No evidence | Probe/render success only |
| Rockchip 6.1 Mali Bifrost BSP | SCMI rate clock plus CRU helpers | Yes | Full vendor DVFS lifecycle, but not Panfrost |
| Current local experiment | SCMI core plus retained CRU `CLK_GPU` and `PCLK_GPU_ROOT` helpers | Attempted | Probe-time 200 → 300 MHz succeeds; a runtime rate request while suspended raises an SError |

## Implication for the current work

There is no public Panfrost precedent to copy for RK3576's high-rate path. “Panfrost works on RK3576” in existing distro reports means the CRU-only configuration works; it does not contradict the local finding that its highest nominal OPPs collapse to lower actual CRU rates.

The local SCMI experiment is therefore new integration work, not a restoration
of an existing Panfrost design. Retaining both helper clocks has already proved
the first half: the probe-time SCMI transition now completes and Panfrost
initializes. It does not solve runtime PM; the subsequent SError shows that a
rate request can still enter BL31 after the GPU domain has powered down.

A shippable SCMI implementation must reproduce the BSP's runtime-active guard,
200 MHz pre-suspend transition, and ordered resume restoration while both helper
clocks and the GPU domain are live. Before independently designing that into
Panfrost, asking Jonas Karlman and the linux-rockchip/DRI maintainers for the
status of the promised RK3576/RK3588 mitigation series is the most efficient
next step. Falling back to the public CRU-only Panfrost model has deployed
precedent, but it accepts the observed rate ceiling and inaccurate top-OPP
accounting.
