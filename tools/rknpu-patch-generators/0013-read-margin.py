h="/tmp/b/include/rknpu_drv.h"
s=open(h).read()
old = "#define RKNPU_LOAD_INTERVAL 1000000000\n"
assert s.count(old)==1, ("interval", s.count(old))
s=s.replace(old, old + """
/* Entries in the device tree's rockchip,volt-mem-read-margin table. */
#define RKNPU_MAX_RM_ENTRIES 8

/* No table entry matched, or nothing programmed since power was lost. */
#define RKNPU_RM_NONE UINT_MAX

struct rknpu_volt_rm {
	unsigned long volt;
	u32 rm;
};
""")
old = "\tunsigned int power_off_retries;\n"
new = ("\tunsigned int power_off_retries;\n"
       "\t/*\n"
       "\t * NPU_GRF SRAM read margin. Its reset value is 4; the vendor\n"
       "\t * derives one from the rail voltage and rewrites it as part of\n"
       "\t * every transition. current_rm is what was last written, so an\n"
       "\t * unchanged transition writes nothing, and the suspend park\n"
       "\t * invalidates it because the hardware loses the setting.\n"
       "\t */\n"
       "\tstruct regmap *npu_grf;\n"
       "\tu32 current_rm;\n"
       "\tint num_volt_rm;\n"
       "\tstruct rknpu_volt_rm volt_rm[RKNPU_MAX_RM_ENTRIES];\n")
assert s.count(old)==1, ("field", s.count(old))
open(h,"w").write(s.replace(old,new))

p="/tmp/b/rknpu_devfreq.c"
s=open(p).read()
def sub(old,new,tag):
    global s
    assert s.count(old)==1, f"{tag}: {s.count(old)}"
    s=s.replace(old,new)

sub("#include <linux/nvmem-consumer.h>\n",
    "#include <linux/mfd/syscon.h>\n#include <linux/nvmem-consumer.h>\n", "syscon")
sub("#include <linux/regulator/consumer.h>\n",
    "#include <linux/regmap.h>\n#include <linux/regulator/consumer.h>\n", "regmap")

