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
# No BL32. Rockchip's RK3576TRUST.ini loads rk3576_bl32 (OP-TEE)
# next to BL31. Nothing here needs it: the NPU hangs that were once blamed
# on its absence turned out to be PVTPLL state not surviving a power domain
# cycle, fixed in the driver (see package/rknpu-driver/0008).
#
# It is left out, not impossible. Two routes exist if a secure world is ever
# wanted - for key storage, RPMB, or secure boot:
#
#   - Rockchip's blob has the useful security features (OEM OTP key, key
#     ladder, RPMB). binman rejects it directly - that path takes an ELF, or
#     a binary carrying an optee_v1_header, and rkbin's blob has neither - so
#     WITH_BL32=1 below wraps it in an ELF at the RK3576TRUST.ini address.
#   - Upstream optee_os does have a plat-rockchip rk3576 (platform_rk3576.c)
#     and builds to an ELF, so it packages cleanly - but that file only sets
#     up the DDR firewall. With no tee_otp_get_hw_unique_key() behind it,
#     OP-TEE falls back to its built-in default HUK. RPMB derives its
#     authentication key from that HUK, so secure storage would be keyed on a
#     constant published in public source. Rockchip's blob is the one with
#     real OTP backing, which is why WITH_BL32 uses it.
#
# WITH_BL32=1 is opt-in and unset by default: a secure world nothing has
# tested yet has no business in a firmware image. Before shipping it, the
# kernel needs a reserved-memory node covering BL32_ADDR - rkbin blobs
# publish no reservation, and Linux will otherwise allocate over OP-TEE's
# DRAM and crash.
BL32_BIN="bin/rk35/rk3576_bl32_v1.12.bin"
# DRAM base (0x40000000, CONFIG_SYS_SDRAM_BASE) plus the 0x8400000 that
# RKTRUST/RK3576TRUST.ini gives as [BL32_OPTION] ADDR. That value is an
# offset, not an address: Rockchip's own fit_args.sh adds the base to it,
#
#   -t) TEE_LOAD_ADDR=$2
#       # Compatible leagcy: Offset
#       if ((TEE_LOAD_ADDR < DRAM_BASE)); then
#               TEE_LOAD_ADDR="0x"$(echo "obase=16;$((DRAM_BASE+$2))"|bc)
#
# Taking it literally puts the blob below the start of RAM - this board's
# System RAM begins at 0x40200000 - and SPL hangs mid-FIT trying to copy the
# TEE there, after verifying u-boot, atf-2 and atf-3 and before BL31 runs.
BL32_ADDR="0x48400000"

# USE_OPENSOURCE_TEE=1 replaces both Rockchip blobs with upstream: OP-TEE built
# for PLATFORM=rockchip-rk3576, inside TF-A built with SPD=opteed. It is the
# only combination that can give both a PKCS#11 TA and a per-device key -
# Rockchip's BL32 has the OTP-backed HUK but no PKCS#11 (probed on hardware:
# TEEC_OpenSession on the PKCS#11 UUID returns ITEM_NOT_FOUND on v1.08 and
# v1.12), while upstream has PKCS#11 but no HUK for this SoC until the patch
# in optee/ is applied.
#
# Opt-in, and unset by default, because it swaps out BL31 as well. Every GPU
# measurement in docs/research rests on Rockchip's BL31 owning the PVTPLL, and
# upstream TF-A is a different implementation of that.
#
# The layout differs from the blob's: upstream puts TZDRAM at 0x70000000/32 MB
# and SHM at 0x72000000/4 MB (plat-rockchip conf.mk), where the blob uses
# 0x48400000 and 14 MB. The reserved-memory node in linux/0022 describes the
# blob's; it has to change with this.

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

if [ '${WITH_BL32:-0}' = 1 ] && [ '${USE_OPENSOURCE_TEE:-0}' != 1 ]; then
    # Wrap the raw blob so binman will take it: objcopy it into an ELF whose
    # single loadable segment sits at the address BL31 expects. Upstream
    # OP-TEE needs none of this - its tee.bin already carries a header binman
    # accepts - so this is skipped when USE_OPENSOURCE_TEE is set.
    cat > /tee.ld <<'LDEOF'
OUTPUT_FORMAT(\"elf64-littleaarch64\", \"elf64-littleaarch64\", \"elf64-littleaarch64\")
OUTPUT_ARCH(aarch64)
ENTRY(_start)
SECTIONS
{
	. = $BL32_ADDR;
	_start = .;
	.text : { *(.data) }
}
LDEOF
    aarch64-linux-gnu-objcopy -B aarch64 -I binary -O elf64-littleaarch64 \\
        --rename-section .data=.text,alloc,load,readonly,code,contents \\
        /rkbin/$BL32_BIN /bl32.o
    aarch64-linux-gnu-ld -T /tee.ld /bl32.o -o /tee.elf
    aarch64-linux-gnu-readelf -l /tee.elf | grep -A1 LOAD
    export TEE=/tee.elf
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

# Deliberately not u-boot-rockchip.bin for either secure-world build: fwup
# packages that name, and a bootloader carrying an untested secure world
# should not be picked up by simply having been built. Enabling one is a
# rename, on purpose.
if [ '${USE_OPENSOURCE_TEE:-0}' = 1 ]; then
    cp u-boot-rockchip.bin /out/u-boot-rockchip-ostee.bin
    # The PKCS#11 TA is a filesystem TA, not an early one: OP-TEE loads it
    # through tee-supplicant from /lib/optee_armtz. It is signed with the key
    # the core was built with - ours, on this path - so it will be trusted,
    # unlike anything we could sign for Rockchip's blob. Export it so the
    # rootfs can install it.
    mkdir -p /out/optee-ta
    cp /optee_os/out/arm-plat-rockchip/export-ta_arm64/ta/*.ta /out/optee-ta/
elif [ '${WITH_BL32:-0}' = 1 ]; then
    cp u-boot-rockchip.bin /out/u-boot-rockchip-bl32.bin
else
    cp u-boot-rockchip.bin /out/
fi
"

if [ "${USE_OPENSOURCE_TEE:-0}" = 1 ]; then
    echo "=== done. Wrote uboot/u-boot-rockchip-ostee.bin (upstream TF-A + OP-TEE)"
elif [ "${WITH_BL32:-0}" = 1 ]; then
    echo "=== done. Wrote uboot/u-boot-rockchip-bl32.bin (Rockchip BL31 + BL32)"
else
    echo "=== done. Updated uboot/u-boot-rockchip.bin"
fi
