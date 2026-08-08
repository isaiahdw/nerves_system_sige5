################################################################################
#
# optee-key
#
################################################################################

# Source lives in this package rather than being downloaded - it is specific
# to this system and small enough to read in one sitting. See src/optee-key.c
# for why it is a process rather than a library binding.
OPTEE_KEY_VERSION = 1.0
OPTEE_KEY_SITE = $(NERVES_DEFCONFIG_DIR)/package/optee-key/src
OPTEE_KEY_SITE_METHOD = local
OPTEE_KEY_DEPENDENCIES = optee-client
OPTEE_KEY_LICENSE = Apache-2.0

define OPTEE_KEY_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_LDFLAGS) \
		-I$(STAGING_DIR)/usr/include/pkcs11 \
		-o $(@D)/optee-key $(@D)/optee-key.c -lckteec
endef

define OPTEE_KEY_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/optee-key $(TARGET_DIR)/usr/bin/optee-key
endef

$(eval $(generic-package))
