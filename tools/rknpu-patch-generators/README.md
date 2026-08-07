# rknpu patch generators

The ten largest rknpu patches are generated, not hand-written:

| Patch | Generator | Edits the tree left by |
| --- | --- | --- |
| `0008-devfreq-mainline-implementation` | `0008-devfreq.py` | `0001`–`0007` plus `0008-base.patch` |
| `0010-devfreq-select-opps-for-chip-variant` | `0010-variant-opps.py` | `0001`–`0009` |
| `0011-power-gate-scmi-on-driver-power-state` | `0011-scmi-gate.py` | `0001`–`0010` |
| `0012-power-transactional-lifecycle` | `0012-power-lifecycle.py` | `0001`–`0011` |
| `0013-devfreq-program-sram-read-margin` | `0013-read-margin.py` | `0001`–`0012` |
| `0014-devfreq-measure-load-over-sampling-window` | `0014-load-metric.py` | `0001`–`0013` |
| `0015-debugfs-report-the-raw-dvfs-signal` | `0015-dvfs-instrumentation.py` | `0001`–`0014` |
| `0016-devfreq-report-demand-and-default-to-ondemand` | `0016-dvfs-demand-metric.py` | `0001`–`0015` |
| `0017-devfreq-raise-the-floor-when-work-arrives` | `0017-devfreq-event-driven-boost.py` | `0001`–`0016` |
| `0018-pin-the-module-for-anything-that-outlives-its-fd` | `0018-lifetime-pin.py` | `0001`–`0017` |

Each generator edits the tree its predecessors leave behind, and the patch is
the `diff -u` of that edit, so hunk headers and context are never written by
hand. That matters: hand-editing these repeatedly produced context that `patch`
accepted only with fuzz, which Buildroot's `-F0` then rejected.

`0008-base.patch` is the frozen original of `0008` - the devfreq rewrite before
the later rounds of fixes. `0008-devfreq.py` edits its output rather than
restating the whole file.

## Regenerating

    ./regen.sh

It is self-contained and repo-relative. `RKNPU_DL_DIR` overrides where the
driver sources come from (default `~/.nerves/dl/rknpu-driver`) and
`NERVES_BR_IMAGE` the container image.

The generators in this directory are the source of truth. `regen.sh` only reads
them and rewrites the patches, so an edit made to a `.patch` by hand is reverted
the next time it runs, not preserved.

Two details of the layout it handles. The download cache is flat but the driver
expects its headers under `include/`, so they are copied there before any patch
is applied - copying to both places instead lets a stale flat header shadow the
patched one. And `0008-base.patch` also carries a `Makefile` hunk, so the
`Makefile` is staged alongside the sources or that hunk applies to nothing; it
is not diffed, and the committed patch keeps it in its message half.

## Checking

    ./build-each.sh

Applies each patch in turn and compiles the module after each one, which is what
keeps the series bisectable. It stops at the first patch that will not apply or
will not build. Module options come from `rknpu-driver.mk` rather than being
restated, because a stale copy of them changes what compiles: without
`CONFIG_ROCKCHIP_RKNPU_DRM_GEM` the build fails at `modpost` instead of at the
compiler.

`0001` to `0003` do not build on their own - the compat headers arrive in `0002`
and the DRM and IOMMU API updates in `0003` and `0004` - so building starts at
`0004`, which `FIRST_BUILDABLE` overrides. Those three are still required to
apply.

To check the series against a pristine kernel rather than the build tree, which
already has the patches applied, extract from `~/.nerves/dl/linux/`.

## Two traps

Anything that has to survive belongs in the generator. Editing a `.patch`
directly works until the next regeneration silently discards it - that has
already cost one build cycle, when a `hw_powered` field added by hand
disappeared.

C escapes need doubling, and the count depends on how many Python literals the
text passes through. Writing C from a generator needs `"...%d\\n"`, because the
generator's own string turns `\n` into a newline. Editing a generator *from
another script* needs `"...%d\\\\n"` for the same reason one layer up - that has
broken the build twice. A regex sweep cannot tell a broken escape from a real
line break in the same literal, so compiling is the only reliable check.
