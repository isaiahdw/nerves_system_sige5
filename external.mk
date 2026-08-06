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

# Buildroot applies linux/*.patch once, when it first extracts the kernel, and
# keeps nothing that would notice a patch being added or edited afterwards. It
# rebuilds from the tree it already has and reports success, so a new patch
# just quietly is not in the image.
#
# Hash the patch set and keep the hash inside the extracted tree. When it no
# longer matches, the tree is stale: delete it and the next step extracts and
# patches a fresh one.
#
# Both checks below run only for a goal that builds, and only once. Deleting a
# build directory is not a thing to do while merely reading a makefile for
# `make source`, `make legal-info` or a variable query, and a recursive make
# reparsing this must not delete a tree the outer one is using.
#
# The marker is exported, so a nested make sees it already set and skips.
# MAKELEVEL cannot be used for this: buildroot is not invoked at the top level
# here, so requiring level 0 disables the check altogether.
#
# := on the check and = order matter: the value is captured before the marker
# is set, or this make would skip itself.
NERVES_BUILD_GOALS = all world
NERVES_STALE_CHECK := \
	$(if $(NERVES_STALE_CHECKED),,$(filter $(NERVES_BUILD_GOALS),$(MAKECMDGOALS)))
export NERVES_STALE_CHECKED := 1

# /dev/null keeps cat off stdin when there are no patches at all.
NERVES_LINUX_PATCHES = $(sort $(wildcard $(NERVES_DEFCONFIG_DIR)/linux/*.patch))
NERVES_LINUX_PATCH_HASH = $(shell cat /dev/null $(NERVES_LINUX_PATCHES) | sha256sum | cut -d' ' -f1)
NERVES_LINUX_PATCH_STAMP = $(LINUX_DIR)/.nerves-linux-patch-hash

ifneq ($(NERVES_STALE_CHECK),)
$(shell if [ -d "$(LINUX_DIR)" ] && \
	   [ "$$(cat $(NERVES_LINUX_PATCH_STAMP) 2>/dev/null)" != "$(NERVES_LINUX_PATCH_HASH)" ]; then \
		rm -rf "$(LINUX_DIR)"; \
	fi)
endif

# optee-key's source lives in this tree (SITE_METHOD = local), and buildroot
# copies it once at extract time - editing it afterwards changes nothing until
# the build directory is thrown away. Same hash-and-discard trick as the kernel
# patches above.
NERVES_OPTEE_KEY_SRC = $(sort $(wildcard $(NERVES_DEFCONFIG_DIR)/package/optee-key/src/*))
NERVES_OPTEE_KEY_HASH = $(shell cat /dev/null $(NERVES_OPTEE_KEY_SRC) | sha256sum | cut -d' ' -f1)
NERVES_OPTEE_KEY_DIR = $(BUILD_DIR)/optee-key-$(OPTEE_KEY_VERSION)
NERVES_OPTEE_KEY_STAMP = $(NERVES_OPTEE_KEY_DIR)/.nerves-src-hash

ifneq ($(NERVES_STALE_CHECK),)
$(shell if [ -d "$(NERVES_OPTEE_KEY_DIR)" ] && \
	   [ "$$(cat $(NERVES_OPTEE_KEY_STAMP) 2>/dev/null)" != "$(NERVES_OPTEE_KEY_HASH)" ]; then \
		rm -rf "$(NERVES_OPTEE_KEY_DIR)"; \
	fi)
endif

define OPTEE_KEY_RECORD_SRC_HASH
	echo $(NERVES_OPTEE_KEY_HASH) > $(NERVES_OPTEE_KEY_STAMP)
endef
OPTEE_KEY_POST_EXTRACT_HOOKS += OPTEE_KEY_RECORD_SRC_HASH

define NERVES_LINUX_RECORD_PATCH_HASH
	echo $(NERVES_LINUX_PATCH_HASH) > $(NERVES_LINUX_PATCH_STAMP)
endef
LINUX_POST_PATCH_HOOKS += NERVES_LINUX_RECORD_PATCH_HASH