helper = '''/*
 * NPU_GRF memory control registers. The SRAM read-margin field sits in each,
 * and these are Rockchip write-enable registers: the upper half selects which
 * bits the lower half applies to, so only the margin moves.
 */
#define NPU_GRF_MEM_CON0 0x08
#define NPU_GRF_MEM_CON1 0x0c
#define NPU_GRF_MEM_CON2 0x10
#define NPU_GRF_RM_SHIFT 2
#define NPU_GRF_RM_MASK_CON0_CON2 0x001c0000
#define NPU_GRF_RM_MASK_CON1 0x003c0000

/* The margin field is three bits wide in each of the three registers. */
#define NPU_RM_MAX 7

static u32 npu_target_read_margin(struct rknpu_device *rknpu_dev,
				  unsigned long volt)
{
	int i;

	/* Descending thresholds: the first at or below this voltage wins. */
	for (i = 0; i < rknpu_dev->num_volt_rm; i++)
		if (volt >= rknpu_dev->volt_rm[i].volt)
			return rknpu_dev->volt_rm[i].rm;

	return RKNPU_RM_NONE;
}

static int npu_set_read_margin(struct rknpu_device *rknpu_dev, u32 rm)
{
	int ret;

	if (!rknpu_dev->npu_grf || rm == RKNPU_RM_NONE)
		return 0;

	/*
	 * Check the gate before the cache, pairing with the release in
	 * rknpu_power_on(): a plain read could see the gate open while still
	 * holding the cache from before power was lost, and skip the write. A
	 * caller can also hold a runtime-PM reference and still find this
	 * closed, so report that rather than continue with a stale margin.
	 */
	if (!smp_load_acquire(&rknpu_dev->hw_powered))
		return -EBUSY;

	if (rm == READ_ONCE(rknpu_dev->current_rm))
		return 0;

	ret = regmap_write(rknpu_dev->npu_grf, NPU_GRF_MEM_CON0,
			   NPU_GRF_RM_MASK_CON0_CON2 | (rm << NPU_GRF_RM_SHIFT));
	if (!ret)
		ret = regmap_write(rknpu_dev->npu_grf, NPU_GRF_MEM_CON1,
				   NPU_GRF_RM_MASK_CON1 |
					   (rm << NPU_GRF_RM_SHIFT));
	if (!ret)
		ret = regmap_write(rknpu_dev->npu_grf, NPU_GRF_MEM_CON2,
				   NPU_GRF_RM_MASK_CON0_CON2 |
					   (rm << NPU_GRF_RM_SHIFT));
	if (ret) {
		/*
		 * A partial write leaves the three banks disagreeing. Do not
		 * record it as done, so the next transition tries again.
		 */
		WRITE_ONCE(rknpu_dev->current_rm, RKNPU_RM_NONE);
		LOG_DEV_ERROR(rknpu_dev->dev,
			      "failed to set npu read margin %u: %d\\n", rm, ret);
		return ret;
	}

	WRITE_ONCE(rknpu_dev->current_rm, rm);

	return 0;
}

/* Program the margin this frequency's OPP asks for. */
static int npu_apply_read_margin(struct rknpu_device *rknpu_dev,
				 unsigned long freq)
{
	struct dev_pm_opp *opp;
	unsigned long volt;

	if (!rknpu_dev->npu_grf)
		return 0;

	opp = dev_pm_opp_find_freq_exact(rknpu_dev->dev, freq, true);
	if (IS_ERR(opp))
		return PTR_ERR(opp);
	volt = dev_pm_opp_get_voltage(opp);
	dev_pm_opp_put(opp);

	return npu_set_read_margin(rknpu_dev,
				   npu_target_read_margin(rknpu_dev, volt));
}

/*
 * Program the margin while the rail is at the higher of the two voltages:
 * after it rises when scaling up, before it falls when scaling down. Hence the
 * OPP core's regulator step rather than anything after dev_pm_opp_set_rate().
 *
 * Drive the driver's own regulator: _opp_set_regulators() replaces a custom
 * config_regulators with its single-regulator helper, so registering one
 * through regulator_names uninstalls this callback.
 */
static int npu_opp_config_regulators(struct device *dev,
				     struct dev_pm_opp *old_opp,
				     struct dev_pm_opp *new_opp,
				     struct regulator **regulators,
				     unsigned int count)
{
	struct rknpu_device *rknpu_dev = dev_get_drvdata(dev);
	struct dev_pm_opp_supply old_supply, new_supply;
	unsigned long old_freq, new_freq;
	u32 target_rm;
	int ret;

	if (!rknpu_dev->vdd)
		return 0;

	ret = dev_pm_opp_get_supplies(new_opp, &new_supply);
	if (ret)
		return ret;
	ret = dev_pm_opp_get_supplies(old_opp, &old_supply);
	if (ret)
		return ret;

	old_freq = dev_pm_opp_get_freq(old_opp);
	new_freq = dev_pm_opp_get_freq(new_opp);
	target_rm = npu_target_read_margin(rknpu_dev, new_supply.u_volt);

	if (new_freq > old_freq) {
		ret = regulator_set_voltage(rknpu_dev->vdd, new_supply.u_volt,
					    new_supply.u_volt_max);
		if (ret)
			return ret;
		ret = npu_set_read_margin(rknpu_dev, target_rm);
		if (ret)
			return ret;
	} else {
		ret = npu_set_read_margin(rknpu_dev, target_rm);
		if (ret)
			return ret;
		ret = regulator_set_voltage(rknpu_dev->vdd, new_supply.u_volt,
					    new_supply.u_volt_max);
		if (ret) {
			npu_set_read_margin(rknpu_dev,
					    npu_target_read_margin(
						    rknpu_dev,
						    old_supply.u_volt));
			return ret;
		}
	}

	return 0;
}

/* An OPP with no table entry would leave the margin wherever it was. */
static int npu_read_margin_check_opps(struct rknpu_device *rknpu_dev)
{
	struct device *dev = rknpu_dev->dev;
	unsigned long freq = 0;
	struct dev_pm_opp *opp;

	if (!rknpu_dev->npu_grf)
		return 0;

	while (1) {
		unsigned long volt;

		opp = dev_pm_opp_find_freq_ceil(dev, &freq);
		if (IS_ERR(opp)) {
			/* -ERANGE ends the table; anything else is a real error. */
			if (PTR_ERR(opp) == -ERANGE)
				return 0;
			return PTR_ERR(opp);
		}

		volt = dev_pm_opp_get_voltage(opp);
		dev_pm_opp_put(opp);

		if (npu_target_read_margin(rknpu_dev, volt) == RKNPU_RM_NONE) {
			LOG_DEV_ERROR(dev,
				      "no read margin for %lu Hz at %lu uV\\n",
				      freq, volt);
			return -EINVAL;
		}
		freq++;
	}
}

/*
 * The syscon and the table live on the operating-points-v2 node. Absent, the
 * margin is left alone; present but unusable is an error rather than a silent
 * fallback to the reset value.
 */
static int npu_read_margin_init(struct rknpu_device *rknpu_dev)
{
	struct device *dev = rknpu_dev->dev;
	struct device_node *np;
	int count, i, ret = 0;

	WRITE_ONCE(rknpu_dev->current_rm, RKNPU_RM_NONE);

	np = of_parse_phandle(dev->of_node, "operating-points-v2", 0);
	if (!np)
		return 0;

	/* Neither property means the feature is off; half of one is rejected. */
	if (!of_property_present(np, "rockchip,grf") &&
	    !of_property_present(np, "rockchip,volt-mem-read-margin"))
		goto out;

	if (!of_property_present(np, "rockchip,grf") ||
	    !of_property_present(np, "rockchip,volt-mem-read-margin")) {
		LOG_DEV_ERROR(
			dev,
			"rockchip,grf and rockchip,volt-mem-read-margin go together\\n");
		ret = -EINVAL;
		goto out;
	}

	rknpu_dev->npu_grf = syscon_regmap_lookup_by_phandle(np, "rockchip,grf");
	if (IS_ERR(rknpu_dev->npu_grf)) {
		ret = PTR_ERR(rknpu_dev->npu_grf);
		rknpu_dev->npu_grf = NULL;
		LOG_DEV_ERROR(dev, "failed to look up rockchip,grf: %d\\n", ret);
		goto out;
	}

	count = of_property_count_u32_elems(np, "rockchip,volt-mem-read-margin");
	if (count <= 0 || count % 2 || count / 2 > RKNPU_MAX_RM_ENTRIES) {
		LOG_DEV_ERROR(dev, "bad rockchip,volt-mem-read-margin table\\n");
		rknpu_dev->npu_grf = NULL;
		ret = -EINVAL;
		goto out;
	}

	for (i = 0; i < count / 2; i++) {
		u32 volt, rm;

		of_property_read_u32_index(np, "rockchip,volt-mem-read-margin", i * 2,
					   &volt);
		of_property_read_u32_index(np, "rockchip,volt-mem-read-margin",
					   i * 2 + 1, &rm);
		/* First match wins, so the thresholds have to descend. */
		if (rm > NPU_RM_MAX ||
		    (i && volt >= rknpu_dev->volt_rm[i - 1].volt)) {
			LOG_DEV_ERROR(
				dev,
				"rockchip,volt-mem-read-margin must descend, with rm <= %d\\n",
				NPU_RM_MAX);
			rknpu_dev->npu_grf = NULL;
			ret = -EINVAL;
			goto out;
		}

		rknpu_dev->volt_rm[i].volt = volt;
		rknpu_dev->volt_rm[i].rm = rm;
	}
	rknpu_dev->num_volt_rm = count / 2;

out:
	of_node_put(np);

	return ret;
}

'''
anchor = "static int npu_devfreq_apply_locked(struct device *dev);\n"
sub(anchor, anchor + "\n" + helper, "helpers")

