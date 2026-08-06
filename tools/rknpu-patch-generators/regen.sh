#!/bin/bash
# Regenerate every generated rknpu patch, in order, from the generators in this
# directory. Those generators are the source of truth: this script only reads
# them, so a patch edited by hand is reverted here rather than preserved.
#
# Each generator edits the tree its predecessors leave behind, and the patch is
# the diff of that edit, so hunk headers and context are never written by hand.
set -euo pipefail

GEN=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$GEN/../.." && pwd)
PD=$REPO/package/rknpu-driver
DL=${RKNPU_DL_DIR:-$HOME/.nerves/dl/rknpu-driver}
IMG=${NERVES_BR_IMAGE:-ghcr.io/nerves-project/nerves_system_br:1.34.0}

[ -d "$DL" ] || { echo "no driver sources at $DL (set RKNPU_DL_DIR)" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# $1 = last patch to apply before generating, $2 = generator, $3.. = files
gen() {
	local upto=$1 script=$2; shift 2
	local files=("$@") copy="" diffs="" base="" apply="" n

	# 0008 is generated against its own frozen base rather than a predecessor.
	# That base also carries a Makefile hunk, so the Makefile is staged too or
	# the patch applies only in part; it is not diffed, and the committed patch
	# keeps that hunk in its message half.
	if [ "$upto" = "0007" ]; then
		base='cp /tmp/w/Makefile /tmp/a/ && cp /tmp/w/Makefile /tmp/b/ && cd /tmp/b && patch -F0 -g0 -p1 -E -t -N -i /gen/0008-base.patch || exit 1; cd /tmp/w;'
	fi
	for f in "${files[@]}"; do
		copy="$copy cp $f /tmp/a/$(dirname "$f")/ || exit 1; cp $f /tmp/b/$(dirname "$f")/ || exit 1;"
		diffs="$diffs diff -u a/$f b/$f > /out/h_$(basename "$f").hunk;"
	done
	for n in 0001 0002 0003 0004 0005 0006 0007 0008 0009 0010 0011 0012 0013 0014 0015 0016 0017; do
		apply="$apply patch -F0 -g0 -p1 -E -t -N -i /pd/$n-*.patch >/tmp/p.log 2>&1 || { echo \"apply $n failed\"; head -20 /tmp/p.log; exit 1; };"
		[ "$n" = "$upto" ] && break
	done

	container run --rm -v "$PD":/pd -v "$WORK":/out -v "$DL":/dl -v "$GEN":/gen "$IMG" sh -c "
		set -u
		rm -rf /tmp/w && mkdir -p /tmp/w/include && cp /dl/* /tmp/w/
		cd /tmp/w && for h in rknpu_*.h; do mv \$h include/; done
		$apply
		rm -rf /tmp/a /tmp/b && mkdir -p /tmp/a/include /tmp/b/include
		$copy
		$base
		python3 /gen/$script.py || exit 1
		# diff exits 1 when files differ, which is the normal case here.
		cd /tmp && $diffs
		exit 0" || { echo "generation failed for $script" >&2; exit 1; }
	echo "  generated $script"
}

# Splice the fresh hunks into the patch, keeping its commit message.
place() {
	python3 - "$WORK" "$@" <<'PY'
import sys
work, patch = sys.argv[1], sys.argv[2]
pairs = [a.split('=') for a in sys.argv[3:]]
head = open(patch).read()
# Cut at the earliest section this call regenerates, not at the first section
# in the file: 0008 carries a Makefile hunk nothing regenerates, and it has to
# survive. Taking the minimum also makes the cut independent of the order the
# sections happen to be in.
cuts = [head.index(m) for m in ('--- a/%s\n' % f for f, _ in pairs) if m in head]
assert cuts, "no regenerated section found in " + patch
head = head[:min(cuts)]
body = ""
for f, hunk in pairs:
    d = "\n".join(open(work + "/" + hunk).read().split("\n")[2:])
    if not d.strip():
        continue
    body += "--- a/%s\n+++ b/%s\n%s" % (f, f, d)
open(patch, 'w').write(head + body)
PY
}

gen 0007 0008-devfreq rknpu_devfreq.c include/rknpu_drv.h include/rknpu_devfreq.h
place "$PD/0008-devfreq-mainline-implementation.patch" \
	rknpu_devfreq.c=h_rknpu_devfreq.c.hunk \
	include/rknpu_drv.h=h_rknpu_drv.h.hunk \
	include/rknpu_devfreq.h=h_rknpu_devfreq.h.hunk

gen 0009 0010-variant-opps rknpu_devfreq.c
place "$PD/0010-devfreq-select-opps-for-chip-variant.patch" \
	rknpu_devfreq.c=h_rknpu_devfreq.c.hunk

gen 0010 0011-scmi-gate rknpu_drv.c
place "$PD/0011-power-gate-scmi-on-driver-power-state.patch" \
	rknpu_drv.c=h_rknpu_drv.c.hunk

gen 0011 0012-power-lifecycle rknpu_drv.c rknpu_devfreq.c rknpu_debugger.c rknpu_gem.c include/rknpu_devfreq.h include/rknpu_drv.h
place "$PD/0012-power-transactional-lifecycle.patch" \
	rknpu_drv.c=h_rknpu_drv.c.hunk \
	rknpu_devfreq.c=h_rknpu_devfreq.c.hunk \
	rknpu_debugger.c=h_rknpu_debugger.c.hunk \
	rknpu_gem.c=h_rknpu_gem.c.hunk \
	include/rknpu_devfreq.h=h_rknpu_devfreq.h.hunk \
	include/rknpu_drv.h=h_rknpu_drv.h.hunk

gen 0012 0013-read-margin rknpu_devfreq.c rknpu_drv.c include/rknpu_drv.h
place "$PD/0013-devfreq-program-sram-read-margin.patch" \
	rknpu_devfreq.c=h_rknpu_devfreq.c.hunk \
	rknpu_drv.c=h_rknpu_drv.c.hunk \
	include/rknpu_drv.h=h_rknpu_drv.h.hunk

gen 0013 0014-load-metric rknpu_devfreq.c rknpu_drv.c include/rknpu_drv.h
place "$PD/0014-devfreq-measure-load-over-sampling-window.patch" \
	rknpu_devfreq.c=h_rknpu_devfreq.c.hunk \
	rknpu_drv.c=h_rknpu_drv.c.hunk \
	include/rknpu_drv.h=h_rknpu_drv.h.hunk

gen 0014 0015-dvfs-instrumentation rknpu_devfreq.c include/rknpu_devfreq.h rknpu_debugger.c
place "$PD/0015-debugfs-report-the-raw-dvfs-signal.patch" \
	rknpu_devfreq.c=h_rknpu_devfreq.c.hunk \
	include/rknpu_devfreq.h=h_rknpu_devfreq.h.hunk \
	rknpu_debugger.c=h_rknpu_debugger.c.hunk

gen 0015 0016-dvfs-demand-metric rknpu_devfreq.c include/rknpu_drv.h
place "$PD/0016-devfreq-report-demand-and-default-to-ondemand.patch" \
	rknpu_devfreq.c=h_rknpu_devfreq.c.hunk \
	include/rknpu_drv.h=h_rknpu_drv.h.hunk

gen 0016 0017-devfreq-event-driven-boost rknpu_devfreq.c rknpu_drv.c include/rknpu_drv.h include/rknpu_devfreq.h
place "$PD/0017-devfreq-raise-the-floor-when-work-arrives.patch" \
	rknpu_devfreq.c=h_rknpu_devfreq.c.hunk \
	rknpu_drv.c=h_rknpu_drv.c.hunk \
	include/rknpu_drv.h=h_rknpu_drv.h.hunk \
	include/rknpu_devfreq.h=h_rknpu_devfreq.h.hunk

echo "chain regenerated; run build-each.sh to check it applies and builds"
