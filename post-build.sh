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
# Read the bootloader itself, not the sidecar beside it. The .variant file is
# written by scripts/build-uboot.sh and can disagree with u-boot-rockchip.bin
# - a stale sidecar once caused this check to delete the TA from an image
# whose bootloader really did carry OP-TEE, which left the device with a
# secure world it could not talk to. The marker is the same string
# build-uboot.sh greps for when it verifies its own output.
BOOTLOADER=$NERVES_DEFCONFIG_DIR/uboot/u-boot-rockchip.bin
VARIANT_FILE=$NERVES_DEFCONFIG_DIR/uboot/u-boot-rockchip.variant
PKCS11_TA=$TARGET_DIR/lib/optee_armtz/fd02c9da-306c-48c7-a49c-bbd827ae86ee.ta

if [ ! -r "$BOOTLOADER" ]; then
    echo "BUILD FAILED: cannot read $BOOTLOADER." >&2
    echo "  It is what fwup packages; an unreadable one is not a plain one." >&2
    exit 1
fi

if grep -qa "HUK burn" "$BOOTLOADER"; then
    VARIANT=secure-world
else
    VARIANT=plain
fi

# The sidecar is documentation; disagreeing with the binary means one of them
# is stale and neither can be trusted to decide what ships.
CLAIMED=$(sed -n 's/^variant: *//p' "$VARIANT_FILE" 2>/dev/null)
if [ -n "$CLAIMED" ] && [ "$CLAIMED" != "$VARIANT" ]; then
    echo "BUILD FAILED: $VARIANT_FILE says '$CLAIMED' but" >&2
    echo "  u-boot-rockchip.bin is '$VARIANT'. Re-run scripts/build-uboot.sh" >&2
    echo "  so the bootloader and its record agree." >&2
    exit 1
fi

if [ -f "$PKCS11_TA" ]; then HAVE_TA=yes; else HAVE_TA=no; fi

case "$VARIANT/$HAVE_TA" in
    secure-world/yes)
        # Any secure bootloader beside any TA is not a pair. The manifest
        # records what scripts/build-uboot.sh actually built and verified, so
        # check both against it rather than against each other's existence.
        WANT_BL=$(sed -n 's/^bootloader-sha256: *//p' "$VARIANT_FILE")
        WANT_TA=$(sed -n 's/^ta-sha256: *//p' "$VARIANT_FILE")
        WANT_KEY=$(sed -n 's/^ta-pubkey-sha256: *//p' "$VARIANT_FILE")
        if [ -z "$WANT_BL" ] || [ -z "$WANT_TA" ] || [ -z "$WANT_KEY" ]; then
            echo "BUILD FAILED: $VARIANT_FILE records no digests." >&2
            echo "  Re-run scripts/build-uboot.sh to write a manifest." >&2
            exit 1
        fi
        GOT_BL=$(sha256sum "$BOOTLOADER" | cut -d' ' -f1)
        GOT_TA=$(sha256sum "$PKCS11_TA" | cut -d' ' -f1)
        if [ "$GOT_BL" != "$WANT_BL" ]; then
            echo "BUILD FAILED: the bootloader is not the one the manifest" >&2
            echo "  records. Re-run scripts/build-uboot.sh." >&2
            exit 1
        fi
        if [ "$GOT_TA" != "$WANT_TA" ]; then
            echo "BUILD FAILED: the PKCS#11 TA is not the one built with this" >&2
            echo "  bootloader; it was signed against a different core and" >&2
            echo "  will not load. Re-run scripts/build-uboot.sh." >&2
            exit 1
        fi
        # The signing key is reported, not re-checked. Its fingerprint cannot
        # be recomputed here - build-uboot.sh derives it from the signing key,
        # which is a temporary file or an HSM and is gone by now - and it would
        # add nothing if it could: the signature is inside the bytes ta-sha256
        # covers, so a TA signed by a different key already fails that compare.
        # Requiring the field rejects a manifest from a build-uboot.sh that
        # predates it, and printing it puts the image's signing identity in the
        # build log where it can be audited.
        echo "post-build: bootloader and TA match the manifest"
        echo "post-build: TA signed by key $(printf %s "$WANT_KEY" | cut -c1-16)..."
        ;;
    plain/no)
        echo "post-build: bootloader is 'plain', no PKCS#11 TA - consistent"
        ;;
    secure-world/no)
        echo "BUILD FAILED: the bootloader carries a secure world but no PKCS#11 TA" >&2
        echo "  is in the image. Re-run scripts/build-uboot.sh." >&2
        exit 1
        ;;
    plain/yes)
        # Not a failure to stop on: buildroot's target directory is
        # incremental, so a TA installed by an earlier secure-world build
        # outlives the overlay that put it there. It cannot be loaded by a
        # plain bootloader and cannot be right, so drop it - the same thing
        # scripts/build-uboot.sh does when it builds the plain variant.
        rm -f "$PKCS11_TA"
        rmdir "$(dirname "$PKCS11_TA")" 2>/dev/null || true
        echo "post-build: bootloader is '$VARIANT'; removed a stale PKCS#11 TA"
        ;;
    *)
        echo "BUILD FAILED: cannot determine the bootloader variant from" >&2
        echo "  $BOOTLOADER" >&2
        exit 1
        ;;
esac
