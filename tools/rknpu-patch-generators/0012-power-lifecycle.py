import re

# ---- rknpu_devfreq.c: drop the empty wrappers ----
p="/tmp/b/rknpu_devfreq.c"
s=open(p).read()
old = """void rknpu_devfreq_lock(struct rknpu_device *rknpu_dev)
{
}

void rknpu_devfreq_unlock(struct rknpu_device *rknpu_dev)
{
}

"""
assert s.count(old)==1, ("wrapper defs", s.count(old))
open(p,"w").write(s.replace(old,""))

# ---- rknpu_devfreq.h: drop both declarations and both stubs ----
p="/tmp/b/include/rknpu_devfreq.h"
s=open(p).read()
s = s.replace("void rknpu_devfreq_lock(struct rknpu_device *rknpu_dev);\nvoid rknpu_devfreq_unlock(struct rknpu_device *rknpu_dev);\n", "")
s = re.sub(r"static inline void rknpu_devfreq_lock\(struct rknpu_device \*rknpu_dev\)\n\{\n\}\n\n", "", s)
s = re.sub(r"static inline void rknpu_devfreq_unlock\(struct rknpu_device \*rknpu_dev\)\n\{\n\}\n\n", "", s)
assert "rknpu_devfreq_lock" not in s, "header still references the wrapper"
open(p,"w").write(s)

# ---- rknpu_drv.c ----
p="/tmp/b/rknpu_drv.c"
s=open(p).read()
def sub(old,new,tag):
    global s
    assert s.count(old)==1, f"{tag}: {s.count(old)}"
    s=s.replace(old,new)

