#!/bin/bash
# Compile the rknpu module after each patch, so the series stays bisectable.
#
# Stops at the first patch that will not apply or will not build. The module
# options come from rknpu-driver.mk rather than being restated here: dropping
# CONFIG_ROCKCHIP_RKNPU_DRM_GEM turns a working series into modpost link
# failures, so a copy that drifts out of date reports success wrongly.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PD=$REPO/package/rknpu-driver
DL=${RKNPU_DL_DIR:-$HOME/.nerves/dl/rknpu-driver}
IMG=${NERVES_BR_IMAGE:-ghcr.io/nerves-project/nerves_system_br:1.34.0}
VOL=${NERVES_BUILD_VOLUME:-$(container volume ls 2>/dev/null |
	awk '$1 ~ /^nerves_system_sige5-/ && $1 !~ /-platform$/ { print $1; exit }')}

[ -d "$DL" ] || { echo "no driver sources at $DL (set RKNPU_DL_DIR)" >&2; exit 1; }
[ -n "$VOL" ] || { echo "no build volume found (set NERVES_BUILD_VOLUME)" >&2; exit 1; }

# RKNPU_DRIVER_MODULE_MAKE_OPTS, joined onto one line.
OPTS=$(sed -n '/^RKNPU_DRIVER_MODULE_MAKE_OPTS[[:space:]]*=/,/[^\\]$/p' "$PD/rknpu-driver.mk" |
	sed -e 's/^RKNPU_DRIVER_MODULE_MAKE_OPTS[[:space:]]*=//' -e 's/\\$//' |
	tr -d '\t' | tr '\n' ' ')
[ -n "${OPTS// /}" ] || { echo "no module options found in rknpu-driver.mk" >&2; exit 1; }

# The shipped configuration is the one in the .mk, and it stays the default so
# a drifted copy cannot report success wrongly. RKNPU_OPTS overrides it to
# check a configuration this system does not ship but Kconfig still offers -
# the DMA-heap half, which selects a different set of objects:
#
#   RKNPU_OPTS='CONFIG_ROCKCHIP_RKNPU=m CONFIG_ROCKCHIP_RKNPU_DMA_HEAP=y' \
#       FIRST_BUILDABLE=0012 ./build-each.sh
OPTS=${RKNPU_OPTS:-$OPTS}

echo "volume:  $VOL"
echo "options: $OPTS"

# 0001-0003 are one unit: the compat headers arrive in 0002 and the DRM and
# IOMMU API updates in 0003 and 0004, so none of them compiles alone. They are
# still required to apply; building starts at the first patch that can.
FIRST_BUILDABLE=${FIRST_BUILDABLE:-0004}

INNER='
set -u
opts=$1
first=$2
K=$(ls -d /work/build/linux-* 2>/dev/null | head -1)
[ -n "$K" ] || { echo "no kernel tree in the build volume" >&2; exit 1; }

# Mirror RKNPU_DRIVER_EXTRACT_CMDS: sources flat, headers only under include/.
# Copying headers to both places lets a stale flat copy shadow the patched one.
rm -rf /tmp/w && mkdir -p /tmp/w/include
cp /dl/*.c /dl/Kconfig /dl/Makefile /tmp/w/
cp /dl/*.h /tmp/w/include/
cd /tmp/w

for p in /pd/00*.patch; do
	n=$(basename "$p" | cut -c1-4)
	if ! patch -F0 -g0 -p1 -E -t -N -i "$p" >/tmp/patch.log 2>&1; then
		echo "$n WILL NOT APPLY"
		head -20 /tmp/patch.log
		exit 1
	fi
	if [ "$n" \< "$first" ]; then
		echo "$n applied (not built alone)"
		continue
	fi
	if out=$(make -C "$K" M=/tmp/w ARCH=arm64 \
			CROSS_COMPILE=/work/host/bin/aarch64-nerves-linux-gnu- \
			$opts modules 2>&1); then
		echo "$n ok"
	else
		echo "$n FAILS TO BUILD"
		printf "%s\n" "$out" | grep -iE "error|undefined" | head -10
		exit 1
	fi
done
echo "all patches apply; every patch from $first builds"
'

container run --rm \
	-v "$PD":/pd -v "$DL":/dl -v "$VOL":/work \
	"$IMG" sh -c "$INNER" _ "$OPTS" "$FIRST_BUILDABLE"