# install the callback without handing the OPP core our regulator
sub("""	if (of_find_property(dev->of_node, "rknpu-supply", NULL)) {
		/* NULL-terminated: the OPP core counts with while (*temp++) */
		const char *const reg_names[] = { "rknpu", NULL };

		ret = devm_pm_opp_set_regulators(dev, reg_names);
		if (ret) {
			LOG_DEV_ERROR(dev, "failed to set opp regulator: %d\\n",
				      ret);
			return ret;
		}
	}""",
"""	if (of_find_property(dev->of_node, "rknpu-supply", NULL)) {
		struct dev_pm_opp_config config = {
			.config_regulators = npu_opp_config_regulators,
		};

		ret = npu_read_margin_init(rknpu_dev);
		if (ret)
			return ret;

		ret = devm_pm_opp_set_config(dev, &config);
		if (ret) {
			LOG_DEV_ERROR(dev, "failed to set opp config: %d\\n",
				      ret);
			return ret;
		}
	}""", "opp config")

# the park loses the setting and leaves the OPP core's cache untouched, so the
# rate call on the way back can be a no-op that never reaches the callback
sub("""	rknpu_dev->current_freq = POWER_DOWN_FREQ;

	return 0;
}""",
"""	rknpu_dev->current_freq = POWER_DOWN_FREQ;
	WRITE_ONCE(rknpu_dev->current_rm, RKNPU_RM_NONE);

	return 0;
}""", "park invalidate")

