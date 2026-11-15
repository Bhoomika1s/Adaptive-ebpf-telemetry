#!/usr/bin/env python3
import csv, glob, math, os, re, statistics, subprocess, sys
T = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447}
here = os.path.dirname(os.path.abspath(__file__))

def load(d):
    if not os.path.exists(d + "/runs.csv"):
        subprocess.run([sys.executable, here + "/analyze.py", d], stdout=subprocess.DEVNULL)
    out = {}
    for r in csv.DictReader(open(d + "/runs.csv")):
        if r["valid"] == "0" or not r["coverage"] or not r["cpu_pct_one_core"]:
            continue
        out[(r["policy"], int(r["seed"]))] = {k: float(r[k]) if r[k] else float("nan") for k in
            ("coverage", "cpu_pct_one_core", "recall", "false_alarms_per_hour", "esc_delay_s")} | {"load": r["load"]}
    return out

def ci(v):
    n = len(v); m = sum(v) / n
    return m, (T.get(n - 1, 1.96) * statistics.stdev(v) / math.sqrt(n) if n > 1 else float("nan"))

def mean(rows, k):
    v = [r[k] for r in rows if r[k] == r[k]]
    return sum(v) / len(v) if v else float("nan")

dirs = sys.argv[1:]
base = load(dirs[0])
groups = {}
for p in ("med", "high"):
    rows = {s: r for (pp, s), r in base.items() if pp == p}
    if rows: groups["fixed-" + p] = rows
for i, d in enumerate(dirs):
    data = base if i == 0 else load(d)
    rows = {s: r for (pp, s), r in data.items() if pp == "adaptive"}
    if rows: groups["adaptive:" + os.path.basename(d.rstrip("/"))] = rows
print(f"{'setting':34}{'n':>2} {'coverage':>8} {'cost%':>6} {'recall':>6} {'fa/h':>6} {'delay_s':>7} {'Q=cov/cost':>10}")
for name, rows in groups.items():
    rr = list(rows.values())
    c, k = mean(rr, "coverage"), mean(rr, "cpu_pct_one_core")
    print(f"{name:34}{len(rr):>2} {c:8.3f} {k:6.2f} {mean(rr,'recall'):6.2f} {mean(rr,'false_alarms_per_hour'):6.1f} {mean(rr,'esc_delay_s'):7.2f} {c/k:10.3f}")
for name, rows in groups.items():
    if not name.startswith("adaptive"): continue
    for b in ("fixed-med", "fixed-high"):
        if b not in groups: continue
        seeds = sorted(set(rows) & set(groups[b]))
        if len(seeds) < 2: continue
        q = [(rows[s]["coverage"] / rows[s]["cpu_pct_one_core"]) / (groups[b][s]["coverage"] / groups[b][s]["cpu_pct_one_core"]) for s in seeds]
        dc = [rows[s]["coverage"] - groups[b][s]["coverage"] for s in seeds]
        dk = [rows[s]["cpu_pct_one_core"] - groups[b][s]["cpu_pct_one_core"] for s in seeds]
        print(f"{name} vs {b} (n={len(seeds)}): Q ratio {ci(q)[0]:.2f}+/-{ci(q)[1]:.2f}  coverage {ci(dc)[0]:+.3f}+/-{ci(dc)[1]:.3f}  cost {ci(dk)[0]:+.3f}+/-{ci(dk)[1]:.3f}")
for d in dirs:
    vals = []
    for f in sorted(glob.glob(d + "/*_adaptive_*.sum.txt")):
        m = re.search(r"BUDGET budget_pct=([\d.]+).*mean_cost_pct=([\d.]+)", open(f).read())
        if m: vals.append((float(m.group(1)), float(m.group(2)), os.path.basename(f).split(".")[0]))
    if vals:
        over = [v for v in vals if v[1] > v[0]]
        print(f"budget {vals[0][0]}% in {d}: controller mean cost per run {[v[1] for v in vals]}  runs over budget: {len(over)} of {len(vals)}")
