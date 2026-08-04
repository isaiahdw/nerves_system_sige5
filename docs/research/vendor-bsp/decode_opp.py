#!/usr/bin/env python3
"""Decode the OPP tables out of a dtc-produced .dts into markdown."""
import re, sys

src = open(sys.argv[1]).read()


def cells(text):
    return [int(x, 16) if x.startswith("0x") else int(x) for x in text.split()]


def node_body(name):
    m = re.search(r"^\t%s \{$" % re.escape(name), src, re.M)
    if not m:
        return None
    depth, i = 0, m.start()
    for j in range(m.start(), len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
    return None


def prop(body, key):
    m = re.search(r"%s = <([^>]*)>;" % re.escape(key), body)
    return cells(m.group(1)) if m else None


def report(name):
    body = node_body(name)
    if not body:
        print("## %s\n\nnot present\n" % name)
        return
    print("## %s\n" % name)

    sel = prop(body, "rockchip,pvtm-voltage-sel")
    if sel:
        print("Voltage-grade buckets:\n")
        print("| pvtm | grade |")
        print("| --- | --- |")
        for k in range(0, len(sel), 3):
            print("| %d-%d | L%d |" % (sel[k], sel[k + 1], sel[k + 2]))
        print()

    for key, scale, unit in [
        ("rockchip,pvtm-freq", 1, "kHz"),
        ("rockchip,pvtm-volt", 1, "uV"),
        ("rockchip,low-temp", 1000, "degC"),
        ("rockchip,low-temp-min-volt", 1, "uV"),
        ("rockchip,temp-hysteresis", 1000, "degC"),
        ("rockchip,pvtm-ref-temp", 1, "degC"),
    ]:
        v = prop(body, key)
        if v:
            print("- `%s` = %s %s" % (key, v[0] // scale if scale > 1 else v[0], unit))
    tp = prop(body, "rockchip,pvtm-temp-prop")
    if tp:
        print("- `rockchip,pvtm-temp-prop` = %s" % (", ".join(str(x) for x in tp)))
    vm = prop(body, "volt-mem-read-margin")
    if vm:
        print("- `volt-mem-read-margin` = " + ", ".join(
            "%duV->%d" % (vm[k], vm[k + 1]) for k in range(0, len(vm), 2)))
    print()

    opps = {}
    grades = set()
    for m in re.finditer(r"\t\t(opp-[\w-]+) \{(.*?)\n\t\t\};", body, re.S):
        ob = m.group(2)
        hz = prop(ob, "opp-hz")
        if not hz:
            continue
        mhz = ((hz[0] << 32) | hz[1]) // 1000000
        row = {}
        for gm in re.finditer(r"opp-microvolt(-L(\d+))? = <([^>]*)>;", ob):
            g = "base" if gm.group(2) is None else "L" + gm.group(2)
            row[g] = cells(gm.group(3))[0]
            grades.add(g)
        opps[mhz] = row

    order = ["base"] + sorted((g for g in grades if g != "base"), key=lambda s: int(s[1:]))
    print("| MHz | " + " | ".join(order) + " |")
    print("| --- | " + " | ".join("---" for _ in order) + " |")
    for mhz in sorted(opps):
        print("| %d | " % mhz + " | ".join(str(opps[mhz].get(g, "")) for g in order) + " |")
    print()


for n in ["gpu-opp-table", "npu-opp-table", "cluster0-opp-table",
          "cluster1-opp-table", "dmc-opp-table", "vop-opp-table"]:
    report(n)
