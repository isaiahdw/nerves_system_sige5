import io
# --- driver ---
c="/tmp/b/rknpu_drv.c"
s=open(c).read()
def sub(old,new,tag):
    global s
    assert s.count(old)==1, f"{tag}: {s.count(old)}"
    s=s.replace(old,new)

# item 1: publish the powered state once clocks and domains are up
sub("\tret = pm_runtime_get_sync(dev);\n",
    "\t/* Clocks and domains are up: the SCMI path is safe from here. */\n"
    "\tWRITE_ONCE(rknpu_dev->hw_powered, true);\n"
    "\n"
    "\tret = pm_runtime_get_sync(dev);\n"
    "\tif (ret >= 0) {\n"
    "\t\t/*\n"
    "\t\t * No resume callback runs when the device was already active -\n"
    "\t\t * which is what writing \"on\" to power/control leaves behind -\n"
    "\t\t * so nothing else would apply a rate deferred while the gate\n"
    "\t\t * was shut.\n"
    "\t\t */\n"
    "\t\trknpu_devfreq_sync_rate(dev);\n"
    "\t}\n", "power_on flag")

# item 1: clear it once teardown is committed to
sub("\tif (rknpu_dev->multiple_domains) {\n#ifndef FPGA_PLATFORM\n",
    "\t/*\n"
    "\t * Committed to tearing down now; everything above could still bail\n"
    "\t * out and leave the hardware up.\n"
    "\t */\n"
    "\tWRITE_ONCE(rknpu_dev->hw_powered, false);\n"
    "\n"
    "\tif (rknpu_dev->multiple_domains) {\n#ifndef FPGA_PLATFORM\n", "power_off flag")

# item 3: probe must not zero the bookkeeping when the power-off failed
sub("\trknpu_power_off(rknpu_dev);\n\tatomic_set(&rknpu_dev->power_refcount, 0);\n",
    "\t/*\n"
    "\t * A failed power-off leaves the regulators, clocks and domains still\n"
    "\t * referenced. Zeroing the count regardless would tell the next\n"
    "\t * power-on to enable them a second time.\n"
    "\t */\n"
    "\tif (rknpu_power_off(rknpu_dev))\n"
    "\t\tLOG_DEV_WARN(dev, \"failed to power off after probe\\n\");\n"
    "\telse\n"
    "\t\tatomic_set(&rknpu_dev->power_refcount, 0);\n", "probe power_off")

# item 4: retries must stay asynchronous even at delayms=0
sub("""static void rknpu_power_off_delay_work(struct work_struct *power_off_work)
{""",
    """/* Floor for the debugfs-settable delay: zero would busy-loop the workqueue. */
#define RKNPU_POWER_OFF_RETRY_MIN_MS 20
#define RKNPU_POWER_OFF_RETRY_LIMIT 10

/*
 * Re-arm the power-off worker after a power-off that could not run. Always
 * asynchronous: rknpu_power_put_delay() calls straight back into
 * rknpu_power_put() when the configured delay is zero, and with power-off
 * now able to return -EBUSY against an in-flight rate change the two would
 * recurse into each other until one of them won.
 */
static void rknpu_power_off_retry(struct rknpu_device *rknpu_dev)
{
	unsigned int delay = rknpu_dev->power_put_delay;

	/* Absent early in probe and once remove has quiesced it. */
	if (!rknpu_dev->power_off_wq)
		return;

	if (++rknpu_dev->power_off_retries > RKNPU_POWER_OFF_RETRY_LIMIT) {
		LOG_DEV_ERROR(
			rknpu_dev->dev,
			"giving up on powering the npu down after %d attempts\\n",
			RKNPU_POWER_OFF_RETRY_LIMIT);
		return;
	}

	if (delay < RKNPU_POWER_OFF_RETRY_MIN_MS)
		delay = RKNPU_POWER_OFF_RETRY_MIN_MS;

	queue_delayed_work(rknpu_dev->power_off_wq, &rknpu_dev->power_off_work,
			   msecs_to_jiffies(delay));
}

static void rknpu_power_off_delay_work(struct work_struct *power_off_work)
{""", "retry helper")

s=s.replace("""	mutex_unlock(&rknpu_dev->power_lock);

	if (ret)
		rknpu_power_put_delay(rknpu_dev);

	return ret;
}""","""	mutex_unlock(&rknpu_dev->power_lock);

	if (ret)
		rknpu_power_off_retry(rknpu_dev);

	return ret;
}""",1)

sub("""	mutex_unlock(&rknpu_dev->power_lock);

	if (ret)
		rknpu_power_put_delay(rknpu_dev);
}""","""	mutex_unlock(&rknpu_dev->power_lock);

	if (ret)
		rknpu_power_off_retry(rknpu_dev);
}""", "delay work retry")

open(c,"w").write(s)
print("edited ok")
