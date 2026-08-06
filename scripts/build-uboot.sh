#!/bin/sh
#
# Reproducibly build the committed U-Boot blob for the Sige5 from mainline
# U-Boot + the required Rockchip binary blobs (rkbin: DDR-init TPL and BL31 —
# there is no open-source DRAM init or BL31 for the RK3576), in a Docker
# container.
#
# Produces, in uboot/:
#   u-boot-rockchip.bin      The combined boot image (TPL/SPL idbloader at
#                            the file start, U-Boot FIT at 8 MB). fwup's
#                            `complete` task writes it at sector 64 on the
#                            eMMC, where the boot ROM expects it.
#
# The build adds the Nerves environment support on top of the stock
# sige5-rk3576_defconfig: env in MMC at 0xF00000, shared with
# fwup/nerves_runtime (see uboot/uboot.env and
# fwup_include/fwup-common.conf).
#
# Usage: scripts/build-uboot.sh
#
set -e

UBOOT_VERSION="v2026.01"
# rkbin source. rockchip-linux/rkbin is the official repo but has not been
# updated since 2026-06-11 and still ships BL32 v1.08, whose OP-TEE (3.13)
# has no PKCS#11 TA - probed on hardware, TEEC_OpenSession on
# fd02c9da-306c-48c7-a49c-bbd827ae86ee returns ITEM_NOT_FOUND. This fork
# carries a later Rockchip drop: BL32 v1.12, whose changelog fixes PKCS#11
# attribute handling (CKA_PUBLIC_KEY_INFO, CKA_SUBJECT), so the TA is there.
#
# It is third-party bytes running at S-EL1, which is a real trust step down
# from the official repo. Pinned by commit so the build is reproducible, and
# worth re-pointing at rockchip-linux/rkbin once it publishes v1.12.
RKBIN_REPO="https://github.com/flipperdevices/rkbin.git"
RKBIN_COMMIT="2e2961b363274470d8d805985af0dc1915e7d147"
DDR_BIN="bin/rk35/rk3576_ddr_lp4_2112MHz_lp5_2736MHz_v1.13.bin"
BL31_ELF="bin/rk35/rk3576_bl31_v1.25.elf"
# No BL32 from rkbin. Rockchip's RK3576TRUST.ini loads rk3576_bl32 next to
# BL31, and this used to be buildable with WITH_BL32=1, but there is no way to
# use it: the blob ships no PKCS#11 TA (measured - TEEC_OpenSession returns
# ITEM_NOT_FOUND on v1.08 and v1.12), is not a filesystem TA, and authoring one
# needs Rockchip's signing key. See docs/research/rk3576-firmware-versions.md,
# which also keeps the trust-ini load-address trap that route walked into.

# SECURE_WORLD=1 builds a bootloader with a secure world: upstream OP-TEE for
# PLATFORM=rockchip-rk3576 inside TF-A with SPD=opteed, replacing rkbin's BL31.
# The result is written to u-boot-rockchip.bin - the same file fwup packages -
# so a normal `mix firmware` and flash carries it, with no bootloader swapping.
#
# That image fuses a hardware unique key on the first boot of a part that has
# none, because a secure world without one cannot store anything. It only ever
# writes a blank slot, and only after the checks in optee/0011 pass. See
# docs/research/rk3576-secure-world.md.
#
# It also swaps out BL31. GPU rates are unaffected - the PVTPLL tables are
# identical and it was measured both ways - but that is the thing to re-check if
# clocks ever look wrong.
#
# SECURE_WORLD_DEBUG=1 adds read-only diagnostics: an OTP survey, a search for
# the secure TRNG, and a dry run reporting what a burn would do.
OPTEE_GIT="https://github.com/OP-TEE/optee_os.git"
OPTEE_COMMIT="5a53776"
TFA_GIT="https://github.com/ARM-software/arm-trusted-firmware.git"
# Pinned, like OP-TEE above: master moves, and a bootloader that cannot be
# rebuilt byte for byte is not much use for working out what is on a board.
TFA_COMMIT="9ad327a8d124ce82002614c23e33992d4de6f7cf"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

