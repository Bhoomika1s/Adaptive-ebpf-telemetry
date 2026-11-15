#!/usr/bin/env python3

import csv, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RES = sys.argv[1] if len(sys.argv) > 1 else "results"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(RES, "figs")
os.makedirs(OUT, exist_ok=True)

# colour-blind-safe palette
C = {"low": "#0072B2", "med": "#E69F00", "high": "#D55E00", "adaptive": "#009E73",
     "budget": "#CC79A7", "grey": "#666666"}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def summary(folder):
    """{(load, policy): row} from <results>/<folder>/summary.csv, or {} if missing."""
    p = os.path.join(RES, folder, "summary.csv")
    if not os.path.exists(p):
        print("skip (missing):", p)
        return {}
    with open(p) as f:
        return {(r["load"], r["policy"]): r for r in csv.DictReader(f)}


def get(rows, load, policy, key):
    r = rows.get((load, policy))
    return (num(r[key]), num(r.get(key + "_ci95"))) if r else (float("nan"), float("nan"))


def finish(fig, name):
    fig.tight_layout()
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=200)
    plt.close(fig)
    print("wrote", path)


# paired helpers (same seed = same workload and anomalies)
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}


def runs(folder, load=None, policy=None):
    """{seed: row} from <results>/<folder>/runs.csv"""
    p = os.path.join(RES, folder, "runs.csv")
    if not os.path.exists(p):
        print("skip (missing):", p)
        return {}
    with open(p) as f:
        return {int(r["seed"]): r for r in csv.DictReader(f)
                if r.get("valid", "1") != "0"
                and (load is None or r["load"] == load) and (policy is None or r["policy"] == policy)}


def mci(v):
    v = [x for x in v if x == x]
    n = len(v)
    if n == 0:
        return float("nan"), float("nan")
    m = sum(v) / n
    if n < 2:
        return m, float("nan")
    sd = (sum((x - m) ** 2 for x in v) / (n - 1)) ** 0.5
    return m, T975.get(n - 1, 1.96) * sd / n ** 0.5


def vals(rows, metric, seeds=None):
    return {s: num(r.get(metric)) for s, r in rows.items() if seeds is None or s in seeds}


def paired(A, B, metric, ratio=False):
    a, b = vals(A, metric), vals(B, metric)
    out = []
    for s in sorted(set(a) & set(b)):
        if a[s] == a[s] and b[s] == b[s] and (not ratio or b[s] > 0):
            out.append(a[s] / b[s] if ratio else a[s] - b[s])
    return out


#  Fig 1: cost (relative to fixed-high, same seed) vs coverage
def fig_pareto():
    hi = runs("final_main", "high", "high")
    if not hi:
        return
    fig, ax = plt.subplots(figsize=(5.4, 3.7))
    def put(rows, label, color, marker, note=None):
        cr = paired(rows, hi, "cpu_pct_one_core", ratio=True)
        cov = [x for x in vals(rows, "coverage").values() if x == x]
        x, xe = mci([100 * c for c in cr]); y, ye = mci(cov)
        ax.errorbar(x, y, xerr=xe, yerr=ye, fmt=marker, ms=7, color=color, capsize=2, label=label)
        if note:
            ax.annotate(note, (x, y), textcoords="offset points", xytext=(5, -11), fontsize=7)
        return x, y
    for pol, lab, mk in (("low", "fixed low", "o"), ("med", "fixed medium", "o"), ("high", "fixed high", "o"),
                         ("adaptive", "adaptive (default)", "s")):
        put(runs("final_main", "high", pol), lab, C[pol], mk)
    pts = []
    for b in ("1.0", "1.5", "2.5", "4.0"):
        rows = runs("final_budget_" + b, "high", "adaptive")
        if rows:
            pts.append((rows, b))
    xs = []
    for rows, b in pts:
        x, y = put(rows, "budget controller" if not xs else None, C["budget"], "D", b + "%")
        xs.append((x, y))
    if len(xs) > 1:
        ax.plot([p[0] for p in xs], [p[1] for p in xs], "-", color=C["budget"], lw=1, zorder=1)
    ax.set_xlabel("telemetry cost, % of fixed-high cost on the same seed")
    ax.set_ylabel("coverage of events during anomalies")
    ax.set_ylim(-0.05, 1.1); ax.grid(alpha=0.25); ax.legend(fontsize=7, loc="lower right")
    ax.set_title("Cost vs. coverage (high load, 5 seeds, paired by seed)", fontsize=9)
    finish(fig, "fig_cost_vs_coverage.png")


