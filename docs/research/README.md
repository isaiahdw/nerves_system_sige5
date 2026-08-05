# Research notes

Working notes from bringing this board up. Each records what was measured and
how, so a conclusion can be re-checked rather than taken on trust.

| note | what it covers |
| --- | --- |
| [rk3576-secure-otp-survey.md](rk3576-secure-otp-survey.md) | The secure OTP: what is in it, where the HUK belongs and how that was confirmed, where the secure TRNG is, and what fusing one would involve. |
| [rk3576-gpu-clock-investigation.md](rk3576-gpu-clock-investigation.md) | Why requested GPU rates are not delivered rates, measured through panfrost's cycle counters, and why an OPP rate names an operating point rather than a frequency. |
| [rk3576-vendor-opp-tables.md](rk3576-vendor-opp-tables.md) | The vendor BSP's OPP tables and voltage grades, decoded. |
| [rk3576-bsp-gpu-clock-path.md](rk3576-bsp-gpu-clock-path.md) | How the vendor BSP reaches the GPU clock, for comparison with the mainline SCMI path. |
| [rk3576-firmware-versions.md](rk3576-firmware-versions.md) | Which rkbin blobs exist, which are used here, and what changed between them. |
| [rk3576-panfrost-online.md](rk3576-panfrost-online.md) | What the state of panfrost on RK3576 was upstream, separating "probes and renders" from "safe DVFS". |
| [rk3576-gpu-stress-tools.md](rk3576-gpu-stress-tools.md) | Which GPU load generators are usable on a headless board, and which lie. |
| [vendor-bsp/](vendor-bsp/) | Raw artifacts pulled off the vendor image - device tree, kernel config - kept so the decoded notes can be checked against the source. |
