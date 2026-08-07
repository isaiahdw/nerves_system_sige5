# Probe publishes the device before it has finished building it.
#
# rknpu_drm_probe() calls drm_dev_register() and only then assigns
# drm_dev->dev_private, so an open landing in that window reaches a device
# whose private pointer is still NULL - and every GEM path dereferences it
# without checking. It also registers the fake device afterwards, which
# rknpu_gem_sync_ioctl() dereferences, so the same window hands out a NULL
# there too.
#
# The registration is also far too early in probe: it runs before power is on,
# before devfreq, the power-off workqueue, the deferrable work, the IOMMU
# domains, the cache scatter-gather tables, the debugger and the timer exist.
#
# Split allocation from publication. Allocation stays where the old call was,
# because the rest of probe hangs things off the object; publication moves to
# the end, after the last subsystem is up.

p = "/tmp/b/rknpu_drv.c"


def edit(path, old, new, what):
    s = open(path).read()
    assert s.count(old) == 1, (what, s.count(old))
    open(path, "w").write(s.replace(old, new))


# ---- the fake device has to succeed or fail, not half-exist ----
#
# platform_device_register_full() returns an ERR_PTR, which is not NULL, so
# the old test accepted it and then dereferenced it. And a failure left
# fake_dev NULL while the caller ignored the return, which
# rknpu_gem_sync_ioctl() only notices as a WARN_ON before using it anyway.
edit(p, """	pdev = platform_device_register_full(&rknpu_dev_info);
	if (pdev) {
		ret = of_dma_configure(&pdev->dev, NULL, true);
		if (ret) {
			platform_device_unregister(pdev);
			pdev = NULL;
		}
	}

	rknpu_dev->fake_dev = pdev ? &pdev->dev : NULL;

	return ret;
}""",
     """	pdev = platform_device_register_full(&rknpu_dev_info);
	if (IS_ERR(pdev))
		return PTR_ERR(pdev);

	ret = of_dma_configure(&pdev->dev, NULL, true);
	if (ret) {
		platform_device_unregister(pdev);
		return ret;
	}

	rknpu_dev->fake_dev = &pdev->dev;

	return 0;
}""",
     "fake dev err ptr")

# Clear the pointer before dropping the device, so nothing is left holding a
# reference to a device that has gone.
edit(p, """	pdev = to_platform_device(rknpu_dev->fake_dev);

	platform_device_unregister(pdev);""",
     """	pdev = to_platform_device(rknpu_dev->fake_dev);
	rknpu_dev->fake_dev = NULL;

	platform_device_unregister(pdev);""",
     "fake dev clear")

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

err_free_drm:
#if KERNEL_VERSION(4, 15, 0) <= LINUX_VERSION_CODE
	drm_dev_put(drm_dev);
#else
	drm_dev_unref(drm_dev);
#endif

	return ret;
}""",
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
 * exist - including the fake device, which rknpu_gem_sync_ioctl() uses for
 * every cache operation.
 */
static int rknpu_drm_register(struct rknpu_device *rknpu_dev)
{
	int ret;

	ret = drm_fake_dev_register(rknpu_dev);
	if (ret)
		return ret;

	ret = drm_dev_register(rknpu_dev->drm_dev, 0);
	if (ret < 0) {
		drm_fake_dev_unregister(rknpu_dev);
		return ret;
	}

	return 0;
}

/* Undo rknpu_drm_alloc() for a device that was never published. */
static void rknpu_drm_free(struct rknpu_device *rknpu_dev)
{
	struct drm_device *drm_dev = rknpu_dev->drm_dev;

	rknpu_dev->drm_dev = NULL;

	if (!drm_dev)
		return;

#if KERNEL_VERSION(4, 15, 0) <= LINUX_VERSION_CODE
	drm_dev_put(drm_dev);
#else
	drm_dev_unref(drm_dev);
#endif
}""",
     "split drm probe")

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