#  Fig 2: budget sweep: coverage, and measured cost per run against the budget
def fig_budget():
    import re, glob
    budgets, cov, costs = [], [], []
    for b in ("1.0", "1.5", "2.5", "4.0"):
        rows = runs("final_budget_" + b, "high", "adaptive")
        if not rows:
            continue
        budgets.append(float(b))
        cov.append([x for x in vals(rows, "coverage").values() if x == x])
        c = []
        for f in sorted(glob.glob(os.path.join(RES, "final_budget_" + b, "*.sum.txt"))):
            m = re.search(r"mean_cost_pct=([\d.]+)", open(f).read())
            if m:
                c.append(float(m.group(1)))
        costs.append(c)
    if not budgets:
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.1))
    for b, v in zip(budgets, cov):
        m, e = mci(v)
        a1.errorbar(b, m, yerr=min(e, m, 1 - m) if e == e else 0, fmt="o", color=C["budget"], capsize=3)
        a1.plot([b] * len(v), v, ".", color=C["grey"], alpha=0.6, ms=5)
    a1.plot(budgets, [mci(v)[0] for v in cov], "-", color=C["budget"], lw=1)
    main = runs("final_main", "high", "adaptive")
    if main:
        a1.axhline(mci([x for x in vals(main, "coverage").values() if x == x])[0], color=C["adaptive"], ls=":", label="no budget")
        a1.legend(fontsize=7, loc="lower right")
    for b, v in zip(budgets, costs):
        a2.plot([b] * len(v), v, "o", color=C["budget"], alpha=0.8, ms=5)
    a2.plot([0.5, 4.3], [0.5, 4.3], "--", color=C["grey"], lw=1, label="budget")
    a2.set_xlim(0.7, 4.3); a2.set_ylim(0, 4.3)
    a1.set_ylabel("coverage (mean; dots = seeds)"); a2.set_ylabel("measured cost per run\n(% of one core)", fontsize=9)
    for a in (a1, a2):
        a.set_xlabel("declared budget (% of one core)"); a.grid(alpha=0.25)
    a1.set_ylim(0, 1.05); a2.legend(fontsize=7, loc="upper left")
    fig.suptitle("Budget controller (dots = seeds; right panel: below the dashed line = budget met)", fontsize=8)
    finish(fig, "fig_budget.png")


#  Fig 3: mode timeline of one run 
def fig_timeline(load="high", seed=101):
    base = os.path.join(RES, "final_main", f"{load}_adaptive_s{seed}_r0")
    if not (os.path.exists(base + ".tel.csv") and os.path.exists(base + ".gt.csv")):
        print("skip (missing):", base + ".tel.csv")
        return
    t, ep, mode, sw, busy = [], [], [], [], []
    with open(base + ".tel.csv") as f:
        for r in csv.DictReader(f):
            try:
                row = (float(r["t"]), float(r["epoch"]), int(r["mode"]), float(r["switch_rate"]), float(r["busy_frac"]))
            except (TypeError, ValueError, KeyError):
                continue
            t.append(row[0]); ep.append(row[1]); mode.append(row[2]); sw.append(row[3]); busy.append(row[4])
    gt = []
    with open(base + ".gt.csv") as f:
        for r in csv.DictReader(f):
            gt.append((float(r["start_epoch"]), float(r["end_epoch"]), r["kind"]))
    e0 = ep[0] - t[0]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.2, 3.8), sharex=True, gridspec_kw={"height_ratios": [1, 1]})
    for s, e, k in gt:
        for a in (a1, a2):
            a.axvspan(s - e0, e - e0, color="#999999", alpha=0.25, lw=0)
    a1.step(t, mode, where="post", color=C["adaptive"])
    a1.set_yticks([0, 1, 2]); a1.set_yticklabels(["low", "medium", "high"]); a1.set_ylabel("telemetry mode")
    a2.plot(t, busy, color=C["low"], label="busy fraction")
    a2.set_ylabel("busy fraction", color=C["low"]); a2.set_xlabel("time (s)")
    a3 = a2.twinx()
    a3.plot(t, sw, color=C["high"], lw=0.8, label="switch rate")
    a3.set_yscale("log"); a3.set_ylabel("switches/s", color=C["high"])
    a1.set_title(f"Adaptive controller over one run ({load} load, seed {seed}); grey = injected anomalies", fontsize=9)
    finish(fig, "fig_timeline.png")


