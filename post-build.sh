#!/bin/sh

set -e

# Create the fwup ops script for on-device firmware operations (revert,
# validate, factory-reset, status)
# NOTE: revert.fw is the previous, more limited version of this. ops.fw is
#       backwards compatible.
mkdir -p $TARGET_DIR/usr/share/fwup
$HOST_DIR/usr/bin/fwup -c -f $NERVES_DEFCONFIG_DIR/fwup-ops.conf -o $TARGET_DIR/usr/share/fwup/ops.fw
ln -sf ops.fw $TARGET_DIR/usr/share/fwup/revert.fw

# Copy the fwup includes to the images dir
cp -rf $NERVES_DEFCONFIG_DIR/fwup_include $BINARIES_DIR

# Mesa is built with -Ddraw-use-llvm=false (see external.mk), so nothing
# should link libLLVM at runtime; Buildroot's Kconfig still forces the
# target LLVM package in. Remove the orphaned libraries from the rootfs,
# gated on proof that no ELF in the image NEEDs them. If libLLVM is
# genuinely needed the build fails here rather than silently shipping
# ~90 MB of dead weight (or silently breaking GL).
READELF=$HOST_DIR/bin/aarch64-nerves-linux-gnu-readelf
prune_orphan_lib() {
    users=$(find $TARGET_DIR -type f -name "*.so*" ! -name "$1*" \
        -exec sh -c "$READELF -d \"\$0\" 2>/dev/null | grep -q 'NEEDED.*$1'" {} \; -print)
    if [ -z "$users" ]; then
        rm -f $TARGET_DIR/usr/lib/$1*
        echo "post-build: removed orphaned $1 from target"
    else
        echo "post-build: $1 still needed by: $users"
        return 1
    fi
}
# Order matters: libLTO/libRemarks are LLVM's own companion libraries
# (installed by the same package) and link libLLVM themselves.
prune_orphan_lib libSPIRV-LLVM-Translator
prune_orphan_lib libRemarks
prune_orphan_lib libLTO
prune_orphan_lib libclang-cpp
prune_orphan_lib libclang
prune_orphan_lib libLLVM