# ---- publish last, and hold the probe reference until it has ----
#
# The probe-time power reference used to be dropped here, before the debugger,
# the timer and now the registration. Anything failing after that point would
# reach err_power_put and drop the reference a second time - and by then the
# debugfs nodes are live, so the count it decremented could be a reference
# somebody else was relying on. Hold it until nothing else can fail.
edit(p, """	/*
	 * Drop the probe-time reference. rknpu_power_put() puts the count back
	 * and schedules a retry if the power-off could not run, so the count
	 * keeps describing the hardware either way.
	 */
	if (rknpu_power_put(rknpu_dev))
		LOG_DEV_WARN(dev, "deferred power off after probe\\n");
	atomic_set(&rknpu_dev->cmdline_power_refcount, 0);
	atomic_set(&rknpu_dev->iommu_domain_refcount, 0);

	rknpu_debugger_init(rknpu_dev);
	rknpu_init_timer(rknpu_dev);

	return 0;

err_power_put:""",
     """	atomic_set(&rknpu_dev->cmdline_power_refcount, 0);
	atomic_set(&rknpu_dev->iommu_domain_refcount, 0);

	rknpu_init_timer(rknpu_dev);

	/*
	 * Last of the things that can fail. From here userspace can open the
	 * node and submit work, and everything an ioctl reaches - power,
	 * devfreq, the workqueue, the deferrable work, the IOMMU domains, the
	 * timer - is now up.
	 */
#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	ret = rknpu_drm_register(rknpu_dev);
	if (ret) {
		LOG_DEV_ERROR(dev, "failed to register drm device (%d)\\n", ret);
		goto err_cancel_timer;
	}
#endif
#ifdef CONFIG_ROCKCHIP_RKNPU_DMA_HEAP
	ret = misc_register(&rknpu_dev->miscdev);
	if (ret) {
		LOG_DEV_ERROR(dev, "cannot register miscdev (%d)\\n", ret);
		goto err_cancel_timer;
	}
#endif

	/*
	 * The debugger goes after all of it, and nothing fallible follows,
	 * because its files outlive the call that made them. One left open
	 * across a failed probe would keep pointers to nodes that
	 * rknpu_debugger_remove() frees and to a rknpu_dev that devm is about
	 * to release; and a write to its power attribute in that window takes
	 * a reference teardown does not know about, so the workqueue, the
	 * domains and the device go while it is still held. Publishing it here
	 * means there is no window: probe cannot fail after this point.
	 */
	rknpu_debugger_init(rknpu_dev);

	/*
	 * Drop the probe-time reference. rknpu_power_put() puts the count back
	 * and schedules a retry if the power-off could not run, so the count
	 * keeps describing the hardware either way.
	 *
	 * Nothing above this point remains that can fail, so it goes here and
	 * only here. err_power_put drops it for every path that did not get
	 * this far, which is what makes that one put unambiguous.
	 */
	if (rknpu_power_put(rknpu_dev))
		LOG_DEV_WARN(dev, "deferred power off after probe\\n");

	return 0;

err_cancel_timer:
	rknpu_cancel_timer(rknpu_dev);
	/*
	 * While the probe reference is still held: freeing the domains
	 * switches the active one, which is register access.
	 */
	if (rknpu_dev->iommu_en)
		rknpu_iommu_free_domains(rknpu_dev);

err_power_put:""",
     "publish last")

# Nothing is registered on any of these paths, so the unwind releases the
# allocation rather than unregistering something that was never published.
#
# The rest is what remove() does and probe never did: all of it guarded, so
# the earlier jumps here - which reach this before any of it exists - are
# unaffected. rknpu_iommu_free_domains() is deliberately not among them; it
# needs the power reference that err_power_put above has already dropped, and
# is done there instead.
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
	for (i = 0; i < RKNPU_CACHE_SG_TABLE_NUM; i++) {
		if (rknpu_dev->cache_sgt[i]) {
			sg_free_table(rknpu_dev->cache_sgt[i]);
			kfree(rknpu_dev->cache_sgt[i]);
			rknpu_dev->cache_sgt[i] = NULL;
		}
	}

	if (IS_ENABLED(CONFIG_ROCKCHIP_RKNPU_SRAM) && rknpu_dev->sram_mm)
		rknpu_mm_destroy(rknpu_dev->sram_mm);

	if (rknpu_dev->iommu_en)
		iommu_group_put(rknpu_dev->iommu_group);

