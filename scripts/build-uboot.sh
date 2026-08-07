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
# The tag is what the clone asks for; this is what it has to resolve to. A tag
# can be moved, a commit cannot.
UBOOT_COMMIT="127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3"
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
# Full SHA: a short one is ambiguous, and this commit decides both what the
# secure world does and what sign_encrypt.py below is checked against.
OPTEE_COMMIT="5a5377616c67d88a63f2724637dffc1d854b48df"
# sha256 of scripts/sign_encrypt.py at that commit. It runs on this machine, as
# the user who owns the TA private key.
OPTEE_SIGNER_SHA256="9f4b2d91541518ee4900ec4339db00f3c9cec2419edefc91bda102e68946642d"
TFA_GIT="https://github.com/ARM-software/arm-trusted-firmware.git"
# Pinned, like OP-TEE above: master moves, and a bootloader that cannot be
# rebuilt byte for byte is not much use for working out what is on a board.
TFA_COMMIT="9ad327a8d124ce82002614c23e33992d4de6f7cf"

# The key OP-TEE embeds to decide which trusted applications it will load.
# It must not live in this repository: whoever holds the private half can sign
# a TA carrying the PKCS#11 UUID, and OP-TEE derives a TA's secure-storage key
# from the HUK and that UUID - so such a TA reads the real one's stored objects,
# the device key included. OP-TEE's built-in default key is published in their
# repository, which is why it cannot be used here.
TA_KEY="${TA_KEY:-$HOME/.config/nerves_system_sige5/ta-sign.pem}"
PKCS11_UUID="fd02c9da-306c-48c7-a49c-bbd827ae86ee"

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

if [ "${SECURE_WORLD:-0}" = 1 ] && [ ! -f "$TA_KEY" ]; then
    cat >&2 <<MISSING_KEY
BUILD REFUSED: no TA signing key at $TA_KEY

A secure world needs its own key for signing trusted applications. Without one
OP-TEE embeds its published development key, and anyone can then sign a TA that
reads this device's stored keys.

Make one, keep it off devices and out of this repository:

    mkdir -p "\$(dirname "$TA_KEY")"
    openssl genrsa -out "$TA_KEY" 2048
    chmod 600 "$TA_KEY"

Back it up. Losing it means a rebuilt core will not load the TAs already
deployed, and every device has to be reflashed with a matched pair.
MISSING_KEY
    exit 1
fi

# Only the public half goes into the container. The container installs
# packages, clones repositories and runs their build systems, all with the
# network up; a private key mounted there is readable by every one of them,
# and read-only stops modification, not exfiltration. OP-TEE builds against
# TA_PUBLIC_KEY and emits a digest, which is signed out here and stitched back.
TA_PUBKEY=""
if [ "${SECURE_WORLD:-0}" = 1 ]; then
    TA_PUBKEY="$(mktemp -t ta-pub.XXXXXX)"
    trap 'rm -f "$TA_PUBKEY"' EXIT
    openssl rsa -in "$TA_KEY" -pubout -out "$TA_PUBKEY" 2>/dev/null
fi

TA_KEY_MOUNT=""
[ -n "$TA_PUBKEY" ] && TA_KEY_MOUNT="-v $TA_PUBKEY:/ta-public.pem:ro"

docker run --rm -i -v "$REPO_DIR/uboot":/out -v "$REPO_DIR/optee":/optee-patches \
    -v "$REPO_DIR/rootfs_overlay":/overlay \
    $TA_KEY_MOUNT "$BUILD_IMAGE" bash -s <<CONTAINER_SCRIPT
set -e
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git make gcc \
    gcc-aarch64-linux-gnu bison flex libssl-dev bc python3 python3-dev \
    python3-setuptools python3-pyelftools swig device-tree-compiler \
    libgnutls28-dev uuid-dev python3-cryptography >/dev/null

git clone --depth 1 --branch $UBOOT_VERSION https://source.denx.de/u-boot/u-boot.git /u-boot
UBOOT_HEAD=\$(git -C /u-boot rev-parse HEAD)
if [ "\$UBOOT_HEAD" != "$UBOOT_COMMIT" ]; then
    echo "BUILD FAILED: $UBOOT_VERSION resolves to \$UBOOT_HEAD," >&2
    echo "  not the pinned $UBOOT_COMMIT. The tag moved." >&2
    exit 1