OUT_BIN="$REPO_DIR/uboot/u-boot-rockchip.bin"
BUILD_STAMP="$(mktemp)"
trap 'rm -f "$BUILD_STAMP"' EXIT

# Fed on stdin rather than as bash -c "...". In a double-quoted argument any
# unescaped quote below would end the string early, and the container would
# silently run a truncated script - building fine, copying nothing, exiting 0.
# In this heredoc quotes are just characters. Host expansion still happens, so
# \$ still defers a variable to the container.
# Pinned by digest for the same reason the sources are pinned by commit: the
# bookworm tag moves, and a toolchain that changes underneath makes the output
# unexplainable.
BUILD_IMAGE="debian:bookworm@sha256:813017f3d62be4b5891a7acca6a01bdcd4b8513daa81b1ab99d3a50385b26931"

docker run --rm -i -v "$REPO_DIR/uboot":/out -v "$REPO_DIR/optee":/optee-patches \
    "$BUILD_IMAGE" bash -s <<CONTAINER_SCRIPT
set -e
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git make gcc \
    gcc-aarch64-linux-gnu bison flex libssl-dev bc python3 python3-dev \
    python3-setuptools python3-pyelftools swig device-tree-compiler \
    libgnutls28-dev uuid-dev python3-cryptography >/dev/null

git clone --depth 1 --branch $UBOOT_VERSION https://source.denx.de/u-boot/u-boot.git /u-boot
# Partial clone. rkbin is a multi-GB binary repo and a full history download
# takes over an hour; blobs are fetched on demand at checkout instead, which
# pulls only the handful this build actually reads.
git clone --filter=blob:none $RKBIN_REPO /rkbin
git -C /rkbin checkout $RKBIN_COMMIT