# power_on becomes transactional
start = s.index("static int rknpu_power_on(struct rknpu_device *rknpu_dev)\n{")
end = s.index("\n}\n", s.index("out:\n", start)) + 3
new_on = '''static int rknpu_power_on(struct rknpu_device *rknpu_dev)
{
	struct device *dev = rknpu_dev->dev;
	bool domain_err = false;
	int ret;

#ifndef FPGA_PLATFORM
	if (rknpu_dev->vdd) {
		ret = regulator_enable(rknpu_dev->vdd);
		if (ret) {
			LOG_DEV_ERROR(
				dev,
				"failed to enable vdd reg for rknpu, ret: %d\\n",
				ret);
			return ret;
		}
	}

	if (rknpu_dev->mem) {
		ret = regulator_enable(rknpu_dev->mem);
		if (ret) {
			LOG_DEV_ERROR(
				dev,
				"failed to enable mem reg for rknpu, ret: %d\\n",
				ret);
			goto err_vdd;
		}
	}
#endif

	ret = clk_bulk_prepare_enable(rknpu_dev->num_clks, rknpu_dev->clks);
	if (ret) {
		LOG_DEV_ERROR(dev, "failed to enable clk for rknpu, ret: %d\\n",
			      ret);
		goto err_mem;
	}

	if (rknpu_dev->multiple_domains) {
		if (rknpu_dev->genpd_dev_npu0) {
			ret = pm_runtime_resume_and_get(
				rknpu_dev->genpd_dev_npu0);
			if (ret < 0) {
				LOG_DEV_ERROR(
					dev,
					"failed to get pm runtime for npu0, ret: %d\\n",
					ret);
				goto err_clk;
			}
		}
		if (rknpu_dev->genpd_dev_npu1) {
			ret = pm_runtime_resume_and_get(
				rknpu_dev->genpd_dev_npu1);
			if (ret < 0) {
				LOG_DEV_ERROR(
					dev,
					"failed to get pm runtime for npu1, ret: %d\\n",
					ret);
				goto err_npu0;
			}
		}
		if (rknpu_dev->genpd_dev_npu2) {
			ret = pm_runtime_resume_and_get(
				rknpu_dev->genpd_dev_npu2);
			if (ret < 0) {
				LOG_DEV_ERROR(
					dev,
					"failed to get pm runtime for npu2, ret: %d\\n",
					ret);
				goto err_npu1;
			}
		}
	}

	/* Clocks and domains are up: the SCMI path is safe from here. */
	WRITE_ONCE(rknpu_dev->hw_powered, true);

	ret = pm_runtime_get_sync(dev);
	if (ret < 0) {
		LOG_DEV_ERROR(dev,
			      "failed to get pm runtime for rknpu, ret: %d\\n",
			      ret);
		pm_runtime_put_noidle(dev);
		goto err_hw;
	}

	/*
	 * No resume callback runs when the device was already active - which is
	 * what writing "on" to power/control leaves behind - so nothing else
	 * would apply a rate deferred while the gate was shut.
	 */
	ret = rknpu_devfreq_sync_rate(dev);
	if (ret) {
		int pmret;

		LOG_DEV_ERROR(dev, "failed to restore npu rate, ret: %d\\n",
			      ret);
		/*
		 * The clocks and domains below can only come down once the
		 * device is really suspended. If it is not - another reference
		 * is held, or the mandatory park failed - leave the whole stack
		 * up rather than pulling the suppliers out from under it.
		 */
		pmret = pm_runtime_put_sync(dev);
		if (pmret < 0 || !pm_runtime_status_suspended(dev)) {
			LOG_DEV_ERROR(
				dev,
				"npu still active after failed resume, leaving it powered\\n");
			return ret;
		}
		goto err_hw;
	}

	if (rknpu_dev->config->state_init != NULL)
		rknpu_dev->config->state_init(rknpu_dev);

	return 0;

err_hw:
	WRITE_ONCE(rknpu_dev->hw_powered, false);
	if (rknpu_dev->multiple_domains && rknpu_dev->genpd_dev_npu2)
		domain_err |= pm_runtime_put_sync(rknpu_dev->genpd_dev_npu2) < 0;
err_npu1:
	if (rknpu_dev->multiple_domains && rknpu_dev->genpd_dev_npu1)
		domain_err |= pm_runtime_put_sync(rknpu_dev->genpd_dev_npu1) < 0;
err_npu0:
	if (rknpu_dev->multiple_domains && rknpu_dev->genpd_dev_npu0)
		domain_err |= pm_runtime_put_sync(rknpu_dev->genpd_dev_npu0) < 0;

	/*
	 * A domain that would not go down still has registers behind these
	 * clocks and rails. Unwinding them anyway is what the unwind exists to
	 * prevent, so stop here and leave the leak.
	 */
	if (domain_err) {
		LOG_DEV_ERROR(
			dev,
			"npu domains did not go down, leaving clocks and rails up\\n");
		return ret;
	}
err_clk:
	clk_bulk_disable_unprepare(rknpu_dev->num_clks, rknpu_dev->clks);
err_mem:
#ifndef FPGA_PLATFORM
	if (rknpu_dev->mem)
		regulator_disable(rknpu_dev->mem);
err_vdd:
	if (rknpu_dev->vdd)
		regulator_disable(rknpu_dev->vdd);
#endif

	return ret;
}
'''
s = s[:start] + new_on + s[end:]

# power_get must not leave the reference behind on failure
sub("""	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_inc_return(&rknpu_dev->power_refcount) == 1)
		ret = rknpu_power_on(rknpu_dev);
	mutex_unlock(&rknpu_dev->power_lock);""",
"""	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_inc_return(&rknpu_dev->power_refcount) == 1) {
		ret = rknpu_power_on(rknpu_dev);
		if (ret)
			atomic_dec(&rknpu_dev->power_refcount);
	}
	mutex_unlock(&rknpu_dev->power_lock);""", "power_get rollback")

