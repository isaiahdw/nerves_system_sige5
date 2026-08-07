################################################################################
#
# brcmfmac43752-firmware
#
################################################################################

# Firmware for the AP6275S combo module: the BCM43752 WiFi side (firmware, CLM
# blob and NVRAM over SDIO) and the Bluetooth side's patchram. The BT core
# enumerates as BCM4362A2 and hci_bcm asks for brcm/BCM4362A2.hcd by that name;
# without it the controller runs on its ROM firmware. Neither is in upstream
# linux-firmware, so both come from the Armbian collection, pinned by commit.
BRCMFMAC43752_FIRMWARE_VERSION = d9846710f54da5e4383e2d67311819659ac2cf5c
BRCMFMAC43752_FIRMWARE_SITE = https://raw.githubusercontent.com/armbian/firmware/$(BRCMFMAC43752_FIRMWARE_VERSION)/brcm
BRCMFMAC43752_FIRMWARE_SOURCE = brcmfmac43752-sdio.bin
BRCMFMAC43752_FIRMWARE_EXTRA_DOWNLOADS = \
	brcmfmac43752-sdio.clm_blob \
	brcmfmac43752-sdio.txt \
	BCM4362A2.hcd
BRCMFMAC43752_FIRMWARE_LICENSE = PROPRIETARY
BRCMFMAC43752_FIRMWARE_REDISTRIBUTE = NO

define BRCMFMAC43752_FIRMWARE_EXTRACT_CMDS
	cp $(BRCMFMAC43752_FIRMWARE_DL_DIR)/brcmfmac43752-sdio.bin \
		$(BRCMFMAC43752_FIRMWARE_DL_DIR)/brcmfmac43752-sdio.clm_blob \
		$(BRCMFMAC43752_FIRMWARE_DL_DIR)/brcmfmac43752-sdio.txt \
		$(BRCMFMAC43752_FIRMWARE_DL_DIR)/BCM4362A2.hcd \
		$(@D)/
endef

# brcmfmac requests the board-specific NVRAM name first
# (brcmfmac43752-sdio.armsom,sige5.txt), then the generic one.
define BRCMFMAC43752_FIRMWARE_INSTALL_TARGET_CMDS
	$(INSTALL) -d $(TARGET_DIR)/lib/firmware/brcm
	$(INSTALL) -m 0644 $(@D)/brcmfmac43752-sdio.bin \
		$(@D)/brcmfmac43752-sdio.clm_blob \
		$(@D)/brcmfmac43752-sdio.txt \
		$(@D)/BCM4362A2.hcd \
		$(TARGET_DIR)/lib/firmware/brcm/
	ln -sf brcmfmac43752-sdio.txt \
		"$(TARGET_DIR)/lib/firmware/brcm/brcmfmac43752-sdio.armsom,sige5.txt"
endef

$(eval $(generic-package))