if [ '${SECURE_WORLD:-0}' = 1 ]; then
    # OP-TEE first: TF-A needs tee.bin to package as BL32.
    git clone --filter=blob:none $OPTEE_GIT /optee_os
    git -C /optee_os checkout $OPTEE_COMMIT
    git -C /optee_os apply /optee-patches/*.patch
    make -C /optee_os PLATFORM=rockchip-rk3576 \
        CROSS_COMPILE64=aarch64-linux-gnu- \
        CFG_USER_TA_TARGETS=ta_arm64 \
        CFG_PKCS11_TA=y \
        CFG_RK3576_HUK_DRY_RUN=$([ "${SECURE_WORLD_DEBUG:-0}" = 1 ] && echo y || echo n) \
        CFG_RK3576_TRNG_S_PROBE=$([ "${SECURE_WORLD_DEBUG:-0}" = 1 ] && echo y || echo n) \
        CFG_RK3576_PERSIST_HUK=y \
        -j\$(nproc)

    git clone $TFA_GIT /tfa
    git -C /tfa checkout --detach $TFA_COMMIT
    make -C /tfa PLAT=rk3576 \
        CROSS_COMPILE=aarch64-linux-gnu- \
        SPD=opteed BL32=/optee_os/out/arm-plat-rockchip/core/tee.bin \
        -j\$(nproc)
fi

cd /u-boot
export ROCKCHIP_TPL=/rkbin/$DDR_BIN
if [ '${SECURE_WORLD:-0}' = 1 ]; then
    export BL31=/tfa/build/rk3576/release/bl31/bl31.elf
    export TEE=/optee_os/out/arm-plat-rockchip/core/tee.bin
    aarch64-linux-gnu-readelf -h \$BL31 | head -3
else
    export BL31=/rkbin/$BL31_ELF
fi

make sige5-rk3576_defconfig

# Nerves environment: stored on the SD card (mmc dev 0) at 15 MB, shared
# with fwup/nerves_runtime/fw_env.config.
cat >> .config <<'EOF'
CONFIG_ENV_IS_IN_MMC=y
CONFIG_ENV_OFFSET=0xF00000
CONFIG_ENV_SIZE=0x20000
CONFIG_SYS_MMC_ENV_DEV=0
EOF
make olddefconfig
grep -E 'CONFIG_ENV_IS|CONFIG_ENV_OFFSET|CONFIG_ENV_SIZE|CONFIG_SYS_MMC_ENV_DEV' .config

make -j\$(nproc) CROSS_COMPILE=aarch64-linux-gnu-

# One output name. fwup packages u-boot-rockchip.bin, so whichever variant was
# built is the one that ships - no second artifact and no swapping at flash
# time. uboot/u-boot-rockchip.variant records which it is, in a form git can
# show; a diff of the binary alone says only that binary files differ.

if [ '${SECURE_WORLD:-0}' = 1 ]; then
    cp u-boot-rockchip.bin /out/u-boot-rockchip.bin
    # The PKCS#11 TA is a filesystem TA, not an early one: OP-TEE loads it
    # through tee-supplicant from /lib/optee_armtz. It is signed with the key
    # the core was built with - ours - so it will be trusted. Export it so the
    # rootfs can install it.
    mkdir -p /out/optee-ta
    cp /optee_os/out/arm-plat-rockchip/export-ta_arm64/ta/*.ta /out/optee-ta/
else
    cp u-boot-rockchip.bin /out/
fi
CONTAINER_SCRIPT

# Check the artifact matches what was asked for. The container can succeed and
# still not produce what was intended - a truncated script, a copy that never
# ran, a stale file left in place - and every one of those exits 0. Compare the
# result against the request rather than trusting the exit code.
if [ ! -f "$OUT_BIN" ]; then
    echo "BUILD FAILED: $OUT_BIN was not produced" >&2
    exit 1
fi

if [ ! "$OUT_BIN" -nt "$BUILD_STAMP" ]; then
    echo "BUILD FAILED: $OUT_BIN was not written by this run." >&2
    echo "  The container exited 0 but left the previous binary in place." >&2
    exit 1
fi

# tee.bin only appears in the image when a secure world was built into it
if grep -qa "OP-TEE version" "$OUT_BIN"; then
    BUILT_SECURE=1
else
    BUILT_SECURE=0
fi

if [ "$BUILT_SECURE" != "$([ "${SECURE_WORLD:-0}" = 1 ] && echo 1 || echo 0)" ]; then
    echo "BUILD FAILED: asked for SECURE_WORLD=${SECURE_WORLD:-0}," >&2
    echo "  but the image $([ "$BUILT_SECURE" = 1 ] && echo does || echo does not) contain OP-TEE." >&2
    exit 1
fi

if [ "${SECURE_WORLD:-0}" = 1 ] && ! grep -qa "HUK burn" "$OUT_BIN"; then
    echo "BUILD FAILED: secure world built without the HUK provisioning path" >&2
    exit 1
fi

VARIANT_FILE="$REPO_DIR/uboot/u-boot-rockchip.variant"
if [ "${SECURE_WORLD:-0}" = 1 ]; then
    {
        echo "variant: secure-world"
        echo "built:   upstream TF-A + OP-TEE (PLATFORM=rockchip-rk3576, SPD=opteed)"
        echo "fuses:   yes - burns a HUK on first boot of an unprovisioned part"
        echo "debug:   ${SECURE_WORLD_DEBUG:-0}"
    } > "$VARIANT_FILE"
    echo
    echo "=== uboot/u-boot-rockchip.bin now carries a SECURE WORLD."
    echo "=== Booting it on a part with no HUK fuses one, permanently."
    echo "=== This is the file fwup packages, so any firmware built from this"
    echo "=== system will do that. See uboot/u-boot-rockchip.variant."
else
    {
        echo "variant: plain"
        echo "built:   rkbin BL31, no secure world"
        echo "fuses:   no"
    } > "$VARIANT_FILE"
    echo "=== done. uboot/u-boot-rockchip.bin updated (plain, no secure world)"
fi
