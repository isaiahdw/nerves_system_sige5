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

# The bootloader and the trusted application have to come from the same build.
# scripts/build-uboot.sh checks the pair when it runs, but nothing re-checks it
# at firmware-build time, so an image can be assembled from a bootloader and a
# rootfs that were never built together - a TA with no secure world to load it,
# or a secure world with no TA. Check what is actually about to be packaged.
VARIANT_FILE=$NERVES_DEFCONFIG_DIR/uboot/u-boot-rockchip.variant
PKCS11_TA=$TARGET_DIR/lib/optee_armtz/fd02c9da-306c-48c7-a49c-bbd827ae86ee.ta
VARIANT=$(sed -n 's/^variant: *//p' "$VARIANT_FILE" 2>/dev/null)

if [ -f "$PKCS11_TA" ]; then HAVE_TA=yes; else HAVE_TA=no; fi

case "$VARIANT/$HAVE_TA" in
    secure-world/yes|plain/no)
        echo "post-build: bootloader is '$VARIANT', PKCS#11 TA present: $HAVE_TA"
        ;;
    secure-world/no)
        echo "BUILD FAILED: the bootloader carries a secure world but no PKCS#11 TA" >&2
        echo "  is in the image. Re-run scripts/build-uboot.sh." >&2
        exit 1
        ;;
    plain/yes)
        echo "BUILD FAILED: a PKCS#11 TA is in the image but the bootloader is" >&2
        echo "  '$VARIANT', so nothing will load it. It is also signed against a" >&2
        echo "  core this image does not carry. Remove" >&2
        echo "  rootfs_overlay/lib/optee_armtz/, or rebuild with SECURE_WORLD=1." >&2
        exit 1
        ;;
    *)
        echo "BUILD FAILED: cannot read the bootloader variant from" >&2
        echo "  $VARIANT_FILE" >&2
        exit 1
        ;;
esac
