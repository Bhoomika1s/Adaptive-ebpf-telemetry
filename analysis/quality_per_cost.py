#!/usr/bin/env python3

import csv, math, statistics, sys
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}

def ci(v):
    n = len(v)
    m = sum(v) / n
    return m, (T95.get(n - 1, 1.96) * statistics.stdev(v) / math.sqrt(n) if n > 1 else float("nan"))

d = sys.argv[1] if len(sys.argv) > 1 else "results/final_main"
base = sys.argv[2] if len(sys.argv) > 2 else "high"
rows = {}
for r in csv.DictReader(open(d + "/runs.csv")):
    if r["valid"] == "0" or not r["coverage"] or not r["cpu_pct_one_core"]:
        continue
    rows[(r["load"], r["policy"], int(r["seed"]))] = (float(r["coverage"]), float(r["cpu_pct_one_core"]))
for load in sorted({k[0] for k in rows}):
    seeds = sorted(s for (l, p, s) in rows if l == load and p == "adaptive" and (l, base, s) in rows)
    if len(seeds) < 2:
        continue
    q, dc, dk = [], [], []
    for s in seeds:
        ca, ka = rows[(load, "adaptive", s)]
        ch, kh = rows[(load, base, s)]
        q.append((ca / ka) / (ch / kh) if ch > 0 else float("nan")); dc.append(ca - ch); dk.append(ka - kh)
    print(f"load={load} n={len(seeds)} paired by seed, adaptive vs fixed-{base}")
    for name, v in ((f"Q ratio (coverage per cost, adaptive/{base})", q), ("coverage difference", dc), ("cost difference (points of a core)", dk)):
        v = [x for x in v if x == x]
        if len(v) < 2:
            print(f"  {name}: undefined (the baseline recorded no events)")
            continue
        m, c = ci(v)
        print(f"  {name}: {m:+.3f} +/- {c:.3f}")
