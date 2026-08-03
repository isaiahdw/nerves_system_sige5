#  0017: raise the floor when work arrives, not after it has been seen.
#
#  0016 reported a window containing any execution as fully busy. That reaches
#  the top of the range under sustained load, but it is retrospective: after an
#  idle period the first job runs at whatever rate the device was left at, and
#  the rate only rises once a poll has observed work that has already finished.
#  A workload submitting one inference every few hundred milliseconds gets the
#  worst of it - every job slow, every gap boosted.
#
#  Raise a PM QoS minimum frequency instead, from every power acquisition -
#  which precedes the work but is broader than job submission - and drop it
#  when the device powers down. Re-acquisition is
#  synchronous, so a job arriving after the floor was released still runs at
#  full rate; the release point only decides how long an idle-but-powered
#  device sits high, and it gates entirely after power_put_delay anyway.
#
#  With the floor carrying the policy, the governor no longer has to be told a
#  half-idle device is busy, so the metric goes back to reporting what 0014
#  measures.

h = "/tmp/b/include/rknpu_drv.h"
s = open(h).read()

old = "#include <linux/miscdevice.h>\n"
assert s.count(old) == 1, ("miscdevice include", s.count(old))
s = s.replace(old, old + "#include <linux/pm_qos.h>\n")

old = "\tunsigned int devfreq_idle_windows;\n"
new = ("\tunsigned int devfreq_idle_windows;\n"
       "\t/* Holds the rate up from power acquisition until power down. */\n"
       "\tstruct dev_pm_qos_request devfreq_boost;\n"
       "\tbool devfreq_boost_added;\n"
       "\tunsigned long devfreq_max_khz;\n"
       "\t/*\n"
       "\t * Serialises a whole power transition including the QoS update\n"
       "\t * that follows it. power_lock alone is not enough: it is dropped\n"
       "\t * before the update, so a release could otherwise land after a\n"
       "\t * concurrent acquisition had already raised the floor.\n"
       "\t */\n"
       "\tstruct mutex power_transition_lock;\n")
assert s.count(old) == 1, ("idle windows", s.count(old))
s = s.replace(old, new)
open(h, "w").write(s)

fh = "/tmp/b/include/rknpu_devfreq.h"
s = open(fh).read()

old = "int rknpu_devfreq_sync_rate(struct device *dev);\n"
assert s.count(old) == 1, ("sync_rate decl", s.count(old))
s = s.replace(old, old + "int rknpu_devfreq_boost(struct rknpu_device *rknpu_dev, bool on);\n")

old = ("static inline int rknpu_devfreq_runtime_resume(struct device *dev)\n"
       "{\n\treturn 0;\n}\n")
assert s.count(old) == 1, ("resume stub", s.count(old))
s = s.replace(old, old +
              "\nstatic inline int rknpu_devfreq_boost(struct rknpu_device *rknpu_dev,\n"
              "\t\t\t\t      bool on)\n{\n\treturn 0;\n}\n")
open(fh, "w").write(s)

d = "/tmp/b/rknpu_devfreq.c"
s = open(d).read()

old = "static int npu_devfreq_get_dev_status(struct device *dev,\n"
new = """static bool dvfs_boost = true;
module_param(dvfs_boost, bool, 0644);
MODULE_PARM_DESC(dvfs_boost,
		 "hold the rate at maximum from power acquisition until power down (default on)");

int rknpu_devfreq_boost(struct rknpu_device *rknpu_dev, bool on)
{
	int ret;

	if (!rknpu_dev->devfreq_boost_added || !rknpu_dev->devfreq_max_khz)
		return 0;

	/*
	 * Only the raise is conditional. Gating the release too would leave a
	 * floor already applied stuck at maximum once the parameter is
	 * cleared, so a write to it takes effect at the next acquisition or
	 * power-down rather than at the write itself.
	 */
	ret = dev_pm_qos_update_request(&rknpu_dev->devfreq_boost,
					(on && READ_ONCE(dvfs_boost)) ?
						(s32)rknpu_dev->devfreq_max_khz :
						PM_QOS_DEFAULT_VALUE);

	/* 0 is "already that value", 1 is "changed"; both are success. */
	return ret < 0 ? ret : 0;
}

""" + old
assert s.count(old) == 1, ("get_dev_status anchor", s.count(old))
s = s.replace(old, new, 1)

