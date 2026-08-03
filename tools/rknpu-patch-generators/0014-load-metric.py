#  0014: measure utilization over the real sampling window.
#
#  The load the driver reported was the busy time of the previous whole second,
#  latched by the 1 Hz timer, divided by a constant. Reading it twice in one
#  second returned the same number, so a governor polling faster than 1 Hz acted
#  on a stale sample and a burst was invisible for up to a second.
#
#  Keep a cumulative busy count that the timer never resets, and report the
#  difference since the last sample over the time that actually elapsed.

h = "/tmp/b/include/rknpu_drv.h"
s = open(h).read()

old = """struct rknpu_timer {
	ktime_t busy_time;
	ktime_t total_busy_time;
};
"""
new = """struct rknpu_timer {
	ktime_t busy_time;
	ktime_t total_busy_time;
	/* Busy time the 1 Hz timer has latched, never reset. */
	ktime_t busy_cum;
	/* What the cumulative count read at the last devfreq sample. */
	ktime_t devfreq_prev;
};
"""
assert s.count(old) == 1, ("timer struct", s.count(old))
s = s.replace(old, new)

old = "\tunsigned long current_volt;\n"
new = ("\tunsigned long current_volt;\n"
       "\t/* When devfreq last sampled the load, for the elapsed window. */\n"
       "\tktime_t devfreq_last_sample;\n")
assert s.count(old) == 1, ("current_volt", s.count(old))
s = s.replace(old, new)
open(h, "w").write(s)

c = "/tmp/b/rknpu_drv.c"
s = open(c).read()

old = """		subcore_data->timer.total_busy_time =
			subcore_data->timer.busy_time;
		subcore_data->timer.busy_time = 0;
"""
new = """		subcore_data->timer.busy_cum =
			ktime_add(subcore_data->timer.busy_cum,
				  subcore_data->timer.busy_time);
		subcore_data->timer.total_busy_time =
			subcore_data->timer.busy_time;
		subcore_data->timer.busy_time = 0;
"""
assert s.count(old) == 1, ("hrtimer latch", s.count(old))
s = s.replace(old, new)
open(c, "w").write(s)

d = "/tmp/b/rknpu_devfreq.c"
s = open(d).read()

old = '#include "rknpu_devfreq.h"\n'
new = '#include "rknpu_devfreq.h"\n#include "rknpu_job.h"\n'
assert s.count(old) == 1, ("include", s.count(old))
s = s.replace(old, new)

old = """static int npu_devfreq_get_dev_status(struct device *dev,
				      struct devfreq_dev_status *stat)
{
	struct rknpu_device *rknpu_dev = dev_get_drvdata(dev);
	unsigned long irqflags;
	ktime_t busy = 0;
	int i;

	/*
	 * Report the busiest core, so the governor scales up when any one is
	 * saturated. irq_lock guards the accumulator against a torn read.
	 */
	spin_lock_irqsave(&rknpu_dev->irq_lock, irqflags);
	for (i = 0; i < rknpu_dev->config->num_irqs; i++) {
		ktime_t t = rknpu_dev->subcore_datas[i].timer.total_busy_time;

		if (ktime_compare(t, busy) > 0)
			busy = t;
	}
	spin_unlock_irqrestore(&rknpu_dev->irq_lock, irqflags);

	stat->busy_time = (unsigned long)ktime_to_us(busy);
	stat->total_time = RKNPU_LOAD_INTERVAL / 1000;
	if (stat->busy_time > stat->total_time)
		stat->busy_time = stat->total_time;
	stat->current_frequency = rknpu_dev->current_freq;

	return 0;
}
"""
new = """/*
 * Busy time accrued on one core up to now: what the 1 Hz timer has latched,
 * plus what has accrued since, plus the part of a running job that has not
 * been accounted yet. Monotonic, so the difference between two reads is the
 * busy time between them. Caller holds irq_lock.
 */
static ktime_t npu_core_busy_cum(struct rknpu_subcore_data *sd, ktime_t now)
{
	ktime_t cum = ktime_add(sd->timer.busy_cum, sd->timer.busy_time);

	if (sd->job)
		cum = ktime_add(cum, ktime_sub(now, sd->job->hw_recoder_time));

	return cum;
}

static int npu_devfreq_get_dev_status(struct device *dev,
				      struct devfreq_dev_status *stat)
{
	struct rknpu_device *rknpu_dev = dev_get_drvdata(dev);
	unsigned long irqflags;
	ktime_t now, busy = 0;
	int i;

	/*
	 * Report the busiest core over the window since the last sample, so
	 * the governor acts on the load it is about to decide about rather
	 * than on the previous whole second.
	 */
	spin_lock_irqsave(&rknpu_dev->irq_lock, irqflags);
	now = ktime_get();
	for (i = 0; i < rknpu_dev->config->num_irqs; i++) {
		struct rknpu_subcore_data *sd = &rknpu_dev->subcore_datas[i];
		ktime_t cum = npu_core_busy_cum(sd, now);
		ktime_t delta = ktime_sub(cum, sd->timer.devfreq_prev);

		sd->timer.devfreq_prev = cum;
		if (ktime_compare(delta, busy) > 0)
			busy = delta;
	}
	stat->total_time = (unsigned long)ktime_to_us(
		ktime_sub(now, rknpu_dev->devfreq_last_sample));
	rknpu_dev->devfreq_last_sample = now;
	spin_unlock_irqrestore(&rknpu_dev->irq_lock, irqflags);

	stat->busy_time = (unsigned long)ktime_to_us(busy);
	if (stat->busy_time > stat->total_time)
		stat->busy_time = stat->total_time;
	stat->current_frequency = rknpu_dev->current_freq;

	return 0;
}
"""
assert s.count(old) == 1, ("get_dev_status", s.count(old))
s = s.replace(old, new)

old = """	/*
	 * No automatic polling at probe: the device comes up on the
	 * userspace governor so nothing changes the NPU rate until
	 * something asks for it. Switch to simple_ondemand at runtime
	 * (echo simple_ondemand > .../governor) for load-based scaling.
	 */
	.polling_ms = 0,
"""
new = """	/*
	 * The device comes up on the userspace governor, so nothing changes
	 * the NPU rate until something asks for it and this interval goes
	 * unused. It is set so that selecting a polling governor at runtime
	 * (echo simple_ondemand > .../governor) is all that is needed.
	 */
	.polling_ms = 50,
"""
assert s.count(old) == 1, ("polling", s.count(old))
s = s.replace(old, new)

old = "\trknpu_dev->devfreq = devm_devfreq_add_device(dev, dp,\n"
new = ("\trknpu_dev->devfreq_last_sample = ktime_get();\n"
       "\trknpu_dev->devfreq = devm_devfreq_add_device(dev, dp,\n")
assert s.count(old) == 1, ("add_device", s.count(old))
s = s.replace(old, new)
open(d, "w").write(s)

print("edited ok")
