#!/bin/bash
# Delete old portable-artifact tarballs from the Nerves build volume.
#
# Every system build writes nerves_system_sige5-portable-<version>-<hash>.tar.gz
# into the build volume and nothing removes the previous one. They are ~390 MB
# each, so a few weeks of iteration is tens of gigabytes: 104 of them, 39 GB,
# had accumulated when this was written.
#
# The volume's backing file is sparse and grows to its high-water mark, and the
# guest's virtio-blk device does not support discard - fstrim returns
# "FITRIM ioctl failed: Operation not permitted" even with CAP_SYS_ADMIN. So
# deleting files inside does not shrink the file on the host; it frees blocks
# for the next build to reuse, which is what stops it growing. Reclaiming the
# high-water mark means deleting the volume and rebuilding from scratch.
#
#     tools/prune-build-volume.sh          # keep the 3 newest
#     KEEP=1 tools/prune-build-volume.sh   # keep only the newest
set -euo pipefail

KEEP=${KEEP:-3}
IMG=${NERVES_BR_IMAGE:-ghcr.io/nerves-project/nerves_system_br:1.34.0}
VOL=${NERVES_BUILD_VOLUME:-$(container volume ls 2>/dev/null |
	awk '$1 ~ /^nerves_system_sige5-/ && $1 !~ /-platform$/ { print $1; exit }')}

[ -n "$VOL" ] || { echo "no build volume found (set NERVES_BUILD_VOLUME)" >&2; exit 1; }
echo "volume: $VOL, keeping $KEEP newest"

container run --rm -v "$VOL":/work "$IMG" sh -c '
set -u
keep=$1
cd /work || exit 0
before=$(df -h /work | awk "NR==2 {print \$3}")
total=$(ls -t nerves_system_*-portable-*.tar.gz 2>/dev/null | wc -l)
ls -t nerves_system_*-portable-*.tar.gz 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
after=$(df -h /work | awk "NR==2 {print \$3}")
removed=$((total > keep ? total - keep : 0))
echo "removed $removed of $total artifacts; $before used -> $after used"
' _ "$KEEP"
