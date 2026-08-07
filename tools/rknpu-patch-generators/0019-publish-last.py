# Probe publishes the device before it has finished building it.
#
# rknpu_drm_probe() calls drm_dev_register() and only then assigns
# drm_dev->dev_private, so an open landing in that window reaches a device
# whose private pointer is still NULL - and every GEM path dereferences it
# without checking. That window is small but it is a NULL dereference, not a
# missed feature.
#
# The registration itself is also far too early: it runs before power is on,
# before devfreq, the power-off workqueue, the deferrable work, the cache
# scatter-gather tables and the debugger exist. An ioctl arriving in that
# window reaches a device that is only partly built.
#
# Split allocation from publication. Allocation stays where the old call was,
# because the object has to exist for the rest of probe to hang things off;
# publication moves to the end, after the last subsystem is up.

p = "/tmp/b/rknpu_drv.c"


def edit(path, old, new, what):
    s = open(path).read()
    assert s.count(old) == 1, (what, s.count(old))
    open(path, "w").write(s.replace(old, new))


# ---- split the helper in two ----
edit(p, """static int rknpu_drm_probe(struct rknpu_device *rknpu_dev)
{
	struct device *dev = rknpu_dev->dev;
	struct drm_device *drm_dev = NULL;
	int ret = -EINVAL;

	drm_dev = drm_dev_alloc(&rknpu_drm_driver, dev);
	if (IS_ERR(drm_dev))
		return PTR_ERR(drm_dev);

	/* register the DRM device */
	ret = drm_dev_register(drm_dev, 0);
	if (ret < 0)
		goto err_free_drm;

	drm_dev->dev_private = rknpu_dev;
	rknpu_dev->drm_dev = drm_dev;

	drm_fake_dev_register(rknpu_dev);

	return 0;

err_free_drm:""",
     """/*
 * Allocate the DRM device and wire it to this driver, without publishing it.
 * Nothing can open it yet, so the order here is only about the rest of probe
 * being able to reach it.
 */
static int rknpu_drm_alloc(struct rknpu_device *rknpu_dev)
{
	struct device *dev = rknpu_dev->dev;
	struct drm_device *drm_dev = NULL;

	drm_dev = drm_dev_alloc(&rknpu_drm_driver, dev);
	if (IS_ERR(drm_dev))
		return PTR_ERR(drm_dev);

	/*
	 * Both directions before anyone can open the node. Every GEM path
	 * reaches the driver through dev_private and none of them check it,
	 * so publishing first would leave a window where an open resolves to
	 * NULL.
	 */
	drm_dev->dev_private = rknpu_dev;
	rknpu_dev->drm_dev = drm_dev;

	return 0;
}

/*
 * Publish it. Called last in probe, because from here userspace can open the
 * node and issue an ioctl, and everything one of those touches has to already
 * exist.
 */
static int rknpu_drm_register(struct rknpu_device *rknpu_dev)
{
	int ret;

	ret = drm_dev_register(rknpu_dev->drm_dev, 0);
	if (ret < 0)
		return ret;

	drm_fake_dev_register(rknpu_dev);

	return 0;
}

/* Undo rknpu_drm_alloc() for a device that was never published. */
static void rknpu_drm_free(struct rknpu_device *rknpu_dev)
{
	struct drm_device *drm_dev = rknpu_dev->drm_dev;

	rknpu_dev->drm_dev = NULL;

	if (!drm_dev)
		return;

err_free_drm:""",
     "split drm probe")

# The old tail of rknpu_drm_probe now closes rknpu_drm_free(), which has no
# error to return.
edit(p, """err_free_drm:
#if KERNEL_VERSION(4, 15, 0) <= LINUX_VERSION_CODE
	drm_dev_put(drm_dev);
#else
	drm_dev_unref(drm_dev);
#endif

	return ret;
}""",
     """err_free_drm:
#if KERNEL_VERSION(4, 15, 0) <= LINUX_VERSION_CODE
	drm_dev_put(drm_dev);
#else
	drm_dev_unref(drm_dev);
#endif
}""",
     "drm free tail")

# ---- probe: allocate here, publish at the end ----
edit(p, """#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	ret = rknpu_drm_probe(rknpu_dev);
	if (ret) {
		LOG_DEV_ERROR(dev, "failed to probe device for rknpu\\n");
		return ret;
	}
#endif
#ifdef CONFIG_ROCKCHIP_RKNPU_DMA_HEAP
	rknpu_dev->miscdev.minor = MISC_DYNAMIC_MINOR;
	rknpu_dev->miscdev.name = "rknpu";
	rknpu_dev->miscdev.fops = &rknpu_fops;

	ret = misc_register(&rknpu_dev->miscdev);
	if (ret) {
		LOG_DEV_ERROR(dev, "cannot register miscdev (%d)\\n", ret);
		return ret;
	}

""",
     """#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	ret = rknpu_drm_alloc(rknpu_dev);
	if (ret) {
		LOG_DEV_ERROR(dev, "failed to allocate drm device for rknpu\\n");
		return ret;
	}
#endif
#ifdef CONFIG_ROCKCHIP_RKNPU_DMA_HEAP
	rknpu_dev->miscdev.minor = MISC_DYNAMIC_MINOR;
	rknpu_dev->miscdev.name = "rknpu";
	rknpu_dev->miscdev.fops = &rknpu_fops;

""",
     "probe alloc only")

# ---- publish at the very end, and unwind it ----
edit(p, """	rknpu_debugger_init(rknpu_dev);
	rknpu_init_timer(rknpu_dev);

	return 0;

err_power_put:""",
     """	rknpu_debugger_init(rknpu_dev);
	rknpu_init_timer(rknpu_dev);

	/*
	 * Last. From here userspace can open the node and submit work, and
	 * everything an ioctl reaches - power, devfreq, the workqueue, the
	 * deferrable work, the IOMMU domains, the timer - is now up.
	 */
#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	ret = rknpu_drm_register(rknpu_dev);
	if (ret) {
		LOG_DEV_ERROR(dev, "failed to register drm device (%d)\\n", ret);
		goto err_remove_debugger;
	}
#endif
#ifdef CONFIG_ROCKCHIP_RKNPU_DMA_HEAP
	ret = misc_register(&rknpu_dev->miscdev);
	if (ret) {
		LOG_DEV_ERROR(dev, "cannot register miscdev (%d)\\n", ret);
		goto err_remove_debugger;
	}
#endif

	return 0;

err_remove_debugger:
	rknpu_cancel_timer(rknpu_dev);
	rknpu_debugger_remove(rknpu_dev);

err_power_put:""",
     "publish last")

# Nothing is registered on any of these paths now, so the unwind releases the
# allocation rather than unregistering something that was never published.
edit(p, """err_remove_drv:
#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	rknpu_drm_remove(rknpu_dev);
#endif
#ifdef CONFIG_ROCKCHIP_RKNPU_DMA_HEAP
	misc_deregister(&(rknpu_dev->miscdev));
#endif

	return ret;
}""",
     """err_remove_drv:
#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	rknpu_drm_free(rknpu_dev);
#endif

	return ret;
}""",
     "unwind frees rather than unregisters")
