import re
h="/tmp/b/include/rknpu_drv.h"
s=open(h).read()
old="\tunsigned long current_volt;\n"
new=("\tunsigned long current_volt;\n"
     "\t/*\n"
     "\t * Serialises every rate change. The OPP call and the clk_set_rate\n"
     "\t * after it have to be one step, or a request arriving between them\n"
     "\t * leaves the rail set for one point and the clock at another.\n"
     "\t */\n"
     "\tstruct mutex dvfs_lock;\n"
     "\t/*\n"
     "\t * True only between rknpu_power_on() bringing the clocks and power\n"
     "\t * domains up and rknpu_power_off() taking them down. Runtime PM\n"
     "\t * reporting RPM_ACTIVE is not the same thing, and the SCMI rate\n"
     "\t * path has to know the difference or it writes into a gated APB.\n"
     "\t */\n"
     "\tbool hw_powered;\n"
     "\t/* Bounds the power-off retry so a persistent failure cannot spin. */\n"
     "\tunsigned int power_off_retries;\n")
assert s.count(old)==1, s.count(old)
s=s.replace(old,new)
open(h,"w").write(s)

hh="/tmp/b/include/rknpu_devfreq.h"
s=open(hh).read()
old="int rknpu_devfreq_runtime_resume(struct device *dev);\n"
assert s.count(old)==1, s.count(old)
s=s.replace(old, old + "int rknpu_devfreq_sync_rate(struct device *dev);\n")
open(hh,"w").write(s)

p="/tmp/b/rknpu_devfreq.c"
s=open(p).read()
def sub(old,new,tag):
    global s
    assert s.count(old)==1, f"{tag}: {s.count(old)}"
    s=s.replace(old,new)

sub("#define POWER_DOWN_FREQ 200000000\n",
    "#define POWER_DOWN_FREQ 200000000\n"
    "\n"
    "static int npu_devfreq_apply_locked(struct device *dev);\n", "constants")

# --- park helper, temp floor, and the single locked apply path ---
apply_old = s[s.index("static int npu_devfreq_apply_pending(struct device *dev)"):]
apply_old = apply_old[:apply_old.index("\n}\n")+3]
apply_new = '''/*
 * Park the clock at a rate the CRU can produce alone. BL31's PVTPLL state does
 * not survive the power domain going down, and coming back up still set to a
 * PVTPLL rate wedges the SoC on the next job.
 */
static int npu_devfreq_park(struct device *dev)
{
	struct rknpu_device *rknpu_dev = dev_get_drvdata(dev);
	int ret;

	ret = npu_clks_get(rknpu_dev);
	if (!ret) {
		ret = clk_set_rate(rknpu_dev->clks[0].clk, POWER_DOWN_FREQ);
		npu_clks_put(rknpu_dev);
	}
	if (ret) {
		LOG_DEV_ERROR(dev, "failed to park npu rate: %d\\n", ret);
		return ret;
	}

	rknpu_dev->current_freq = POWER_DOWN_FREQ;

	return 0;
}

/*
 * The one place a rate is applied. The governor, a deferred request replayed
 * on power-on and the thermal cooling device all come through here holding
 * dvfs_lock, so the OPP and the clock cannot describe different points.
 */
static int npu_devfreq_apply_locked(struct device *dev)
{
	struct rknpu_device *rknpu_dev = dev_get_drvdata(dev);
	unsigned long freq;
	int ret;

	lockdep_assert_held(&rknpu_dev->dvfs_lock);

	for (;;) {
		freq = READ_ONCE(rknpu_dev->ondemand_freq);
		if (!freq)
			return 0;

		ret = dev_pm_opp_set_rate(dev, freq);
		if (ret) {
			LOG_DEV_ERROR(dev, "failed to set npu rate %lu: %d\\n",
				      freq, ret);
			return ret;
		}

		/*
		 * The park uses a bare clk_set_rate() the OPP core never sees,
		 * so its cached rate survives and the call above can be a
		 * no-op. Set the clock and margin explicitly; the rail is
		 * already correct.
		 */
		ret = clk_set_rate(rknpu_dev->clks[0].clk, freq);
		if (ret) {
			LOG_DEV_ERROR(dev,
				      "failed to restore npu clock %lu: %d\\n",
				      freq, ret);
			return ret;
		}

		rknpu_dev->current_freq = freq;
		if (rknpu_dev->vdd)
			rknpu_dev->current_volt =
				regulator_get_voltage(rknpu_dev->vdd);

		if (READ_ONCE(rknpu_dev->ondemand_freq) == freq)
			return 0;
	}
}

static int npu_devfreq_apply_pending(struct device *dev)
{
	struct rknpu_device *rknpu_dev = dev_get_drvdata(dev);
	int ret;

	mutex_lock(&rknpu_dev->dvfs_lock);
	ret = npu_devfreq_apply_locked(dev);
	mutex_unlock(&rknpu_dev->dvfs_lock);

	return ret;
}

/*
 * Apply any pending rate for a caller that has just powered the hardware up.
 * pm_runtime_get_sync() runs no resume callback when the device is already
 * RPM_ACTIVE, so nothing else would apply a rate deferred while it was down.
 */
int rknpu_devfreq_sync_rate(struct device *dev)
{
	struct rknpu_device *rknpu_dev = dev_get_drvdata(dev);

	if (!rknpu_dev->devfreq || !READ_ONCE(rknpu_dev->hw_powered))
		return 0;

	return npu_devfreq_apply_pending(dev);
}
EXPORT_SYMBOL(rknpu_devfreq_sync_rate);
'''
s=s.replace(apply_old, apply_new)
open(p,"w").write(s)
print("stage 1 ok")

