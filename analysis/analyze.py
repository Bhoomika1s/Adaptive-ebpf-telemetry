import argparse, csv, glob, json, math, os, re, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import load_gt, load_tel, score_run

T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201,
       2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074,
       2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]
NAN = float("nan")
NAME = re.compile(r"^([a-z]+)_([a-z]+)_s(\d+)_r(\d+)$")
METRICS = ["overhead_pct", "mean_latency_ms", "collector_cpu_s", "ebpf_cpu_s", "total_cpu_s",
           "cpu_pct_one_core", "ebpf_ns_per_switch", "maxrss_kb", "coverage",
           "coverage_per_anomaly", "esc_delay_s", "high_delay_s", "ctr_delay_s",
           "esc_missed", "ctr_missed", "false_alarms_per_hour", "frac_low", "frac_med",
           "frac_high", "transitions_per_min", "emitted", "dropped", "drop_rate",
           "recall", "precision", "clock_step_s", "gt_ambiguous"]


def mean_ci(vals):
    v = [x for x in vals if x is not None and x == x]
    n = len(v)
    if n == 0:
        return NAN, NAN
    m = sum(v) / n
    if n < 2:
        return m, NAN
    t = T95[n - 2] if n - 1 <= 30 else 1.96
    return m, t * statistics.stdev(v) / math.sqrt(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--sw-med", type=float, default=20000.0)
    ap.add_argument("--busy-med", type=float, default=0.5)
    a = ap.parse_args()

    runs = []
    for gtp in sorted(glob.glob(os.path.join(a.dir, "*.gt.csv"))):
        stem = os.path.basename(gtp)[:-len(".gt.csv")]
        m = NAME.match(stem)
        if not m:
            continue
        load, policy, seed, rep = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
        base = os.path.join(a.dir, stem)
        row = {"load": load, "policy": policy, "seed": seed, "rep": rep}
        try:
            with open(base + ".bench.json") as f:
                b = json.load(f)
            row["req_per_s"] = b["req_per_s"]
            row["mean_latency_ms"] = b["mean_latency_ms"]
            row["bench_duration"] = b["duration_s"]
        except (OSError, ValueError):
            row["req_per_s"] = None
            row["mean_latency_ms"] = None
            row["bench_duration"] = None
        try:
            with open(base + ".sum.txt") as f:
                txt = f.read()
        except OSError:
            txt = ""

        def field(key):
            mm = re.search(key + r"=([\d.]+)", txt)
            return float(mm.group(1)) if mm else None

        row["collector_cpu_s"] = field("collector_cpu_s")
        row["ebpf_cpu_s"] = field("ebpf_cpu_s")
        row["maxrss_kb"] = field("maxrss_kb")
        sw_ns, sw_cnt = field("ebpf_switch_ns"), field("ebpf_switch_cnt")
        row["ebpf_ns_per_switch"] = sw_ns / sw_cnt if sw_ns is not None and sw_cnt else None
        if policy != "none" and os.path.exists(base + ".tel.csv"):
            row.update(score_run(load_tel(base + ".tel.csv"), load_gt(gtp),
                                 a.sw_med, a.busy_med))
        # a wall-clock step (e.g. WSL2 time sync) makes the ground truth and the scoring invalid
        row["valid"] = 0 if (row.get("gt_ambiguous") or 0) > 0 else 1
        if row.get("collector_cpu_s") is not None and row.get("ebpf_cpu_s") is not None:
            row["total_cpu_s"] = row["collector_cpu_s"] + row["ebpf_cpu_s"]
            # denominator: how long the collector really ran, on the steady clock (a wall-clock
            # duration would be inflated by a VM pause); fall back to the workload's duration
            dur = row.get("duration_s") or row.get("bench_duration")
            if dur:
                row["cpu_pct_one_core"] = 100.0 * row["total_cpu_s"] / dur
        runs.append(row)

    if not runs:
        sys.exit("no runs found in " + a.dir)

    base_by = {}
    for r in runs:
        if r["policy"] == "none" and r.get("req_per_s"):
            base_by.setdefault((r["load"], r["seed"]), []).append(r["req_per_s"])
    base_load = {}
    for (load, _), v in base_by.items():
        base_load.setdefault(load, []).extend(v)
    for r in runs:
        b = base_by.get((r["load"], r["seed"])) or base_load.get(r["load"])
        if r["policy"] != "none" and r.get("req_per_s") and b:
            r["overhead_pct"] = (1.0 - r["req_per_s"] / (sum(b) / len(b))) * 100.0
        else:
            r["overhead_pct"] = None

    cols = ["load", "policy", "seed", "rep", "req_per_s"] + METRICS + ["valid"]
    with open(os.path.join(a.dir, "runs.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in runs:
            w.writerow([r.get(c, "") if r.get(c) is not None else "" for c in cols])

    order = {"none": 0, "low": 1, "med": 2, "high": 3, "adaptive": 4}
    groups = {}
    bad = [r for r in runs if r.get("valid") == 0]
    for r in bad:
        print(f"WARNING invalid run (an anomaly boundary fell inside a {r['clock_step_s']:.1f} s wall-clock step): "
              f"{r['load']}_{r['policy']}_s{r['seed']}_r{r['rep']} -> excluded from summary.csv; re-run it")
    for r in runs:
        if r.get("valid") == 0:
            continue
        groups.setdefault((r["load"], r["policy"]), []).append(r)
    summ = []
    for (load, policy) in sorted(groups, key=lambda k: (k[0], order.get(k[1], 9))):
        g = groups[(load, policy)]
        row = {"load": load, "policy": policy, "n": len(g)}
        for mname in METRICS:
            m_, ci_ = mean_ci([r.get(mname) for r in g])
            row[mname] = m_
            row[mname + "_ci95"] = ci_
        summ.append(row)
    scols = ["load", "policy", "n"] + [c for mname in METRICS for c in (mname, mname + "_ci95")]
    with open(os.path.join(a.dir, "summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(scols)
        for row in summ:
            w.writerow(["" if (isinstance(row[c], float) and row[c] != row[c]) else row[c]
                        for c in scols])

    def fmt(x, p=2):
        return "-" if x is None or x != x else f"{x:.{p}f}"

    print(f"{'load':5} {'policy':9} {'n':>2} {'overhead%':>16} {'cpu%/core':>10} {'ns/sw':>6} {'coverage':>9} "
          f"{'esc_delay_s':>11} {'fa/h':>6} {'high%':>6} {'trans/min':>9} {'drop%':>6} {'recall':>6} {'prec':>5}")
    for r in summ:
        oh = f"{fmt(r['overhead_pct'])}±{fmt(r['overhead_pct_ci95'])}"
        print(f"{r['load']:5} {r['policy']:9} {r['n']:>2} {oh:>16} {fmt(r['cpu_pct_one_core'], 3):>10} {fmt(r['ebpf_ns_per_switch'], 0):>6} "
              f"{fmt(r['coverage'], 3):>9} {fmt(r['esc_delay_s']):>11} "
              f"{fmt(r['false_alarms_per_hour'], 1):>6} {fmt(100 * r['frac_high'] if r['frac_high'] == r['frac_high'] else NAN, 1):>6} "
              f"{fmt(r['transitions_per_min'], 1):>9} {fmt(100 * r['drop_rate'] if r['drop_rate'] == r['drop_rate'] else NAN, 1):>6} "
              f"{fmt(r['recall'], 2):>6} {fmt(r['precision'], 2):>5}")
    print(f"\nwrote {os.path.join(a.dir, 'runs.csv')} and {os.path.join(a.dir, 'summary.csv')}")


if __name__ == "__main__":
    main()
