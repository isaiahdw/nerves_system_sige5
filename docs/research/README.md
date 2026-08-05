# Research notes

Reference notes on how this hardware behaves. Each states what is known and how
it was established, so a conclusion can be re-checked rather than taken on
trust.

| note | what it covers |
| --- | --- |
| [rk3576-secure-world.md](rk3576-secure-world.md) | The secure address map, what the secure OTP holds, the HUK and how one is fused, the secure TRNG, and RPMB. |
| [rk3576-gpu-clocks.md](rk3576-gpu-clocks.md) | How the GPU's PVTPLL clock works, what each OPP actually delivers, and the device-tree decisions that follow. |
| [rk3576-vendor-opp-tables.md](rk3576-vendor-opp-tables.md) | The vendor BSP's OPP tables and voltage grades, decoded. |
| [rk3576-bsp-gpu-clock-path.md](rk3576-bsp-gpu-clock-path.md) | How the vendor BSP reaches the GPU clock, for comparison with the mainline SCMI path. |
| [rk3576-firmware-versions.md](rk3576-firmware-versions.md) | Which rkbin blobs exist, which are used here, why the BL32 blob is not, and the trust-ini load-address trap. |
| [rk3576-panfrost-online.md](rk3576-panfrost-online.md) | The state of panfrost on RK3576 upstream, separating "probes and renders" from "safe DVFS". |
| [rk3576-gpu-stress-tools.md](rk3576-gpu-stress-tools.md) | Which GPU load generators are usable on a headless board, and which lie. |
| [vendor-bsp/](vendor-bsp/) | Raw artifacts pulled off the vendor image - device tree, kernel config - kept so the decoded notes can be checked against the source. |
