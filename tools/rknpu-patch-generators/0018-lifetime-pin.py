import re

# The NPU is soldered to the board and its node is in the base device tree, so
# the device never goes away while the system runs. The driver is still a
# module, though, and nothing stopped it being unloaded - or unbound through
# sysfs - while objects that reach back into it were still alive.
#
# Rather than make the driver survive that (a hot-unplug design, which for this
# part would mean replacing dma_alloc_attrs with hand-rolled page and IOMMU
# management in the riskiest code it has), state the invariant it actually
# needs and enforce it: teardown does not begin while anything it owns is
# alive.
#
# Most of that is already true and comes for free from the kernel. An open DRM
# fd pins the module through DEFINE_DRM_GEM_FOPS, an mmap holds the file, an
# exported dma-buf inherits the same owner through drm_gem_prime_export, the
# misc device sets one, and procfs is rundown-protected by the proc core. What
# is left is everything with a lifetime of its own.

p = "/tmp/b/rknpu_drv.c"
g = "/tmp/b/rknpu_gem.c"
f = "/tmp/b/rknpu_fence.c"
j = "/tmp/b/rknpu_job.c"
h = "/tmp/b/include/rknpu_drv.h"


def edit(path, old, new, what):
    s = open(path).read()
    assert s.count(old) == 1, (what, s.count(old))
    open(path, "w").write(s.replace(old, new))


# ---- no sysfs unbind ----
#
# Unbind through sysfs runs remove() at a moment userspace chooses, which no
# amount of reference counting inside the driver can refuse. The device is not
# removable in any real sense, so stop offering it.
edit(p, """	.driver = {
		.owner = THIS_MODULE,
		.name = "RKNPU",""",
     """	.driver = {
		.owner = THIS_MODULE,
		.name = "RKNPU",
		/*
		 * The NPU is soldered and described by the base device tree,
		 * so it is never removed. Offering unbind would let userspace
		 * start teardown underneath live objects, which is the one
		 * case the module reference below cannot refuse.
		 */
		.suppress_bind_attrs = true,""",
     "suppress bind attrs")

# ---- counters, so a broken invariant is loud rather than silent ----
edit(h, "\tunsigned int power_off_retries;\n",
     """\tunsigned int power_off_retries;
\t/*
\t * Objects that hold a module reference. The reference is what keeps
\t * teardown away; these only exist to say which kind leaked if one is
\t * ever missed, since by then the module refcount alone cannot tell.
\t */
\tatomic_t live_gem;
\tatomic_t live_jobs;
\tatomic_t live_fences;
""",
     "live counters")

# ---- a place to take and drop the reference ----
#
# __module_get rather than try_module_get: every one of these runs from a
# caller that already holds a reference - an ioctl on an open fd - so it cannot
# fail, and treating it as fallible would add an error path that never runs.
# Deliberately not beside rknpu_fops: that sits inside the DMA-heap ifdef, so
# defining them there leaves rknpu_gem.c, rknpu_fence.c and rknpu_job.c calling
# symbols that do not exist in the configuration this system builds. modpost
# catches it, but only after a full link.
edit(p, """static int rknpu_power_on(struct rknpu_device *rknpu_dev);
static int rknpu_power_off(struct rknpu_device *rknpu_dev);""",
     """static int rknpu_power_on(struct rknpu_device *rknpu_dev);
static int rknpu_power_off(struct rknpu_device *rknpu_dev);

/*
 * Hold the module for an object that can outlive the fd that made it.
 *
 * The caller is always inside an ioctl on an open fd, which already pins the
 * module, so the count cannot be going to zero here and __module_get is safe.
 */
void rknpu_pin_module(atomic_t *live)
{
	__module_get(THIS_MODULE);
	atomic_inc(live);
}

void rknpu_unpin_module(atomic_t *live)
{
	atomic_dec(live);
	module_put(THIS_MODULE);
}""",
     "pin helpers")

edit(h, "int rknpu_power_get(struct rknpu_device *rknpu_dev);",
     """void rknpu_pin_module(atomic_t *live);
void rknpu_unpin_module(atomic_t *live);
int rknpu_power_get(struct rknpu_device *rknpu_dev);""",
     "pin decls")

# ---- remove() states the invariant it is relying on ----
#
# Reaching remove() means the module reference hit zero, so all three of these
# are zero unless something takes a reference it never drops. That is a driver
# bug and there is no recovery from it - the memory is about to go - so say so
# and carry on rather than pretending it can be handled.
edit(p, """	rknpu_gem_flush_deferred(rknpu_dev, true);
		rknpu_power_put(rknpu_dev);
	} else {
		rknpu_gem_flush_deferred(rknpu_dev, false);
	}
""",
     """	rknpu_gem_flush_deferred(rknpu_dev, true);
		rknpu_power_put(rknpu_dev);
	} else {
		rknpu_gem_flush_deferred(rknpu_dev, false);
	}

	/*
	 * Every object that can outlive its fd holds a module reference, and
	 * this only runs once the last one is gone, so all three are zero. A
	 * non-zero count means a reference was taken and never dropped: the
	 * object is about to point into freed memory and nothing here can fix
	 * that, so name it instead of leaving a later crash to explain it.
	 */
	WARN(atomic_read(&rknpu_dev->live_gem) ||
		     atomic_read(&rknpu_dev->live_jobs) ||
		     atomic_read(&rknpu_dev->live_fences),
	     "rknpu: teardown with %d gem, %d job(s), %d fence(s) still live\\n",
	     atomic_read(&rknpu_dev->live_gem),
	     atomic_read(&rknpu_dev->live_jobs),
	     atomic_read(&rknpu_dev->live_fences));
""",
     "remove assert")