s=open(p).read()
# target(): publish the request before the same-frequency early return, so a
# request matching the current rate still cancels an older pending one
sub("""	if (*freq == rknpu_dev->current_freq)
		return 0;
""",
"""	/*
	 * Publish before comparing. Returning early without this leaves an
	 * older deferred request standing, and the next resume replays that
	 * rather than what was last asked for.
	 */
	WRITE_ONCE(rknpu_dev->ondemand_freq, *freq);

	if (*freq == rknpu_dev->current_freq)
		return 0;
""", "publish before compare")

# target(): apply through the one locked path
old = """	ret = dev_pm_opp_set_rate(dev, *freq);
	if (ret)
		LOG_DEV_ERROR(dev, "failed to set npu rate %lu: %d\\n", *freq,
			      ret);

	if (!ret) {
		rknpu_dev->current_freq = *freq; /* SCMI readback is unreliable */
		WRITE_ONCE(rknpu_dev->ondemand_freq, *freq);
		if (rknpu_dev->vdd)
			rknpu_dev->current_volt =
				regulator_get_voltage(rknpu_dev->vdd);
	}

	pm_runtime_put_noidle(dev);

	return ret;"""
new = """	mutex_lock(&rknpu_dev->dvfs_lock);
	ret = npu_devfreq_apply_locked(dev);
	mutex_unlock(&rknpu_dev->dvfs_lock);

	pm_runtime_put_noidle(dev);

	return ret;"""
assert s.count(old)==1, ("target", s.count(old))
s=s.replace(old,new)

# suspend: the park is mandatory, the devfreq bookkeeping is not
old = """	if (!rknpu_dev->devfreq)
		return 0;

	ret = devfreq_suspend_device(rknpu_dev->devfreq);"""
new = """	/*
	 * Parking is not optional and devfreq never coming up is not a reason
	 * to skip it: without it the PVTPLL rate outlives the power domain and
	 * wedges the SoC on the next job. Only the bookkeeping below is
	 * conditional.
	 */
	if (!rknpu_dev->devfreq)
		return npu_devfreq_park(dev);

	ret = devfreq_suspend_device(rknpu_dev->devfreq);"""
assert s.count(old)==1, ("suspend guard", s.count(old))
s=s.replace(old,new)

old = s[s.index("\t/*\n\t * BL31 owns the NPU PVTPLL and it does not survive the power domain"):]
old = old[:old.index("\treturn 0;\n}")+len("\treturn 0;\n}")]
new = """	ret = npu_devfreq_park(dev);
	if (ret) {
		/*
		 * A failed runtime suspend must not leave devfreq suspended
		 * while the PM core goes on treating the device as active.
		 */
		devfreq_resume_device(rknpu_dev->devfreq);
		return ret;
	}

	return 0;
}"""
s=s.replace(old,new)

# init: the dvfs lock
old = """	rknpu_dev->devfreq->last_status.current_frequency =
		rknpu_dev->current_freq;"""
new = """	rknpu_dev->devfreq->last_status.current_frequency =
		rknpu_dev->current_freq;"""
assert s.count(old)==1, ("init snapshot", s.count(old))
s=s.replace(old,new)

old = """	if (!of_find_property(dev->of_node, "operating-points-v2", NULL)) {"""
new = """	mutex_init(&rknpu_dev->dvfs_lock);

	if (!of_find_property(dev->of_node, "operating-points-v2", NULL)) {"""
assert s.count(old)==1, ("mutex init", s.count(old))
s=s.replace(old,new)
open(p,"w").write(s)
print("stage 2 ok")

s=open(p).read()
# target() publishes once, up front; both deferred branches used to repeat it
sub("""	if (pm_runtime_get_if_active(dev) <= 0) {
		WRITE_ONCE(rknpu_dev->ondemand_freq, *freq);
		return 0;
	}""",
"""	if (pm_runtime_get_if_active(dev) <= 0)
		return 0;""", "defer branch 1")

sub("""	if (!READ_ONCE(rknpu_dev->hw_powered)) {
		WRITE_ONCE(rknpu_dev->ondemand_freq, *freq);
		pm_runtime_put_noidle(dev);
		return 0;
	}""",
"""	if (!READ_ONCE(rknpu_dev->hw_powered)) {
		pm_runtime_put_noidle(dev);
		return 0;
	}""", "defer branch 2")
open(p,"w").write(s)
print("stage D ok")
