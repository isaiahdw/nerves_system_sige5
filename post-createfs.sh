#!/bin/sh

set -e

FWUP_CONFIG=$NERVES_DEFCONFIG_DIR/fwup.conf

# Run the common post-image processing for nerves
$BR2_EXTERNAL_NERVES_PATH/board/nerves-common/post-createfs.sh $TARGET_DIR $FWUP_CONFIG

# Drop the previous build's portable artifact before this one's is written.
#
# The container build volume is buildroot's output directory, and every build
# adds a ~390 MB nerves_system_sige5-portable-*.tar.gz to it while removing
# none of the earlier ones. Left to itself that is unbounded: 104 tarballs and
# 39 GB once, then 7 more on a volume that had reached 100 GB on disk.
#
# Deleting them late does not lose anything. The artifact firmware builds
# actually consume is copied out to ~/.nerves/artifacts on the host, so the
# copies in here are duplicates from the moment they are made, and a build that
# fails after this point still leaves the host copy intact.
#
# BASE_DIR is the right place to look, from nerves' own build runner rather
# than from guessing: mounts/1 in nerves/artifact/build_runners/docker.ex binds
# the build volume at working_dir(), and copy_artifact/2 runs
# `cp <name> /nerves/dl/<name>` with a relative source, so `make system` writes
# the tarball into the working directory. That directory is also buildroot's
# output - it is what holds build/, host/, target/ and images/ - so BASE_DIR,
# the working directory and the volume root are all the same path.
#
# This only holds the line. The volume's backing file is sparse and grows to
# its high-water mark, and the guest's virtio-blk device has no discard
# support, so freed blocks are reused rather than returned - space already lost
# needs the volume deleted. See tools/prune-build-volume.sh.
BUILD_ROOT=${BASE_DIR:-${BINARIES_DIR:+$BINARIES_DIR/..}}
if [ -n "$BUILD_ROOT" ] && [ -d "$BUILD_ROOT" ]; then
    # Counted and announced rather than silent: this runs where nobody looks,
    # and a rename upstream would turn it into a no-op that says nothing.
    stale=$(find "$BUILD_ROOT" -maxdepth 1 -name 'nerves_system_*-portable-*.tar.gz' 2>/dev/null | wc -l | tr -d ' ')
    find "$BUILD_ROOT" -maxdepth 1 -name 'nerves_system_*-portable-*.tar.gz' -delete 2>/dev/null || true
    echo "post-createfs: removed $stale stale portable artifact(s) from $BUILD_ROOT"
else
    echo "post-createfs: no BASE_DIR/BINARIES_DIR, skipping artifact prune" >&2
fi