# the remaining empty-wrapper call sites
s = s.replace("#ifndef FPGA_PLATFORM\n\trknpu_devfreq_lock(rknpu_dev);\n#endif\n\n", "")
s = s.replace("#ifndef FPGA_PLATFORM\n\trknpu_devfreq_unlock(rknpu_dev);\n#endif\n\n", "")
s = s.replace("\t\trknpu_devfreq_unlock(rknpu_dev);\n", "")
s = s.replace("\t\t\trknpu_devfreq_unlock(rknpu_dev);\n", "")
s = s.replace("\trknpu_devfreq_unlock(rknpu_dev);\n", "")
s = s.replace("\trknpu_devfreq_lock(rknpu_dev);\n", "")
assert "rknpu_devfreq_lock" not in s and "rknpu_devfreq_unlock" not in s, "wrapper call site left behind"
open(p,"w").write(s)
print("stage A ok")

s=open(p).read()

# ioctl paths must not touch hardware after a failed power-up
sub("""	rknpu_power_get(rknpu_dev);

	switch (_IOC_NR(cmd)) {""",
"""	ret = rknpu_power_get(rknpu_dev);
	if (ret)
		return ret;

	switch (_IOC_NR(cmd)) {""", "non-drm ioctl")

sub("""		int ret = -EINVAL;                                          \\
		rknpu_power_get(rknpu_dev);                                 \\
		ret = func(dev, data, file_priv);                           \\""",
"""		int ret = rknpu_power_get(rknpu_dev);                       \\
		if (ret)                                                    \\
			return ret;                                         \\
		ret = func(dev, data, file_priv);                           \\""", "drm ioctl")

# probe holds a real reference for as long as the hardware is up
sub("""	ret = rknpu_power_on(rknpu_dev);
	if (ret)
		goto err_remove_drv;

#ifndef FPGA_PLATFORM
	rknpu_devfreq_init(rknpu_dev);
#endif""",
"""	/*
	 * Take a counted reference rather than powering up behind the
	 * refcount's back, so the release below goes through the same path as
	 * every other user and the count always describes the hardware.
	 */
	ret = rknpu_power_get(rknpu_dev);
	if (ret)
		goto err_remove_drv;

#ifndef FPGA_PLATFORM
	/*
	 * -EOPNOTSUPP just means no OPP table, which is a supported
	 * configuration. Anything else is fatal, including the -EPROBE_DEFER
	 * raised when the OTP that selects the variant OPPs is not up yet:
	 * binding anyway would run an RK3576S against the full table.
	 */
	ret = rknpu_devfreq_init(rknpu_dev);
	if (ret && ret != -EOPNOTSUPP) {
		rknpu_power_put(rknpu_dev);
		goto err_remove_drv;
	}
#endif""", "probe power + devfreq")

sub("""	/*
	 * A failed power-off leaves the regulators, clocks and domains still
	 * referenced. Zeroing the count regardless would tell the next
	 * power-on to enable them a second time.
	 */
	if (rknpu_power_off(rknpu_dev))
		LOG_DEV_WARN(dev, "failed to power off after probe\\n");
	else
		atomic_set(&rknpu_dev->power_refcount, 0);
	atomic_set(&rknpu_dev->cmdline_power_refcount, 0);""",
"""	/*
	 * Drop the probe-time reference. rknpu_power_put() puts the count back
	 * and schedules a retry if the power-off could not run, so the count
	 * keeps describing the hardware either way.
	 */
	if (rknpu_power_put(rknpu_dev))
		LOG_DEV_WARN(dev, "deferred power off after probe\\n");
	atomic_set(&rknpu_dev->cmdline_power_refcount, 0);""", "probe release")

# remove must not dismantle suppliers under live hardware
sub("""	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_read(&rknpu_dev->power_refcount) > 0)
		rknpu_power_off(rknpu_dev);
	mutex_unlock(&rknpu_dev->power_lock);

	if (rknpu_dev->multiple_domains) {""",
"""	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_read(&rknpu_dev->power_refcount) > 0)
		powered = rknpu_power_off(rknpu_dev) != 0;
	mutex_unlock(&rknpu_dev->power_lock);

	if (powered) {
		/*
		 * Detaching the domains and disabling runtime PM below would
		 * pull the suppliers out from under hardware that is still
		 * running. Leaking them is the lesser problem.
		 */
		LOG_DEV_ERROR(
			rknpu_dev->dev,
			"npu still powered, leaving its domains attached\\n");
		return 0;
	}

	if (rknpu_dev->multiple_domains) {""", "remove guard")
