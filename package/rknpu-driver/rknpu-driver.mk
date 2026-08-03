################################################################################
#
# rknpu-driver
#
################################################################################

# Vendor RKNPU kernel driver (v0.9.8) from the Rockchip 6.1 BSP, built
# out-of-tree against the mainline kernel. The 000x patches port it to the
# 6.18 APIs, reimplement the vendor-only devfreq/OPP integration on generic
# APIs, and attach the NPU to mainline's rockchip-iommu so its buffers are
# ordinary pageable memory rather than CMA. Pairs with librknnrt.
RKNPU_DRIVER_VERSION = 5280f9b4336199c4025c8eed894d2b4e2268dcc6
RKNPU_DRIVER_SITE = https://raw.githubusercontent.com/armbian/linux-rockchip/$(RKNPU_DRIVER_VERSION)/drivers/rknpu
RKNPU_DRIVER_SOURCE = rknpu_drv.c
RKNPU_DRIVER_EXTRA_DOWNLOADS = \
	Kconfig \
	Makefile \
	rknpu_debugger.c \
	rknpu_devfreq.c \
	rknpu_fence.c \
	rknpu_gem.c \
	rknpu_iommu.c \
	rknpu_job.c \
	rknpu_mem.c \
	rknpu_mm.c \
	rknpu_reset.c \
	$(RKNPU_DRIVER_SITE)/include/rknpu_debugger.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_devfreq.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_drv.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_fence.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_gem.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_ioctl.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_iommu.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_job.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_mem.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_mm.h \
	$(RKNPU_DRIVER_SITE)/include/rknpu_reset.h
RKNPU_DRIVER_LICENSE = GPL-2.0
RKNPU_DRIVER_MODULE_MAKE_OPTS = \
	CONFIG_ROCKCHIP_RKNPU=m \
	CONFIG_ROCKCHIP_RKNPU_DRM_GEM=y

define RKNPU_DRIVER_EXTRACT_CMDS
	mkdir -p $(@D)/include
	for f in Kconfig Makefile rknpu_debugger.c rknpu_devfreq.c \
		rknpu_drv.c rknpu_fence.c rknpu_gem.c rknpu_iommu.c \
		rknpu_job.c rknpu_mem.c rknpu_mm.c rknpu_reset.c; do \
		cp $(RKNPU_DRIVER_DL_DIR)/$$f $(@D)/; \
	done
	for f in rknpu_debugger.h rknpu_devfreq.h rknpu_drv.h rknpu_fence.h \
		rknpu_gem.h rknpu_ioctl.h rknpu_iommu.h rknpu_job.h \
		rknpu_mem.h rknpu_mm.h rknpu_reset.h; do \
		cp $(RKNPU_DRIVER_DL_DIR)/$$f $(@D)/include/; \
	done
endef

$(eval $(kernel-module))
$(eval $(generic-package))