# ---- fences ----
#
# A fence is the one object with no owner field to inherit: dma_fence_ops has
# none, and sync_file hands the fence to anything that asks. It also keeps a
# pointer to fence_ctx->spinlock, and fence_ctx is devm memory, so a fence
# outliving remove() would take its lock through a freed allocation. Holding
# the module keeps that allocation alive for exactly as long as the fence.
edit(f, """static const struct dma_fence_ops rknpu_fence_ops = {
	.get_driver_name = rknpu_fence_get_name,
	.get_timeline_name = rknpu_fence_get_name,
};""",
     """/*
 * dma_fence_ops has no owner, and a fence handed out through sync_file can be
 * held by anything for as long as it likes - including past the last close of
 * the fd that created it. The fence also takes its lock from fence_ctx, which
 * is devm memory freed at remove(), so the module reference is what keeps that
 * pointer valid rather than a lifetime of its own.
 */
static void rknpu_fence_release(struct dma_fence *fence)
{
	struct rknpu_device *rknpu_dev =
		container_of(fence->lock, struct rknpu_fence_context, spinlock)
			->rknpu_dev;

	rknpu_unpin_module(&rknpu_dev->live_fences);
	dma_fence_free(fence);
}

static const struct dma_fence_ops rknpu_fence_ops = {
	.get_driver_name = rknpu_fence_get_name,
	.get_timeline_name = rknpu_fence_get_name,
	.release = rknpu_fence_release,
};""",
     "fence release")

edit(f, """	fence_ctx->context = dma_fence_context_alloc(1);
	spin_lock_init(&fence_ctx->spinlock);
""",
     """	fence_ctx->context = dma_fence_context_alloc(1);
	spin_lock_init(&fence_ctx->spinlock);
	fence_ctx->rknpu_dev = rknpu_dev;
""",
     "fence ctx backref")

edit(f, """	dma_fence_init(fence, &rknpu_fence_ops, &fence_ctx->spinlock,
		       fence_ctx->context, ++fence_ctx->seqno);
""",
     """	rknpu_pin_module(&job->rknpu_dev->live_fences);
	dma_fence_init(fence, &rknpu_fence_ops, &fence_ctx->spinlock,
		       fence_ctx->context, ++fence_ctx->seqno);
""",
     "fence pin")

fh = "/tmp/b/include/rknpu_fence.h"
s = open(fh).read()
old = "struct rknpu_fence_context {"
assert s.count(old) == 1, ("fence ctx", s.count(old))
open(fh, "w").write(s.replace(old, """struct rknpu_fence_context {
	/* Reached from a fence through its lock, to drop the module ref. */
	struct rknpu_device *rknpu_dev;"""))

# ---- jobs ----
#
# A job outlives the ioctl that submitted it: nonblocking submits return
# immediately and the cleanup runs from a workqueue. The fd may be closed by
# then, so the job holds its own reference rather than borrowing the caller's.
edit(j, """	job->timestamp = ktime_get();
	job->rknpu_dev = rknpu_dev;""",
     """	job->timestamp = ktime_get();
	job->rknpu_dev = rknpu_dev;
	rknpu_pin_module(&rknpu_dev->live_jobs);""",
     "job pin")

edit(j, """	if (job->args_owner)
		kfree(job->args);

	kfree(job);
}""",
     """	if (job->args_owner)
		kfree(job->args);

	rknpu_unpin_module(&job->rknpu_dev->live_jobs);
	kfree(job);
}""",
     "job unpin")

# ---- gem objects ----
#
# An object is normally reachable from an fd or an exported dma-buf, both of
# which already hold the module. A deferred one is reachable from neither: its
# handle is gone and the destroy that would have freed it could not run, so the
# only thing left holding it is the driver's own list. Pinning at creation and
# dropping at the destroy that actually frees covers that case without needing
# to know which of the others applies.
edit(g, """	rknpu_gem_release(rknpu_obj);
	rknpu_iommu_domain_put(rknpu_dev);

	return 0;
}""",
     """	rknpu_gem_release(rknpu_obj);
	rknpu_iommu_domain_put(rknpu_dev);

	rknpu_unpin_module(&rknpu_dev->live_gem);

	return 0;
}""",
     "gem unpin")