open(p,"w").write(s)
print("stage B ok")

s=open(p).read()

# remove: declare the flag, and keep the retry workqueue alive until the
# power-off has actually succeeded
sub("""static int rknpu_remove(struct platform_device *pdev)
{
	struct rknpu_device *rknpu_dev = platform_get_drvdata(pdev);
	int i = 0;

	cancel_delayed_work_sync(&rknpu_dev->power_off_work);
	destroy_workqueue(rknpu_dev->power_off_wq);

""",
"""static int rknpu_remove(struct platform_device *pdev)
{
	struct rknpu_device *rknpu_dev = platform_get_drvdata(pdev);
	bool powered = false;
	int i = 0;

""", "remove head")

sub("""	if (rknpu_dev->multiple_domains) {
		if (rknpu_dev->genpd_dev_npu0)
			dev_pm_domain_detach(rknpu_dev->genpd_dev_npu0, true);""",
"""	cancel_delayed_work_sync(&rknpu_dev->power_off_work);
	destroy_workqueue(rknpu_dev->power_off_wq);

	if (rknpu_dev->multiple_domains) {
		if (rknpu_dev->genpd_dev_npu0)
			dev_pm_domain_detach(rknpu_dev->genpd_dev_npu0, true);""", "remove wq order")
open(p,"w").write(s)
print("stage C ok")

s=open(p).read()

# probe: the retry workqueue has to exist before anything can take a power
# reference, since dropping one can queue onto it
sub("""	/*
	 * Take a counted reference rather than powering up behind the
	 * refcount's back, so the release below goes through the same path as
	 * every other user and the count always describes the hardware.
	 */
	ret = rknpu_power_get(rknpu_dev);
	if (ret)
		goto err_remove_drv;
""",
"""	/*
	 * Before any power reference exists: dropping one can queue a retry
	 * onto this workqueue, so it cannot be created afterwards.
	 */
	rknpu_dev->power_put_delay = 3000;
	rknpu_dev->power_off_wq =
		create_freezable_workqueue("rknpu_power_off_wq");
	if (!rknpu_dev->power_off_wq) {
		LOG_DEV_ERROR(dev, "rknpu couldn't create power_off workqueue");
		ret = -ENOMEM;
		goto err_remove_drv;
	}
	INIT_DEFERRABLE_WORK(&rknpu_dev->power_off_work,
			     rknpu_power_off_delay_work);

	/*
	 * Take a counted reference rather than powering up behind the
	 * refcount's back, so the release below goes through the same path as
	 * every other user and the count always describes the hardware.
	 */
	ret = rknpu_power_get(rknpu_dev);
	if (ret)
		goto err_remove_wq;
""", "probe wq first")

sub("""	ret = rknpu_devfreq_init(rknpu_dev);
	if (ret && ret != -EOPNOTSUPP) {
		rknpu_power_put(rknpu_dev);
		goto err_remove_drv;
	}
#endif

	// set default power put delay to 3s
	rknpu_dev->power_put_delay = 3000;
	rknpu_dev->power_off_wq =
		create_freezable_workqueue("rknpu_power_off_wq");
	if (!rknpu_dev->power_off_wq) {
		LOG_DEV_ERROR(dev, "rknpu couldn't create power_off workqueue");
		ret = -ENOMEM;
		goto err_devfreq_remove;
	}
	INIT_DEFERRABLE_WORK(&rknpu_dev->power_off_work,
			     rknpu_power_off_delay_work);
""",
"""	ret = rknpu_devfreq_init(rknpu_dev);
	if (ret && ret != -EOPNOTSUPP)
		goto err_power_put;
#endif
""", "probe devfreq + wq move")

