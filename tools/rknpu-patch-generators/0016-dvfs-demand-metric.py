#  0016: report demand rather than duty cycle.
#
#  A utilization governor asks "what fraction of the window was the device
#  executing", and acts as though a device that is idle half the time can be
#  slowed down for free. That holds for a device that is the bottleneck. This
#  one is not: an inference is about 2.3 ms of NPU time inside a 9 ms pipeline,
#  so the NPU is idle roughly half the time even at full load and full rate,
#  and simple_ondemand walks to the lowest OPP - which costs 29% throughput,
#  because the NPU is still on the critical path.
#
#  There is no backlog to detect instead: pending work only exists while a job
#  is on the hardware, so queue depth carries no information the busy count
#  does not.
#
#  So report demand. Any execution inside a sampling window means the device
#  was wanted during that window, and raising the rate makes the work finish
#  sooner; report the window as fully busy. Only after several consecutive
#  windows with no work at all does the rate come down, which keeps a gap
#  between inferences from causing a transition.

h = "/tmp/b/include/rknpu_drv.h"
s = open(h).read()

old = "\tktime_t devfreq_last_sample;\n"
new = ("\tktime_t devfreq_last_sample;\n"
       "\t/* Consecutive devfreq samples that saw no work at all. */\n"
       "\tunsigned int devfreq_idle_windows;\n")
assert s.count(old) == 1, ("last_sample", s.count(old))
s = s.replace(old, new)
open(h, "w").write(s)

d = "/tmp/b/rknpu_devfreq.c"
s = open(d).read()

old = "#define POWER_DOWN_FREQ 200000000\n"
new = old + """
/*
 * Idle sampling windows before the rate is allowed to fall. At the 50 ms
 * polling interval this is 200 ms, comfortably longer than the gap between
 * inferences in a pipeline that is feeding the NPU as fast as it can.
 */
#define RKNPU_DVFS_IDLE_WINDOWS 4

static bool dvfs_demand_metric = true;
module_param(dvfs_demand_metric, bool, 0644);
MODULE_PARM_DESC(dvfs_demand_metric,
		 "report demand (default) rather than duty cycle to devfreq");
"""
assert s.count(old) == 1, ("power down freq", s.count(old))
s = s.replace(old, new)

old = """	stat->busy_time = (unsigned long)ktime_to_us(busy);
	if (stat->busy_time > stat->total_time)
		stat->busy_time = stat->total_time;
	stat->current_frequency = rknpu_dev->current_freq;
"""
new = """	if (dvfs_demand_metric) {
		if (busy > 0)
			rknpu_dev->devfreq_idle_windows = 0;
		else if (rknpu_dev->devfreq_idle_windows <
			 RKNPU_DVFS_IDLE_WINDOWS)
			rknpu_dev->devfreq_idle_windows++;

		stat->busy_time =
			rknpu_dev->devfreq_idle_windows < RKNPU_DVFS_IDLE_WINDOWS ?
				stat->total_time : 0;
	} else {
		stat->busy_time = (unsigned long)ktime_to_us(busy);
		if (stat->busy_time > stat->total_time)
			stat->busy_time = stat->total_time;
	}
	stat->current_frequency = rknpu_dev->current_freq;
"""
assert s.count(old) == 1, ("busy report", s.count(old))
s = s.replace(old, new)

# With a metric that reaches the top of the range, load-based scaling is
# worth having on by default.
old = """	/*
	 * The device comes up on the userspace governor, so nothing changes
	 * the NPU rate until something asks for it and this interval goes
	 * unused. It is set so that selecting a polling governor at runtime
	 * (echo simple_ondemand > .../governor) is all that is needed.
	 */
	.polling_ms = 50,
"""
new = """	/*
	 * Fast enough to follow a burst without the sampling itself costing
	 * anything measurable; the same interval panfrost uses for the GPU.
	 */
	.polling_ms = 50,
"""
assert s.count(old) == 1, ("polling comment", s.count(old))
s = s.replace(old, new)

old = """	rknpu_dev->devfreq = devm_devfreq_add_device(dev, dp,
						     DEVFREQ_GOV_USERSPACE, NULL);
"""
new = """	rknpu_dev->devfreq = devm_devfreq_add_device(dev, dp,
						     DEVFREQ_GOV_SIMPLE_ONDEMAND,
						     NULL);
"""
assert s.count(old) == 1, ("governor", s.count(old))
s = s.replace(old, new)
open(d, "w").write(s)

print("edited ok")
