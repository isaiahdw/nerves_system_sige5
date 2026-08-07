# RK3576 Panfrost GPU stress tools

## Recommendation

Use `stress-ng --gpu` as the main load generator and use
`glmark2-es2-drm` off-screen for the fixed-frequency performance curve.
A small custom EGL/GBM harness is only necessary if exact active/idle pulse
timing or pixel-result verification is required.

## `stress-ng --gpu`

Buildroot 2026.05 packages stress-ng 0.21.00. Its GPU stressor opens
`/dev/dri/renderD128`, creates a GBM/EGL OpenGL ES 2 context, draws a
full-surface fragment workload, and calls `glFinish()` after every frame. It
supports configurable fragment work, framebuffer dimensions, texture size,
texture uploads, worker count, duration, and operation count. It checks
`glGetError()`, but does not validate rendered pixels.

Primary sources:

- [stress-ng 0.21.00 GPU stressor](https://github.com/ColinIanKing/stress-ng/blob/V0.21.00/stress-gpu.c)
- [Buildroot stress-ng package](../../deps/nerves_system_br/buildroot-2026.05/package/stress-ng/stress-ng.mk)

Start with a shader-focused load that avoids stress-ng's default 4096x4096
texture upload:

```sh
stress-ng --gpu 1 \
  --gpu-devnode /dev/dri/renderD128 \
  --gpu-xsize 1280 --gpu-ysize 720 \
  --gpu-frag 64 \
  --gpu-tex-size 1 --gpu-upload 1 \
  --timeout 30m --metrics-brief
```

Increase `--gpu-frag` gradually if this does not saturate the GPU. Avoid
starting with a very large value: a single excessively long frame can trigger
a legitimate scheduler timeout and obscure the DVFS test.

For runtime-PM cycling, leave the same process and EGL context alive and
alternate `SIGSTOP` and `SIGCONT`. Once stopped, outstanding GPU work can
finish and runtime PM can park the domain; continuing the process submits new
work through the resume path.

For context contention, repeat the soak with `--gpu 2` and then `--gpu 4`.
For a DDR/upload-heavy profile, increase `--gpu-tex-size` and
`--gpu-upload` separately from the fragment workload.

The stressor's built-in GPU-frequency sampler reads the Intel-specific
`/sys/class/drm/cardN/gt_cur_freq_mhz`, so RK3576 frequency must continue to be
sampled from the SCMI clock/debugfs source used during driver validation.

### Buildroot integration caveat

`stress-ng` detects EGL, GLES2, and GBM at build time, but the Buildroot
package does not declare those optional libraries as dependencies. In this
system, enable `BR2_PACKAGE_STRESS_NG=y` and make the ordering explicit in the
external tree:

```make
STRESS_NG_DEPENDENCIES += libegl libgbm libgles
```

Otherwise Buildroot can compile stress-ng before the Mesa development files
are available and silently produce a binary whose GPU stressor is marked
unimplemented.

## `glmark2-es2-drm`

Buildroot 2026.05 packages glmark2 2023.01 and enables its DRM/GLESv2 flavor
when EGL, GBM, GLES, udev, and C++ support are available. This system already
enables Mesa Panfrost, GBM, EGL, GLES, and eudev.

The 2023.01 command line supports off-screen rendering,
`--frame-end=finish`, per-scene duration/frame counts, and indefinite looping.
That makes it suitable for comparing identical work at 300, 600, and 900 MHz
without display-vblank limiting the result.

Primary sources:

- [glmark2 2023.01 options](https://github.com/glmark2/glmark2/blob/2023.01/src/options.cpp)
- [glmark2 loop scene](https://github.com/glmark2/glmark2/blob/2023.01/src/scene-loop.cpp)
- [Buildroot glmark2 package](../../deps/nerves_system_br/buildroot-2026.05/package/glmark2/glmark2.mk)

Example shader-heavy measurement:

```sh
glmark2-es2-drm \
  --off-screen --frame-end=finish \
  --size 1280x720 \
  --benchmark 'loop:vertex-steps=0:fragment-steps=64:fragment-loop=true:fragment-uniform=true:duration=30.0'
```

Run the same command under the userspace governor at 300, 600, and 900 MHz.
The FPS should change materially with frequency if the shader cores are using
the SCMI/PVTPLL path. It need not scale linearly because submission and memory
costs remain frequency-independent.

`glmark2 --validate --off-screen --frame-end=finish` adds lightweight output
checking, although not every glmark2 scene has a known validation result.

## Suggested acceptance matrix

1. **Frequency/performance:** fixed 300, 600, and 900 MHz; three 30-second
   off-screen glmark2 repetitions at each rate.
2. **Continuous soak:** one stress-ng GPU worker for 30 minutes, then two and
   four workers; monitor SCMI frequency, runtime status, temperature, and
   kernel errors.
3. **Runtime-PM cycling:** repeatedly stop and continue a single long-lived
   stress-ng process; require active rate to restore and idle rate to park at
   200 MHz on every cycle.
4. **Transition race:** while cycling runtime PM, alternate 300/900 MHz
   userspace requests from a second process for at least 100 cycles.
5. **Correctness/fault check:** require zero SError, GPU fault, timeout,
   devfreq failure, and call-trace messages; run glmark2 validation once at
   each boundary rate.

## Alternatives

Piglit and dEQP are primarily conformance/correctness suites and are much
heavier additions to a minimal Nerves image. `kmscube` is useful as a smoke
test but display presentation normally makes it unsuitable for an uncapped
frequency/performance comparison. CUDA-oriented tools such as `gpu-burn` do
not exercise Panfrost.
