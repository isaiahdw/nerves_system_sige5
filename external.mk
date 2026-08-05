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
# patches a fresh one. This is parsed on every make invocation, including the
# recursive ones, which is harmless - once the tree is gone or the hash agrees
# there is nothing left to do.
# /dev/null keeps cat off stdin when there are no patches at all.
NERVES_LINUX_PATCHES = $(sort $(wildcard $(NERVES_DEFCONFIG_DIR)/linux/*.patch))
NERVES_LINUX_PATCH_HASH = $(shell cat /dev/null $(NERVES_LINUX_PATCHES) | sha256sum | cut -d' ' -f1)
NERVES_LINUX_PATCH_STAMP = $(LINUX_DIR)/.nerves-linux-patch-hash

$(shell if [ -d "$(LINUX_DIR)" ] && \
	   [ "$$(cat $(NERVES_LINUX_PATCH_STAMP) 2>/dev/null)" != "$(NERVES_LINUX_PATCH_HASH)" ]; then \
		rm -rf "$(LINUX_DIR)"; \
	fi)

define NERVES_LINUX_RECORD_PATCH_HASH
	echo $(NERVES_LINUX_PATCH_HASH) > $(NERVES_LINUX_PATCH_STAMP)
endef
LINUX_POST_PATCH_HOOKS += NERVES_LINUX_RECORD_PATCH_HASH
