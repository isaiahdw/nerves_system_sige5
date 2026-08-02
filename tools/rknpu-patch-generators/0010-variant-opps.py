p="/tmp/b/rknpu_devfreq.c"
s=open(p).read()
def sub(old,new,tag):
    global s
    assert s.count(old)==1, f"{tag}: {s.count(old)}"
    s=s.replace(old,new)

sub("#include <linux/pm_opp.h>\n",
    "#include <linux/nvmem-consumer.h>\n#include <linux/pm_opp.h>\n", "include")

helper = '''/*
 * Bin numbers as the vendor's rockchip_get_soc_info() assigns them, and one of
 * ours. BIT(bin) is matched against the first cell of opp-supported-hw in the
 * device tree, so these have to stay in step with the masks there.
 */
#define RKNPU_BIN_RK3576	0
#define RKNPU_BIN_RK3576M	1
#define RKNPU_BIN_RK3576J	2
#define RKNPU_BIN_RK3576S	3
/* Not the vendor's: the restricted table used when the part is unknown. */
#define RKNPU_BIN_UNKNOWN	8

/*
 * Pick the OPPs for this chip's variant. specification_serial_number in OTP
 * separates RK3576 from the S, J and M parts, which have their own frequencies
 * and voltages - the S stops at 500 MHz. The second word is left open: that is
 * the speed grade, which the device tree stands in for by carrying the slowest
 * silicon's voltage for each point.
 *
 * This must run even when the read fails: the OPP core disables any OPP
 * carrying opp-supported-hw unless supported_hw has been set.
 */
static int npu_devfreq_set_supported_hw(struct device *dev)
{
	u32 versions[2];
	int bin = RKNPU_BIN_RK3576;
	u8 spec;
	int ret;

	ret = nvmem_cell_read_u8(dev, "specification_serial_number", &spec);
	if (ret == -EPROBE_DEFER)
		return ret;
	if (ret) {
		/*
		 * Fail closed. Guessing the plain RK3576 table would hand
		 * 900 MHz to an RK3576S whose NPU stops at 500.
		 */
		LOG_DEV_WARN(dev,
			     "no specification_serial_number (%d), restricting the OPP table\\n",
			     ret);
		bin = RKNPU_BIN_UNKNOWN;
	} else {
		switch (spec) {
		case 0x0d:
			bin = RKNPU_BIN_RK3576M;
			break;
		case 0x0a:
			bin = RKNPU_BIN_RK3576J;
			break;
		case 0x13:
			bin = RKNPU_BIN_RK3576S;
			break;
		default:
			bin = RKNPU_BIN_RK3576;
			break;
		}
		LOG_DEV_INFO(dev, "npu bin %d (otp 0x%02x)\\n", bin, spec);
	}

	versions[0] = BIT(bin);
	versions[1] = 0xffff;

	return devm_pm_opp_set_supported_hw(dev, versions, ARRAY_SIZE(versions));
}

'''
anchor = "int rknpu_devfreq_init(struct rknpu_device *rknpu_dev)\n"
sub(anchor, helper + anchor, "helper")

sub("""	ret = devm_pm_opp_of_add_table(dev);
""",
"""	ret = npu_devfreq_set_supported_hw(dev);
	if (ret) {
		LOG_DEV_ERROR(dev, "failed to set supported hw: %d\\n", ret);
		return ret;
	}

	ret = devm_pm_opp_of_add_table(dev);
""", "init")
open(p,"w").write(s)
print("edited ok")