#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	rknpu_drm_free(rknpu_dev);
#endif

	return ret;
}""",
     "unwind frees rather than unregisters")

# The workqueue allocation fails after pm_runtime_enable() and the named domain
# attach, but jumps past the block that undoes them - it cannot use
# err_remove_wq, because there is no workqueue to destroy. Give that block its
# own label so the failure can reach it without destroying something that was
# never created.
edit(p, """err_remove_wq:
	destroy_workqueue(rknpu_dev->power_off_wq);
	rknpu_dev->power_off_wq = NULL;

	/*
	 * Only reached once pm_runtime_enable() and the named domain attach
	 * have run; earlier failures jump straight to err_remove_drv.
	 */
	if (!powered) {""",
     """err_remove_wq:
	destroy_workqueue(rknpu_dev->power_off_wq);
	rknpu_dev->power_off_wq = NULL;

err_detach_domains:
	/*
	 * Reached once pm_runtime_enable() and the named domain attach have
	 * run. Failures before that jump straight to err_remove_drv.
	 */
	if (!powered) {""",
     "detach label")

edit(p, """		LOG_DEV_ERROR(dev, "rknpu couldn't create power_off workqueue");
		ret = -ENOMEM;
		goto err_remove_drv;""",
     """		LOG_DEV_ERROR(dev, "rknpu couldn't create power_off workqueue");
		ret = -ENOMEM;
		goto err_detach_domains;""",
     "workqueue failure phase")

# ---- every failure after the group is taken has to give it back ----
#
# The clock, regulator, MMIO, IRQ, DRM-allocation and DMA-heap failures all
# returned straight out, past the only iommu_group_put() in probe. Nothing
# crashes, but a probe that defers - which the optional regulator makes routine
# at boot - leaks a group reference every time round.
#
# Rewrite the returns in that stretch as one jump. They are uniform: ret is
# already set for some and a literal for others, and none of them has anything
# else to undo, because the group is the first thing probe acquires that the
# device core does not manage.
s = open(p).read()
start = s.index("\trknpu_dev->num_clks = devm_clk_bulk_get_all")
end = s.index("#ifdef CONFIG_ROCKCHIP_RKNPU_FENCE")
region = s[start:end]

import re

# "return ret;" - the value is already the one to report.
region, n_ret = re.subn(r"\n(\t+)return ret;", r"\n\1goto err_put_group;", region)
assert n_ret == 4, ("return ret", n_ret)

# "return -Exxx;" and "return PTR_ERR(...);" - set ret first.
region, n_lit = re.subn(r"\n(\t+)return (-[A-Z]+|PTR_ERR\([^;]*\));",
                        r"\n\1ret = \2;\n\1goto err_put_group;", region)
assert n_lit == 4, ("return literal", n_lit)

s = s[:start] + region + s[end:]
open(p, "w").write(s)
print("early returns routed:", n_ret + n_lit)  # 8

# The label itself is the tail of the existing unwind, so the put stays in one
# place rather than being repeated. The reserved-memory region is the other
# half of the same either/or, and was never released at all.
edit(p, """	if (rknpu_dev->iommu_en)
		iommu_group_put(rknpu_dev->iommu_group);

#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	rknpu_drm_free(rknpu_dev);
#endif

	return ret;
}""",
     """#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	rknpu_drm_free(rknpu_dev);
#endif

err_put_group:
	if (rknpu_dev->iommu_en)
		iommu_group_put(rknpu_dev->iommu_group);
	else
		of_reserved_mem_device_release(dev);

	return ret;
}""",
     "err_put_group tail")

# remove() gives the group back but never the reserved-memory region, which is
# the other half of the same either/or in probe.
edit(p, """	if (rknpu_dev->iommu_en) {
		rknpu_iommu_free_domains(rknpu_dev);
		iommu_group_put(rknpu_dev->iommu_group);
	}
""",
     """	if (rknpu_dev->iommu_en) {
		rknpu_iommu_free_domains(rknpu_dev);
		iommu_group_put(rknpu_dev->iommu_group);
	} else {
		of_reserved_mem_device_release(rknpu_dev->dev);
	}
""",
     "remove reserved mem")