sub("""		ret = clk_set_rate(rknpu_dev->clks[0].clk, freq);
		if (ret) {
			LOG_DEV_ERROR(dev,
				      "failed to restore npu clock %lu: %d\\n",
				      freq, ret);
			return ret;
		}""",
"""		ret = npu_apply_read_margin(rknpu_dev, freq);
		if (ret)
			return ret;

		ret = clk_set_rate(rknpu_dev->clks[0].clk, freq);
		if (ret) {
			LOG_DEV_ERROR(dev,
				      "failed to restore npu clock %lu: %d\\n",
				      freq, ret);
			return ret;
		}""", "explicit reapply")

sub("""	ret = devm_pm_opp_of_add_table(dev);
	if (ret) {
		LOG_DEV_ERROR(dev, "failed to add opp table: %d\\n", ret);
		return ret;
	}
""",
"""	ret = devm_pm_opp_of_add_table(dev);
	if (ret) {
		LOG_DEV_ERROR(dev, "failed to add opp table: %d\\n", ret);
		return ret;
	}

	ret = npu_read_margin_check_opps(rknpu_dev);
	if (ret)
		return ret;
""", "coverage call")


# --- rknpu_drv.c: publish the cleared cache before the gate opens ---
d="/tmp/b/rknpu_drv.c"
t=open(d).read()
old = """	/* Clocks and domains are up: the SCMI path is safe from here. */
	WRITE_ONCE(rknpu_dev->hw_powered, true);
"""
new = """	/*
	 * Hardware comes back with its read margin at the reset value, so the
	 * cached one is meaningless. Clear it before the gate opens and use a
	 * release store, so anything that sees the gate open also sees this.
	 */
	WRITE_ONCE(rknpu_dev->current_rm, RKNPU_RM_NONE);

	/* Clocks and domains are up: the SCMI path is safe from here. */
	smp_store_release(&rknpu_dev->hw_powered, true);
"""
assert t.count(old)==1, ("drv gate", t.count(old))
open(d,"w").write(t.replace(old,new))

open(p,"w").write(s)
print("edited ok")