#  Fig 4: anomaly duration: adaptive minus fixed-high, paired by seed 
def fig_duration():
    groups = [("0.3-0.6 s", "final_dur_short"), ("1-3 s\n(default)", "final_main"), ("4-8 s", "final_dur_long")]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.0))
    for i, (lab, folder) in enumerate(groups):
        ad, hi = runs(folder, "low", "adaptive"), runs(folder, "low", "high")
        if not ad or not hi:
            continue
        for a, metric in ((a1, "coverage"), (a2, "cpu_pct_one_core")):
            d = paired(ad, hi, metric)
            m, e = mci(d)
            a.errorbar(i, m, yerr=e, fmt="o", color=C["adaptive"], capsize=4, ms=7)
            a.plot([i] * len(d), d, ".", color=C["grey"], alpha=0.6, ms=5)
    for a, yl in ((a1, "coverage: adaptive - fixed-high"), (a2, "cost: adaptive - fixed-high (% of one core)")):
        a.axhline(0, color="black", lw=0.8)
        a.set_xticks(range(3)); a.set_xticklabels([g[0] for g in groups], fontsize=8)
        a.set_xlabel("anomaly duration"); a.set_ylabel(yl, fontsize=8); a.grid(alpha=0.25, axis="y")
    fig.suptitle("Effect of anomaly duration (low load; dots = seeds, bars = 95% CI of paired difference)", fontsize=8)
    finish(fig, "fig_duration.png")


#  Fig 5: hysteresis / dwell on borderline anomalies 
def fig_h2():
    names = [("default", "default"), ("nohyst", "no\nhysteresis"), ("nodwell", "no dwell"),
             ("neither", "neither\n(+recover=1)")]
    data = [(lab, runs("final_h2_" + n, "low", "adaptive")) for n, lab in names]
    data = [(l, r) for l, r in data if r]
    if not data:
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.1))
    for i, (lab, r) in enumerate(data):
        tr = [x for x in vals(r, "transitions_per_min").values() if x == x]
        m, e = mci(tr)
        a1.bar(i, m, yerr=e, color=C["adaptive"], capsize=3)
        fa = [x for x in vals(r, "false_alarms_per_hour").values() if x == x]
        a2.plot([i] * len(fa), fa, "o", color=C["high"], alpha=0.7, ms=6)
    for a in (a1, a2):
        a.set_xticks(range(len(data))); a.set_xticklabels([l for l, _ in data], fontsize=7)
        a.grid(alpha=0.25, axis="y")
    a1.set_ylabel("transitions / min\n(mean, 95% CI)", fontsize=9)
    a2.set_ylabel("false alarms / hour\n(dot = one seed)", fontsize=9)
    fig.suptitle("Borderline (flicker) anomalies: what hysteresis and dwell buy", fontsize=9)
    finish(fig, "fig_hysteresis.png")


if __name__ == "__main__":
    for fn in (fig_pareto, fig_budget, fig_timeline, fig_duration, fig_h2):
        try:
            fn()
        except Exception as e:      # never let one figure stop the others
            print("figure failed:", fn.__name__, e)