# The floor is the highest rate this part's OPP table offers, so a variant
# capped at 500 MHz boosts to 500 rather than to a rate it does not have.
old = "\trknpu_dev->devfreq = devm_devfreq_add_device(dev, dp,\n\t\t\t\t\t\t     DEVFREQ_GOV_SIMPLE_ONDEMAND,\n\t\t\t\t\t\t     NULL);\n"
new = """	boost_freq = ULONG_MAX;
	opp = dev_pm_opp_find_freq_floor(dev, &boost_freq);
	if (!IS_ERR(opp)) {
		rknpu_dev->devfreq_max_khz = boost_freq / 1000;
		dev_pm_opp_put(opp);
	}
	if (rknpu_dev->devfreq_max_khz &&
	    dev_pm_qos_add_request(dev, &rknpu_dev->devfreq_boost,
				   DEV_PM_QOS_MIN_FREQUENCY,
				   PM_QOS_DEFAULT_VALUE) >= 0)
		rknpu_dev->devfreq_boost_added = true;
	else
		LOG_DEV_ERROR(
			dev,
			"no devfreq floor; coming up on the userspace governor\\n");

	/*
	 * Load-based scaling is only worth having with the floor to carry the
	 * policy. Without it, come up on userspace, where the rate at least
	 * stays where it is put, rather than on a governor already measured to
	 * settle at the lowest OPP.
	 */
	rknpu_dev->devfreq = devm_devfreq_add_device(
		dev, dp,
		rknpu_dev->devfreq_boost_added ? DEVFREQ_GOV_SIMPLE_ONDEMAND :
						 DEVFREQ_GOV_USERSPACE,
		NULL);
"""
assert s.count(old) == 1, ("governor", s.count(old))
s = s.replace(old, new)

old = ("\tstruct devfreq_dev_profile *dp = &npu_devfreq_profile;\n"
       "\tstruct dev_pm_opp *opp;\n\tint ret;\n")
assert s.count(old) == 1, ("init locals", s.count(old))
s = s.replace(old, "\tstruct devfreq_dev_profile *dp = &npu_devfreq_profile;\n"
                   "\tstruct dev_pm_opp *opp;\n\tunsigned long boost_freq;\n\tint ret;\n")

old = """void rknpu_devfreq_remove(struct rknpu_device *rknpu_dev)
{
"""
new = old + """	if (rknpu_dev->devfreq_boost_added) {
		dev_pm_qos_remove_request(&rknpu_dev->devfreq_boost);
		rknpu_dev->devfreq_boost_added = false;
	}
"""
assert s.count(old) == 1, ("devfreq remove", s.count(old))
s = s.replace(old, new)

old = "static bool dvfs_demand_metric = true;\n"
assert s.count(old) == 1, ("demand default", s.count(old))
s = s.replace(old, "static bool dvfs_demand_metric;\n")

old = '\t\t "report demand (default) rather than duty cycle to devfreq");\n'
assert s.count(old) == 1, ("demand desc", s.count(old))
s = s.replace(old, '\t\t "report demand rather than the measured duty cycle to devfreq");\n')
open(d, "w").write(s)

c = "/tmp/b/rknpu_drv.c"
s = open(c).read()

old = "\tmutex_init(&rknpu_dev->power_lock);\n"
assert s.count(old) == 1, ("power_lock init", s.count(old))
s = s.replace(old, old + "\tmutex_init(&rknpu_dev->power_transition_lock);\n")

# Above the retry comment block, so it stays attached to the function it
# describes, and after the forward declarations these call.
old = "static int rknpu_power_off(struct rknpu_device *rknpu_dev);\n"
assert s.count(old) == 1, ("forward decls", s.count(old))
s = s.replace(old, old + """
static void rknpu_devfreq_floor(struct rknpu_device *rknpu_dev, bool on)
{
	int ret = rknpu_devfreq_boost(rknpu_dev, on);

	if (ret)
		LOG_DEV_ERROR(rknpu_dev->dev,
			      "failed to %s the devfreq floor: %d\\n",
			      on ? "install" : "release", ret);
}

/*
 * Drop one reference, power the device down if it was the last, and release
 * the floor with it. Caller holds power_transition_lock, so the release
 * cannot land after a concurrent acquisition has raised the floor again.
 */
static int rknpu_power_drop(struct rknpu_device *rknpu_dev)
{
	bool unpowered = false;
	int ret = 0;

	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_dec_if_positive(&rknpu_dev->power_refcount) == 0) {
		ret = rknpu_power_off(rknpu_dev);
		if (ret)
			atomic_inc(&rknpu_dev->power_refcount);
		else
			unpowered = true;
	}
	mutex_unlock(&rknpu_dev->power_lock);

	if (unpowered)
		rknpu_devfreq_floor(rknpu_dev, false);

	return ret;
}
""")

