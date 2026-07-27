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
RKBIN_COMMIT="ecb4fcbe954edf38b3ae037d5de6d9f5bccf81f4"
DDR_BIN="bin/rk35/rk3576_ddr_lp4_2112MHz_lp5_2736MHz_v1.12.bin"
BL31_ELF="bin/rk35/rk3576_bl31_v1.24.elf"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

docker run --rm -v "$REPO_DIR/uboot":/out debian:bookworm bash -c "
set -e
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git make gcc \
    gcc-aarch64-linux-gnu bison flex libssl-dev bc python3 python3-dev \
    python3-setuptools python3-pyelftools swig device-tree-compiler \
    libgnutls28-dev uuid-dev >/dev/null

git clone --depth 1 --branch $UBOOT_VERSION https://source.denx.de/u-boot/u-boot.git /u-boot
git clone https://github.com/rockchip-linux/rkbin.git /rkbin
git -C /rkbin checkout $RKBIN_COMMIT

cd /u-boot
export ROCKCHIP_TPL=/rkbin/$DDR_BIN
export BL31=/rkbin/$BL31_ELF

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

cp u-boot-rockchip.bin /out/
"

echo "=== done. Updated uboot/u-boot-rockchip.bin"