sub("""			ret = rknpu_mm_create(rknpu_dev->sram_size, PAGE_SIZE,
					      &rknpu_dev->sram_mm);
			if (ret != 0)
				goto err_remove_wq;""",
"""			ret = rknpu_mm_create(rknpu_dev->sram_size, PAGE_SIZE,
					      &rknpu_dev->sram_mm);
			if (ret != 0)
				goto err_power_put;""", "sram label")

sub("""err_remove_wq:
	destroy_workqueue(rknpu_dev->power_off_wq);

err_devfreq_remove:
#ifndef FPGA_PLATFORM
	rknpu_devfreq_remove(rknpu_dev);
#endif
""",
"""err_power_put:
	/*
	 * Drop the probe-time reference before the resources it depends on go
	 * away, or a failed probe leaves the hardware powered across a reprobe.
	 */
	rknpu_power_put(rknpu_dev);
	cancel_delayed_work_sync(&rknpu_dev->power_off_work);

#ifndef FPGA_PLATFORM
	rknpu_devfreq_remove(rknpu_dev);
#endif

err_remove_wq:
	destroy_workqueue(rknpu_dev->power_off_wq);
	rknpu_dev->power_off_wq = NULL;
""", "probe labels")

# remove: quiesce, then power down, then free
sub("""	struct rknpu_device *rknpu_dev = platform_get_drvdata(pdev);
	bool powered = false;
	int i = 0;

	rknpu_debugger_remove(rknpu_dev);""",
"""	struct rknpu_device *rknpu_dev = platform_get_drvdata(pdev);
	int i = 0;

	/*
	 * Quiesce the retry worker first. It holds rknpu_dev and can power the
	 * device off by itself, so leaving it runnable races everything below.
	 */
	cancel_delayed_work_sync(&rknpu_dev->power_off_work);
	destroy_workqueue(rknpu_dev->power_off_wq);
	rknpu_dev->power_off_wq = NULL;

	/*
	 * Then power down, before freeing anything the power path touches.
	 * The remove callback the kernel calls returns void, so a failure here
	 * cannot stop the teardown - say so rather than pretending otherwise.
	 */
	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_read(&rknpu_dev->power_refcount) > 0) {
		if (rknpu_power_off(rknpu_dev))
			LOG_DEV_ERROR(
				rknpu_dev->dev,
				"failed to power off on remove, tearing down anyway\\n");
		atomic_set(&rknpu_dev->power_refcount, 0);
	}
	mutex_unlock(&rknpu_dev->power_lock);

	rknpu_debugger_remove(rknpu_dev);""", "remove head")

sub("""	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_read(&rknpu_dev->power_refcount) > 0)
		powered = rknpu_power_off(rknpu_dev) != 0;
	mutex_unlock(&rknpu_dev->power_lock);

	if (powered) {
		/*
		 * Detaching the domains and disabling runtime PM below would
		 * pull the suppliers out from under hardware that is still
		 * running. Leaking them is the lesser problem.
		 */
		LOG_DEV_ERROR(
			rknpu_dev->dev,
			"npu still powered, leaving its domains attached\\n");
		return 0;
	}

	cancel_delayed_work_sync(&rknpu_dev->power_off_work);
	destroy_workqueue(rknpu_dev->power_off_wq);

	if (rknpu_dev->multiple_domains) {""",
"""	if (rknpu_dev->multiple_domains) {""", "remove tail")
open(p,"w").write(s)
print("stage D ok")

s=open(p).read()

# power_on: say when it left the hardware up, so the caller keeps the count
sub("""		pmret = pm_runtime_put_sync(dev);
		if (pmret < 0 || !pm_runtime_status_suspended(dev)) {
			LOG_DEV_ERROR(
				dev,
				"npu still active after failed resume, leaving it powered\\n");
			return ret;
		}""",
"""		pmret = pm_runtime_put_sync(dev);
		if (pmret < 0 || !pm_runtime_status_suspended(dev)) {
			LOG_DEV_ERROR(
				dev,
				"npu still active after failed resume, leaving it powered\\n");
			return -EBUSY;
		}""", "sync unwind ebusy")