fi
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
    # TA_PUBLIC_KEY is what the core embeds and checks against. The private
    # half is not here, so the TA it builds carries a placeholder signature
    # and is re-signed outside this container.
    make -C /optee_os PLATFORM=rockchip-rk3576 \
        CROSS_COMPILE64=aarch64-linux-gnu- \
        CFG_USER_TA_TARGETS=ta_arm64 \
        CFG_PKCS11_TA=y \
        TA_PUBLIC_KEY=/ta-public.pem \
        CFG_RK3576_OTP_SURVEY=$([ "${SECURE_WORLD_DEBUG:-0}" = 1 ] && echo y || echo n) \
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
    # through tee-supplicant from /lib/optee_armtz, and verifies its signature
    # against the key embedded in the core. Install the TA this core was built
    # with, so the two always match - a core and a TA from different builds
    # cannot load each other.
    mkdir -p /out/optee-ta /overlay/lib/optee_armtz
    cp /optee_os/out/arm-plat-rockchip/export-ta_arm64/ta/*.ta /out/optee-ta/
    # Everything the signing step outside needs: the unsigned TA and the
    # script that produces and stitches its digest. The stripped ELF is in the
    # TA's own build directory rather than the export tree, so find it - and
    # fail here if it is missing, because signing cannot proceed without it.
    cp /optee_os/scripts/sign_encrypt.py /out/optee-ta/
    STRIPPED=\$(find /optee_os/out -name "$PKCS11_UUID.stripped.elf" | head -1)
    if [ -z "\$STRIPPED" ]; then
        echo "the PKCS#11 TA's stripped ELF was not built" >&2
        exit 1
    fi
    cp "\$STRIPPED" /out/optee-ta/
    # The signed copy is installed outside, once the private key has been
    # applied to it.
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

# Sign the TA out here, where the private key is. The container built it
# against the public half and left a placeholder signature; this replaces that
# with a real one.
#
# Only openssl is handed the key, but everything below runs as the user who
# owns it and so could read it. That is why the inputs are pinned: the OP-TEE
# commit by full SHA, sign_encrypt.py against OPTEE_SIGNER_SHA256, and the
# Python it needs by hash from scripts/signing-requirements.txt. Creating the
# venv is the one step here that uses the network, and --require-hashes is what
# bounds it.
if [ "${SECURE_WORLD:-0}" = 1 ]; then
    TA_WORK="$REPO_DIR/uboot/optee-ta"
    UNSIGNED="$TA_WORK/$PKCS11_UUID.stripped.elf"
    SIGNER="$TA_WORK/sign_encrypt.py"

    if [ ! -f "$UNSIGNED" ] || [ ! -f "$SIGNER" ]; then
        echo "BUILD FAILED: the container did not export what signing needs" >&2
        exit 1
    fi

    # This script came out of a repository cloned over the network and is about
    # to run beside the private key. Check it is the one that was reviewed.
    GOT_SHA=$(shasum -a 256 "$SIGNER" | cut -d' ' -f1)
    if [ "$GOT_SHA" != "$OPTEE_SIGNER_SHA256" ]; then
        echo "BUILD FAILED: sign_encrypt.py does not match the pinned hash." >&2
        echo "  expected $OPTEE_SIGNER_SHA256" >&2
        echo "  got      $GOT_SHA" >&2
        echo "  Refusing to run it next to $TA_KEY. If OPTEE_COMMIT was" >&2
        echo "  bumped, review the new script and update OPTEE_SIGNER_SHA256." >&2
        exit 1
    fi

    # OP-TEE's signing script needs python cryptography. Keep it in a venv of
    # its own rather than the build container, and install it by hash: an
    # unpinned install puts whatever PyPI serves today next to the private key.
    # The recorded requirements hash rebuilds the venv when the pins move.
    VENV="$HOME/.config/nerves_system_sige5/signing-venv"
    REQS="$REPO_DIR/scripts/signing-requirements.txt"
    REQS_SHA=$(shasum -a 256 "$REQS" | cut -d' ' -f1)

    if [ ! -x "$VENV/bin/python3" ] ||
       [ "$(cat "$VENV/.requirements-sha256" 2>/dev/null)" != "$REQS_SHA" ]; then
        echo "=== creating a signing venv in $VENV"
        rm -rf "$VENV"
        python3 -m venv "$VENV"
        # --only-binary bars source distributions: a hashed sdist still runs
        # its own build backend here, beside the private key. --no-deps keeps
        # pip to the closure the file already pins.
        "$VENV/bin/pip" install -q --require-hashes --only-binary=:all: \
            --no-deps -r "$REQS" || {
            echo "BUILD FAILED: could not install the pinned signing dependencies" >&2
            exit 1
        }
        echo "$REQS_SHA" > "$VENV/.requirements-sha256"
    fi
    PY="$VENV/bin/python3"

    "$PY" "$SIGNER" digest --uuid "$PKCS11_UUID" \
        --in "$UNSIGNED" --key "$TA_PUBKEY" --dig "$TA_WORK/digest" >/dev/null

    base64 -d < "$TA_WORK/digest" > "$TA_WORK/digest.bin"
    openssl pkeyutl -sign -inkey "$TA_KEY" -in "$TA_WORK/digest.bin" \
        -pkeyopt digest:sha256 \
        -pkeyopt rsa_padding_mode:pss -pkeyopt rsa_pss_saltlen:digest \
        -out "$TA_WORK/sig.bin"
    base64 < "$TA_WORK/sig.bin" > "$TA_WORK/sig"

    "$PY" "$SIGNER" stitch --uuid "$PKCS11_UUID" \
        --in "$UNSIGNED" --key "$TA_PUBKEY" --sig "$TA_WORK/sig" \
        --out "$REPO_DIR/rootfs_overlay/lib/optee_armtz/$PKCS11_UUID.ta" >/dev/null

    rm -f "$TA_WORK/digest" "$TA_WORK/digest.bin" "$TA_WORK/sig" "$TA_WORK/sig.bin"

    # Prove the stitched TA verifies against the same public half the core was
    # built with. Everything above can succeed and still produce something the
    # secure world refuses at load time, where the only symptom is a TA that
    # will not start.
    if ! "$PY" "$SIGNER" verify --uuid "$PKCS11_UUID" \
            --in "$REPO_DIR/rootfs_overlay/lib/optee_armtz/$PKCS11_UUID.ta" \
            --key "$TA_PUBKEY" >/dev/null 2>&1; then
        echo "BUILD FAILED: the stitched TA does not verify against the key" >&2
        echo "  this core embeds. It would not load on the device." >&2
        exit 1
    fi
    echo "=== signed the PKCS#11 TA outside the build container, verified"
fi

# A core and a TA from different builds cannot load each other, and the failure
# only shows up on the device as a TA that will not start. Check the pair here.
TA_DIR="$REPO_DIR/rootfs_overlay/lib/optee_armtz"
PKCS11_TA="$TA_DIR/fd02c9da-306c-48c7-a49c-bbd827ae86ee.ta"

if [ "${SECURE_WORLD:-0}" = 1 ]; then
    if [ ! -f "$PKCS11_TA" ]; then
        echo "BUILD FAILED: the PKCS#11 TA was not installed into the image" >&2
        exit 1
    fi
    if [ ! "$PKCS11_TA" -nt "$BUILD_STAMP" ]; then
        echo "BUILD FAILED: $PKCS11_TA is left over from an earlier build." >&2
        echo "  It was signed with a different key than this core embeds and" >&2
        echo "  will not load." >&2
        exit 1
    fi
elif [ -f "$PKCS11_TA" ]; then
    # A plain build leaves no secure world to load it, and keeping it invites
    # pairing it with a core it was not built for.
    rm -f "$PKCS11_TA"
    rmdir "$TA_DIR" 2>/dev/null || true
fi

VARIANT_FILE="$REPO_DIR/uboot/u-boot-rockchip.variant"
if [ "${SECURE_WORLD:-0}" = 1 ]; then
    # Digests, so a later firmware build can check it is packaging this
    # bootloader with this TA rather than trusting the word "secure-world".
    BOOTLOADER_SHA=$(shasum -a 256 "$OUT_BIN" | cut -d' ' -f1)
    TA_SHA=$(shasum -a 256 "$PKCS11_TA" | cut -d' ' -f1)
    TA_KEY_FP=$(openssl rsa -in "$TA_PUBKEY" -pubin -outform DER 2>/dev/null |
                shasum -a 256 | cut -d' ' -f1)
    {
        echo "variant: secure-world"
        echo "built:   upstream TF-A + OP-TEE (PLATFORM=rockchip-rk3576, SPD=opteed)"
        echo "fuses:   yes - burns a HUK on first boot of an unprovisioned part"
        echo "debug:   ${SECURE_WORLD_DEBUG:-0}"
        echo "bootloader-sha256: $BOOTLOADER_SHA"
        echo "ta-sha256: $TA_SHA"
        echo "ta-pubkey-sha256: $TA_KEY_FP"
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
