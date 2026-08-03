#  0015: a debugfs node that reports the raw DVFS signal.
#
#  Measurement support, not a fix. The load counters are only read by the
#  governor, and the governor only reads them under a polling governor, so
#  there is no way to see what the driver considers busy while the userspace
#  governor is selected. This exposes the cumulative counters, the queue depth
#  and the last sample the governor was given, so userspace can pick its own
#  sampling interval and compute exact deltas.

d = "/tmp/b/rknpu_devfreq.c"
s = open(d).read()

# The debugger needs the same accounting, so stop hiding it in this file.
old = "static ktime_t npu_core_busy_cum(struct rknpu_subcore_data *sd, ktime_t now)"
new = "ktime_t rknpu_devfreq_core_busy_cum(struct rknpu_subcore_data *sd, ktime_t now)"
assert s.count(old) == 1, ("helper decl", s.count(old))
s = s.replace(old, new)

old = "\t\tktime_t cum = npu_core_busy_cum(sd, now);\n"
new = "\t\tktime_t cum = rknpu_devfreq_core_busy_cum(sd, now);\n"
assert s.count(old) == 1, ("helper call", s.count(old))
s = s.replace(old, new)
open(d, "w").write(s)

h = "/tmp/b/include/rknpu_devfreq.h"
s = open(h).read()

old = ("static inline int rknpu_devfreq_runtime_resume(struct device *dev)\n"
       "{\n\treturn 0;\n}\n")
assert s.count(old) == 1, ("stub anchor", s.count(old))
s = s.replace(old, old +
              "\nstatic inline ktime_t\n"
              "rknpu_devfreq_core_busy_cum(struct rknpu_subcore_data *sd, ktime_t now)\n"
              "{\n\treturn 0;\n}\n")

old = "int rknpu_devfreq_sync_rate(struct device *dev);\n"
assert s.count(old) == 1, ("sync_rate decl", s.count(old))
s = s.replace(old, old +
              "ktime_t rknpu_devfreq_core_busy_cum(struct rknpu_subcore_data *sd,\n"
              "\t\t\t\t    ktime_t now);\n")
open(h, "w").write(s)

c = "/tmp/b/rknpu_debugger.c"
s = open(c).read()

old = '#include "rknpu_debugger.h"\n'
new = '#include "rknpu_debugger.h"\n#include "rknpu_devfreq.h"\n'
assert s.count(old) == 1, ("debugger include", s.count(old))
s = s.replace(old, new)

show = '''static int rknpu_dvfs_show(struct seq_file *m, void *data)
{
	struct rknpu_debugger_node *node = m->private;
	struct rknpu_debugger *debugger = node->debugger;
	struct rknpu_device *rknpu_dev =
		container_of(debugger, struct rknpu_device, debugger);
	unsigned long flags;
	ktime_t now;
	int i;

	/*
	 * One sample per read. The busy counts are cumulative and monotonic,
	 * so a reader takes two samples and divides the difference by the
	 * elapsed timestamp; that works at any interval and does not depend
	 * on a governor polling.
	 */
	spin_lock_irqsave(&rknpu_dev->irq_lock, flags);
	now = ktime_get();
	seq_printf(m, "ts_ns %lld\\n", ktime_to_ns(now));
	for (i = 0; i < rknpu_dev->config->num_irqs; i++) {
		struct rknpu_subcore_data *sd = &rknpu_dev->subcore_datas[i];

		seq_printf(m, "core %d busy_ns %lld queue %lld running %d\\n", i,
			   ktime_to_ns(rknpu_devfreq_core_busy_cum(sd, now)),
			   sd->task_num, sd->job ? 1 : 0);
	}
	spin_unlock_irqrestore(&rknpu_dev->irq_lock, flags);

	seq_printf(m, "freq_hz %lu\\n", rknpu_dev->current_freq);
	seq_printf(m, "volt_uv %lu\\n", rknpu_dev->current_volt);

	/* What the governor was last handed, empty while nothing polls. */
	if (rknpu_dev->devfreq)
		seq_printf(m, "gov_busy_us %lu gov_total_us %lu\\n",
			   rknpu_dev->devfreq->last_status.busy_time,
			   rknpu_dev->devfreq->last_status.total_time);

	return 0;
}

'''
old = "static struct rknpu_debugger_list rknpu_debugger_root_list[] = {"
assert s.count(old) == 1, ("root list", s.count(old))
s = s.replace(old, show + old)

old = '\t{ "load", rknpu_load_show, NULL, NULL },\n'
new = old + '\t{ "dvfs", rknpu_dvfs_show, NULL, NULL },\n'
assert s.count(old) == 1, ("load entry", s.count(old))
s = s.replace(old, new)
open(c, "w").write(s)

print("edited ok")
