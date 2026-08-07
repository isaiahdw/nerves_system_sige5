# Include system-specific packages
include $(sort $(wildcard $(NERVES_DEFCONFIG_DIR)/package/*/*.mk))

# Buildroot 2026.05 hard-requires LLVM in the target Mesa for panfrost
# (Config.in), but the only LLVM consumer in this driver set is the
# gallium software 'draw' module, which panfrost never uses. Turn it off
# so libgallium does not link libLLVM; post-build.sh then removes the
# orphaned library from the rootfs. CONF_OPTS is expanded when the
# configure rule runs, so appending here (external.mk is parsed after
# package makefiles) takes effect.
MESA3D_CONF_OPTS += -Ddraw-use-llvm=false

# Buildroot sets a package's source up once, when it first extracts it, and
# keeps nothing that would notice the inputs to that step changing afterwards.
# It rebuilds from the tree it already has and reports success, so an edited
# patch or an edited local source file just quietly is not in the image.
#
# Hash those inputs and keep the hash inside the extracted tree. When it no
# longer matches, the tree is stale: delete it and the next step extracts and
# patches a fresh one.
#
# The checks run only for a goal that builds, and only once. Deleting a build
# directory is not a thing to do while merely reading a makefile for
# `make source`, `make legal-info` or a variable query, and a recursive make
# reparsing this must not delete a tree the outer one is using.
#
# The marker is exported, so a nested make sees it already set and skips.
# MAKELEVEL cannot be used for this: buildroot is not invoked at the top level
# here, so requiring level 0 disables the check altogether.
#
# := on the check and = order matter: the value is captured before the marker
# is set, or this make would skip itself.
#
# An empty MAKECMDGOALS is the default goal, which builds - and plain `make` in
# the build directory is what buildroot itself tells you to run, so it has to be
# covered. -n and -q ask what would happen rather than doing it, and must not
# delete anything.
#
# A dry run appears in MAKEFLAGS two different ways, and both have to be read.
# GNU make packs short flags into the first word with no leading dash, so -n
# among others shows up as a letter in something like "rRn". But it can also
# arrive as a separate word: `make -n --no-print-directory` yields
# " --no-print-directory -n", where the first word is the long option and the
# -n is at the end. Searching only the first word misses that, and searching it
# without excluding long options matches the "n" in --no-print-directory and
# disables the check on every real build. Both mistakes have been made here.
#
# So: letters from the first word only when it is not a long option, plus the
# exact spellings anywhere in the list.
NERVES_BUILD_GOALS = all world
NERVES_DRY_RUN_WORDS = -n -q --dry-run --just-print --recon --question
NERVES_SHORT_FLAGS = $(filter-out -%,$(firstword $(MAKEFLAGS)))
NERVES_DRY_RUN = $(strip \
	$(foreach f,n q,$(findstring $(f),$(NERVES_SHORT_FLAGS))) \
	$(filter $(NERVES_DRY_RUN_WORDS),$(MAKEFLAGS)))
NERVES_WANTS_BUILD = \
	$(if $(MAKECMDGOALS),$(filter $(NERVES_BUILD_GOALS),$(MAKECMDGOALS)),default)
NERVES_STALE_CHECK := \
	$(if $(NERVES_STALE_CHECKED),,$(if $(NERVES_DRY_RUN),,$(NERVES_WANTS_BUILD)))
export NERVES_STALE_CHECKED := 1

# $(1) build directory, $(2) stamp file, $(3) hash the stamp must hold.
define nerves-discard-if-stale
$(if $(NERVES_STALE_CHECK),$(shell \
	if [ -d "$(1)" ] && [ "$$(cat $(2) 2>/dev/null)" != "$(3)" ]; then \
		rm -rf "$(1)"; \
	fi))
endef

# /dev/null keeps cat off stdin when a set is empty.
nerves-hash = $(shell cat /dev/null $(1) | sha256sum | cut -d' ' -f1)

# The kernel: linux/*.patch, applied at extract.
NERVES_LINUX_PATCH_HASH = \
	$(call nerves-hash,$(sort $(wildcard $(NERVES_DEFCONFIG_DIR)/linux/*.patch)))
NERVES_LINUX_PATCH_STAMP = $(LINUX_DIR)/.nerves-linux-patch-hash

# The NPU driver: its sources are downloaded, but package/rknpu-driver/*.patch
# is what makes them build against mainline, and it is applied at extract too.
NERVES_RKNPU_PATCH_HASH = \
	$(call nerves-hash,$(sort $(wildcard $(NERVES_DEFCONFIG_DIR)/package/rknpu-driver/*.patch)))
NERVES_RKNPU_DIR = $(BUILD_DIR)/rknpu-driver-$(RKNPU_DRIVER_VERSION)
NERVES_RKNPU_STAMP = $(NERVES_RKNPU_DIR)/.nerves-patch-hash

# The WiFi/BT firmware: the files are downloads, but which files and where they
# are installed is decided by the package makefile in this tree, so that is the
# input to hash. Adding a firmware blob to it otherwise changes nothing until
# the build directory is thrown away.
NERVES_BRCMFW_HASH = \
	$(call nerves-hash,$(sort $(wildcard $(NERVES_DEFCONFIG_DIR)/package/brcmfmac43752-firmware/*)))
NERVES_BRCMFW_DIR = \
	$(BUILD_DIR)/brcmfmac43752-firmware-$(BRCMFMAC43752_FIRMWARE_VERSION)
NERVES_BRCMFW_STAMP = $(NERVES_BRCMFW_DIR)/.nerves-pkg-hash

# optee-key: its source lives in this tree (SITE_METHOD = local), so extract is
# a copy.
NERVES_OPTEE_KEY_HASH = \
	$(call nerves-hash,$(sort $(wildcard $(NERVES_DEFCONFIG_DIR)/package/optee-key/src/*)))
NERVES_OPTEE_KEY_DIR = $(BUILD_DIR)/optee-key-$(OPTEE_KEY_VERSION)
NERVES_OPTEE_KEY_STAMP = $(NERVES_OPTEE_KEY_DIR)/.nerves-src-hash

NERVES_STALE_DISCARDED := \
	$(call nerves-discard-if-stale,$(LINUX_DIR),$(NERVES_LINUX_PATCH_STAMP),$(NERVES_LINUX_PATCH_HASH)) \
	$(call nerves-discard-if-stale,$(NERVES_RKNPU_DIR),$(NERVES_RKNPU_STAMP),$(NERVES_RKNPU_PATCH_HASH)) \
	$(call nerves-discard-if-stale,$(NERVES_BRCMFW_DIR),$(NERVES_BRCMFW_STAMP),$(NERVES_BRCMFW_HASH)) \
	$(call nerves-discard-if-stale,$(NERVES_OPTEE_KEY_DIR),$(NERVES_OPTEE_KEY_STAMP),$(NERVES_OPTEE_KEY_HASH))

define OPTEE_KEY_RECORD_SRC_HASH
	echo $(NERVES_OPTEE_KEY_HASH) > $(NERVES_OPTEE_KEY_STAMP)
endef
OPTEE_KEY_POST_EXTRACT_HOOKS += OPTEE_KEY_RECORD_SRC_HASH

define NERVES_LINUX_RECORD_PATCH_HASH
	echo $(NERVES_LINUX_PATCH_HASH) > $(NERVES_LINUX_PATCH_STAMP)
endef
LINUX_POST_PATCH_HOOKS += NERVES_LINUX_RECORD_PATCH_HASH

define NERVES_RKNPU_RECORD_PATCH_HASH
	echo $(NERVES_RKNPU_PATCH_HASH) > $(NERVES_RKNPU_STAMP)
endef
RKNPU_DRIVER_POST_PATCH_HOOKS += NERVES_RKNPU_RECORD_PATCH_HASH

define NERVES_BRCMFW_RECORD_HASH
	echo $(NERVES_BRCMFW_HASH) > $(NERVES_BRCMFW_STAMP)
endef
BRCMFMAC43752_FIRMWARE_POST_EXTRACT_HOOKS += NERVES_BRCMFW_RECORD_HASH
