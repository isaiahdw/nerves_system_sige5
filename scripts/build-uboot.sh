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

# USE_OPENSOURCE_TEE=1 replaces both Rockchip blobs with upstream: OP-TEE built
# for PLATFORM=rockchip-rk3576, inside TF-A built with SPD=opteed. It is the
# only combination that gives both a PKCS#11 TA and a per-device key. Upstream
# has PKCS#11 but no HUK for this SoC until the patches in optee/ are applied;
# Rockchip's blob has the OTP-backed key machinery and no PKCS#11 to reach it
# with, which is why it is not an option here.
#
# Opt-in, and unset by default, because it swaps out BL31 as well. Every GPU
# measurement in docs/research rests on Rockchip's BL31 owning the PVTPLL, and
# upstream TF-A is a different implementation of that.
#
# Upstream puts TZDRAM at 0x70000000/32 MB and SHM at 0x72000000/4 MB
# (plat-rockchip conf.mk). linux/0022 reserves those, and the 0x48400000 region
# a Rockchip BL32 would have used, so one kernel boots either bootloader.

# HUK_DRY_RUN=1 reports what fusing a HUK would write, and where, without
# writing it - see optee/0008. The OTP write path has never executed on this
# SoC and its first run would be permanent, so the address and the
# preconditions are worth seeing on a console first.
OPTEE_GIT="https://github.com/OP-TEE/optee_os.git"
OPTEE_COMMIT="5a53776"
TFA_GIT="https://github.com/ARM-software/arm-trusted-firmware.git"
TFA_BRANCH="master"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

docker run --rm -v "$REPO_DIR/uboot":/out -v "$REPO_DIR/optee":/optee-patches \
    debian:bookworm bash -c "
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

if [ '${USE_OPENSOURCE_TEE:-0}' = 1 ]; then
    # OP-TEE first: TF-A needs tee.bin to package as BL32.
    git clone --filter=blob:none $OPTEE_GIT /optee_os
    git -C /optee_os checkout $OPTEE_COMMIT
    git -C /optee_os apply /optee-patches/*.patch
    make -C /optee_os PLATFORM=rockchip-rk3576 \
        CROSS_COMPILE64=aarch64-linux-gnu- \
        CFG_USER_TA_TARGETS=ta_arm64 \
        CFG_PKCS11_TA=y \
        CFG_RK3576_HUK_DRY_RUN=$([ "${HUK_DRY_RUN:-0}" = 1 ] && echo y || echo n) \
        CFG_RK3576_TRNG_S_PROBE=$([ "${TRNG_S_PROBE:-0}" = 1 ] && echo y || echo n) \
        CFG_RK3576_PERSIST_HUK=$([ "${PERSIST_HUK:-0}" = 1 ] && echo y || echo n) \
        -j\$(nproc)

    git clone --depth 1 --branch $TFA_BRANCH $TFA_GIT /tfa
    make -C /tfa PLAT=rk3576 \
        CROSS_COMPILE=aarch64-linux-gnu- \
        SPD=opteed BL32=/optee_os/out/arm-plat-rockchip/core/tee.bin \
        -j\$(nproc)
fi

cd /u-boot
export ROCKCHIP_TPL=/rkbin/$DDR_BIN
if [ '${USE_OPENSOURCE_TEE:-0}' = 1 ]; then
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

# Deliberately not u-boot-rockchip.bin: fwup packages that name, and a
# bootloader carrying a secure world should not be picked up by simply having
# been built. Enabling it is a rename, on purpose.
if [ '${USE_OPENSOURCE_TEE:-0}' = 1 ]; then
    cp u-boot-rockchip.bin /out/u-boot-rockchip-ostee.bin
    # The PKCS#11 TA is a filesystem TA, not an early one: OP-TEE loads it
    # through tee-supplicant from /lib/optee_armtz. It is signed with the key
    # the core was built with - ours - so it will be trusted. Export it so the
    # rootfs can install it.
    mkdir -p /out/optee-ta
    cp /optee_os/out/arm-plat-rockchip/export-ta_arm64/ta/*.ta /out/optee-ta/
else
    cp u-boot-rockchip.bin /out/
fi
"

if [ "${USE_OPENSOURCE_TEE:-0}" = 1 ]; then
    echo "=== done. Wrote uboot/u-boot-rockchip-ostee.bin (upstream TF-A + OP-TEE)"
else
    echo "=== done. Updated uboot/u-boot-rockchip.bin"
fi