sub("""	if (domain_err) {
		LOG_DEV_ERROR(
			dev,
			"npu domains did not go down, leaving clocks and rails up\\n");
		return ret;
	}""",
"""	if (domain_err) {
		LOG_DEV_ERROR(
			dev,
			"npu domains did not go down, leaving clocks and rails up\\n");
		return -EBUSY;
	}""", "domain unwind ebusy")

# power_get: only release the count when power_on actually unwound
sub("""	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_inc_return(&rknpu_dev->power_refcount) == 1) {
		ret = rknpu_power_on(rknpu_dev);
		if (ret)
			atomic_dec(&rknpu_dev->power_refcount);
	}
	mutex_unlock(&rknpu_dev->power_lock);""",
"""	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_inc_return(&rknpu_dev->power_refcount) == 1) {
		ret = rknpu_power_on(rknpu_dev);
		/*
		 * -EBUSY is rknpu_power_on() reporting that it could not unwind
		 * and left resources enabled. Releasing the count there would
		 * describe powered hardware as off and let the next get enable
		 * it a second time, so the count stays held.
		 */
		if (ret && ret != -EBUSY)
			atomic_dec(&rknpu_dev->power_refcount);
	}
	mutex_unlock(&rknpu_dev->power_lock);""", "power_get ownership")

# clear the retry counter once a power-off actually completes
sub("""	clk_bulk_disable_unprepare(rknpu_dev->num_clks, rknpu_dev->clks);

#ifndef FPGA_PLATFORM
	if (rknpu_dev->vdd)
		regulator_disable(rknpu_dev->vdd);

	if (rknpu_dev->mem)
		regulator_disable(rknpu_dev->mem);
#endif

	return 0;
}""",
"""	clk_bulk_disable_unprepare(rknpu_dev->num_clks, rknpu_dev->clks);

#ifndef FPGA_PLATFORM
	if (rknpu_dev->vdd)
		regulator_disable(rknpu_dev->vdd);

	if (rknpu_dev->mem)
		regulator_disable(rknpu_dev->mem);
#endif

	rknpu_dev->power_off_retries = 0;

	return 0;
}""", "reset retry count")
open(p,"w").write(s)
print("stage E ok")

s=open(p).read()

# probe: complete cleanup, and do not tear down suppliers under live hardware
sub("""err_power_put:
	/*
	 * Drop the probe-time reference before the resources it depends on go
	 * away, or a failed probe leaves the hardware powered across a reprobe.
	 */
	rknpu_power_put(rknpu_dev);
	cancel_delayed_work_sync(&rknpu_dev->power_off_work);

#ifndef FPGA_PLATFORM
	rknpu_devfreq_remove(rknpu_dev);
#endif

err_remove_wq:
	destroy_workqueue(rknpu_dev->power_off_wq);
	rknpu_dev->power_off_wq = NULL;

err_remove_drv:""",
"""err_power_put:
	/*
	 * Drop the probe-time reference before the resources it depends on go
	 * away, or a failed probe leaves the hardware powered across a reprobe.
	 */
	if (rknpu_power_put(rknpu_dev)) {
		LOG_DEV_ERROR(
			dev,
			"npu still powered after a failed probe, leaving its suppliers attached\\n");
		powered = true;
	}
	cancel_delayed_work_sync(&rknpu_dev->power_off_work);

#ifndef FPGA_PLATFORM
	rknpu_devfreq_remove(rknpu_dev);
#endif

err_remove_wq:
	destroy_workqueue(rknpu_dev->power_off_wq);
	rknpu_dev->power_off_wq = NULL;

	/*
	 * Only reached once pm_runtime_enable() and the named domain attach
	 * have run; earlier failures jump straight to err_remove_drv.
	 */
	if (!powered) {
		if (rknpu_dev->multiple_domains) {
			if (rknpu_dev->genpd_dev_npu0)
				dev_pm_domain_detach(rknpu_dev->genpd_dev_npu0,
						     true);
			if (rknpu_dev->genpd_dev_npu1)
				dev_pm_domain_detach(rknpu_dev->genpd_dev_npu1,
						     true);
			if (rknpu_dev->genpd_dev_npu2)
				dev_pm_domain_detach(rknpu_dev->genpd_dev_npu2,
						     true);
		}
		pm_runtime_disable(dev);
	}

err_remove_drv:""", "probe cleanup")