old = """int rknpu_power_get(struct rknpu_device *rknpu_dev)
{
	int ret = 0;

	mutex_lock(&rknpu_dev->power_lock);
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
	mutex_unlock(&rknpu_dev->power_lock);

	return ret;
}
"""
new = """int rknpu_power_get(struct rknpu_device *rknpu_dev)
{
	bool retry = false;
	int ret = 0;

	mutex_lock(&rknpu_dev->power_transition_lock);
	mutex_lock(&rknpu_dev->power_lock);
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
	mutex_unlock(&rknpu_dev->power_lock);

	/*
	 * Raise on every acquisition, not only the one that powered the device
	 * up: rknpu_power_put_delay() leaves the count at one and defers the
	 * power-off, so consecutive acquisitions never re-cross zero. The QoS
	 * core returns early when the value is unchanged, so the repeats are
	 * cheap. Outside power_lock because this runs the governor, which takes
	 * its own; inside the transition lock so a concurrent release cannot
	 * land after it. -EBUSY runs nothing, so it does not raise.
	 *
	 * A constraint that cannot be installed fails the acquisition, and the
	 * reference goes back here because a caller that got an error will not
	 * put one. That is a promise about the constraint and not about the
	 * hardware: pm_qos_update_target() discards what the devfreq notifier
	 * returns, so a rate change that fails once the constraint is in place
	 * is only logged by devfreq.
	 */
	if (!ret) {
		ret = rknpu_devfreq_boost(rknpu_dev, true);
		if (ret) {
			LOG_DEV_ERROR(rknpu_dev->dev,
				      "failed to install the devfreq floor: %d\\n",
				      ret);
			/*
			 * If giving the reference back cannot power the
			 * device down, nobody owns it any more, so the
			 * power-off has to be retried from here.
			 */
			if (rknpu_power_drop(rknpu_dev))
				retry = true;
		}
	}
	mutex_unlock(&rknpu_dev->power_transition_lock);

	if (retry)
		rknpu_power_off_retry(rknpu_dev);

	return ret;
}
"""
assert s.count(old) == 1, ("power_get", s.count(old))
s = s.replace(old, new)

old = """int rknpu_power_put(struct rknpu_device *rknpu_dev)
{
	int ret = 0;

	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_dec_if_positive(&rknpu_dev->power_refcount) == 0) {
		ret = rknpu_power_off(rknpu_dev);
		if (ret)
			atomic_inc(&rknpu_dev->power_refcount);
	}
	mutex_unlock(&rknpu_dev->power_lock);

	if (ret)
		rknpu_power_off_retry(rknpu_dev);

	return ret;
}
"""
new = """int rknpu_power_put(struct rknpu_device *rknpu_dev)
{
	int ret;

	mutex_lock(&rknpu_dev->power_transition_lock);
	ret = rknpu_power_drop(rknpu_dev);
	mutex_unlock(&rknpu_dev->power_transition_lock);

	if (ret)
		rknpu_power_off_retry(rknpu_dev);

	return ret;
}
"""
assert s.count(old) == 1, ("power_put", s.count(old))
s = s.replace(old, new)

old = """	mutex_lock(&rknpu_dev->power_lock);
	if (atomic_dec_if_positive(&rknpu_dev->power_refcount) == 0) {
		ret = rknpu_power_off(rknpu_dev);
		if (ret)
			atomic_inc(&rknpu_dev->power_refcount);
	}
	mutex_unlock(&rknpu_dev->power_lock);

	if (ret)
		rknpu_power_off_retry(rknpu_dev);
}
"""
new = """	mutex_lock(&rknpu_dev->power_transition_lock);
	ret = rknpu_power_drop(rknpu_dev);
	mutex_unlock(&rknpu_dev->power_transition_lock);

	if (ret)
		rknpu_power_off_retry(rknpu_dev);
}
"""
assert s.count(old) == 1, ("delay work body", s.count(old))
s = s.replace(old, new)
open(c, "w").write(s)

print("edited ok")
