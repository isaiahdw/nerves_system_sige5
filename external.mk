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