sub("""	struct rknpu_device *rknpu_dev = NULL;
	struct device *dev = &pdev->dev;""",
"""	struct rknpu_device *rknpu_dev = NULL;
	struct device *dev = &pdev->dev;
	bool powered = false;""", "probe powered flag")

# remove: stop new ioctls before quiescing, and never yank live suppliers
sub("""	/*
	 * Quiesce the retry worker first. It holds rknpu_dev and can power the
	 * device off by itself, so leaving it runnable races everything below.
	 */
	cancel_delayed_work_sync(&rknpu_dev->power_off_work);""",
"""	/*
	 * Close the ioctl paths first. They take power references, and one
	 * arriving after the workqueue is gone would try to queue a retry onto
	 * it.
	 */
#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	rknpu_drm_remove(rknpu_dev);
#endif
#ifdef CONFIG_ROCKCHIP_RKNPU_DMA_HEAP
	misc_deregister(&(rknpu_dev->miscdev));
#endif

	/*
	 * Then the retry worker. It holds rknpu_dev and can power the device
	 * off by itself, so leaving it runnable races everything below.
	 */
	cancel_delayed_work_sync(&rknpu_dev->power_off_work);""", "remove ioctls first")

sub("""	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_read(&rknpu_dev->power_refcount) > 0) {
		if (rknpu_power_off(rknpu_dev))
			LOG_DEV_ERROR(
				rknpu_dev->dev,
				"failed to power off on remove, tearing down anyway\\n");
		atomic_set(&rknpu_dev->power_refcount, 0);
	}
	mutex_unlock(&rknpu_dev->power_lock);""",
"""	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_read(&rknpu_dev->power_refcount) > 0) {
		if (rknpu_power_off(rknpu_dev))
			powered = true;
		else
			atomic_set(&rknpu_dev->power_refcount, 0);
	}
	mutex_unlock(&rknpu_dev->power_lock);""", "remove power off")

sub("""#ifdef CONFIG_ROCKCHIP_RKNPU_DRM_GEM
	rknpu_drm_remove(rknpu_dev);
#endif
#ifdef CONFIG_ROCKCHIP_RKNPU_DMA_HEAP
	misc_deregister(&(rknpu_dev->miscdev));
#endif

#ifndef FPGA_PLATFORM
	rknpu_devfreq_remove(rknpu_dev);
#endif

	if (rknpu_dev->multiple_domains) {""",
"""#ifndef FPGA_PLATFORM
	rknpu_devfreq_remove(rknpu_dev);
#endif

	if (powered) {
		/*
		 * Everything above is freed either way - the callback the
		 * kernel calls returns void, so removal cannot be declined.
		 * The suppliers are the exception: detaching the domains and
		 * disabling runtime PM under hardware that is still running is
		 * worse than leaking them.
		 */
		LOG_DEV_ERROR(
			rknpu_dev->dev,
			"npu still powered on remove, leaving its suppliers attached\\n");
		return 0;
	}

	if (rknpu_dev->multiple_domains) {""", "remove supplier guard")

sub("""	struct rknpu_device *rknpu_dev = platform_get_drvdata(pdev);
	int i = 0;

	/*
	 * Close the ioctl paths first.""",
"""	struct rknpu_device *rknpu_dev = platform_get_drvdata(pdev);
	bool powered = false;
	int i = 0;

	/*
	 * Close the ioctl paths first.""", "remove powered flag")
open(p,"w").write(s)
print("stage F ok")
