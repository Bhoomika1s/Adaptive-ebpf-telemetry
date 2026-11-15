#!/usr/bin/env python3

import csv, glob, math, os, re, sys

RES = sys.argv[1] if len(sys.argv) > 1 else "results"
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
        10: 2.228}
OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


def num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def runs(folder):
    p = os.path.join(RES, folder, "runs.csv")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return list(csv.DictReader(f))


def pick(rows, load=None, policy=None):
    return {int(r["seed"]): r for r in rows
            if r.get("valid", "1") != "0"
            and (load is None or r["load"] == load) and (policy is None or r["policy"] == policy)}


def mean_ci(d):
    n = len(d)
    if n == 0:
        return None
    m = sum(d) / n
    if n < 2:
        return m, float("nan"), n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
    return m, T975.get(n - 1, 1.96) * sd / math.sqrt(n), n


def compare(title, A, B, metric, labelA, labelB, rel=False, unit=""):
    """paired difference A - B on `metric` over the seeds both have"""
    if A is None or B is None:
        say(f"  [skip] {title}: missing data")
        return
    seeds = sorted(set(A) & set(B))
    d = []
    for s in seeds:
        a, b = num(A[s].get(metric)), num(B[s].get(metric))
        if a is not None and b is not None:
            d.append(a - b)
    r = mean_ci(d)
    if not r:
        say(f"  [skip] {title}: no paired values for {metric}")
        return
    m, ci, n = r
    sig = "significant" if ci == ci and abs(m) > ci else "NOT significant (inside noise)"
    meanB = sum(num(B[s][metric]) for s in seeds if num(B[s].get(metric)) is not None) / max(1, len(d))
    relpart = f"  ({100 * m / meanB:+.0f}% of {labelB})" if rel and meanB else ""
    say(f"  {title}: {labelA} - {labelB} = {m:+.3f} +- {ci:.3f} {unit} [n={n}] {sig}{relpart}")


def main():
    main_ = runs("final_main")
    say("== MAIN COMPARISON (paired by seed) ==")
    if main_:
        for load in ("high", "low"):
            ad, hi = pick(main_, load, "adaptive"), pick(main_, load, "high")
            lo, me = pick(main_, load, "low"), pick(main_, load, "med")
            say(f" load={load}")
            compare("cost, adaptive vs fixed-high", ad, hi, "cpu_pct_one_core", "adaptive", "high", rel=True, unit="% core")
            compare("coverage, adaptive vs fixed-high", ad, hi, "coverage", "adaptive", "high")
            compare("cost, adaptive vs fixed-medium", ad, me, "cpu_pct_one_core", "adaptive", "med", unit="% core")
            compare("coverage, adaptive vs fixed-medium", ad, me, "coverage", "adaptive", "med")
            compare("cost, fixed-high vs fixed-low", hi, lo, "cpu_pct_one_core", "high", "low", unit="% core")
            for s in sorted(ad):
                pass
            for key, name in (("recall", "recall"), ("precision", "precision"), ("esc_delay_s", "escalation delay (s)")):
                vals = [num(r.get(key)) for r in ad.values() if num(r.get(key)) is not None]
                if vals:
                    m, ci, n = mean_ci(vals)
                    say(f"  adaptive {name}: {m:.3f} +- {ci:.3f} [n={n}]")

    say("\n== HYSTERESIS / DWELL ON BORDERLINE (flicker) ANOMALIES, low load ==")
    base = runs("final_h2_default")
    for v in ("nohyst", "nodwell", "neither"):
        other = runs("final_h2_" + v)
        if base and other:
            say(f" variant: {v}")
            compare("transitions/min", pick(other), pick(base), "transitions_per_min", v, "default", unit="/min")
            compare("false alarms/hour", pick(other), pick(base), "false_alarms_per_hour", v, "default", unit="/h")
            compare("cost", pick(other), pick(base), "cpu_pct_one_core", v, "default", unit="% core")
    prom = runs("final_promote")
    if base and prom:
        say(" promotion rule vs default controller (same seeds, same flicker anomalies)")
        compare("coverage", pick(prom), pick(base), "coverage", "promote", "default")
        compare("cost", pick(prom), pick(base), "cpu_pct_one_core", "promote", "default", unit="% core")
        compare("escalation delay", pick(prom), pick(base), "esc_delay_s", "promote", "default", unit="s")

    say("\n== ABLATIONS ON CLEAN ANOMALIES, high load (vs adaptive default in final_main; cross-stage) ==")
    ref = pick(main_, "high", "adaptive") if main_ else None
    for v in ("nohyst", "nodwell", "neither", "sigsw", "sigbusy"):
        o = runs("final_abl_" + v)
        if o and ref:
            say(f" variant: {v}")
            compare("transitions/min", pick(o), ref, "transitions_per_min", v, "default", unit="/min")
            compare("coverage", pick(o), ref, "coverage", v, "default")
            compare("cost", pick(o), ref, "cpu_pct_one_core", v, "default", unit="% core")

    say("\n== ANOMALY DURATION, low load: adaptive vs fixed-high (paired) ==")
    for name, folder in (("short 0.3-0.6 s", "final_dur_short"), ("long 4-8 s", "final_dur_long")):
        r = runs(folder)
        if r:
            say(f" {name}")
            compare("cost", pick(r, "low", "adaptive"), pick(r, "low", "high"), "cpu_pct_one_core", "adaptive", "high", unit="% core")
            compare("coverage", pick(r, "low", "adaptive"), pick(r, "low", "high"), "coverage", "adaptive", "high")

    say("\n== DRIFT, high load ==")
    d = runs("final_drift")
    if d:
        compare("cost, adaptive vs fixed-high", pick(d, "high", "adaptive"), pick(d, "high", "high"), "cpu_pct_one_core", "adaptive", "high", rel=True, unit="% core")
        compare("coverage, adaptive vs fixed-high", pick(d, "high", "adaptive"), pick(d, "high", "high"), "coverage", "adaptive", "high")
        fa = [num(r.get("false_alarms_per_hour")) for r in pick(d, "high", "adaptive").values()]
        say(f"  false alarms/hour (adaptive, drift): {[x for x in fa if x is not None]}")

    say("\n== BUDGET CONTROLLER (high load) ==")
    for b in ("1.0", "1.5", "2.5", "4.0"):
        r = runs("final_budget_" + b)
        if not r:
            continue
        costs = []
        for p in sorted(glob.glob(os.path.join(RES, "final_budget_" + b, "*.sum.txt"))):
            m = re.search(r"mean_cost_pct=([\d.]+)", open(p).read())
            if m:
                costs.append(float(m.group(1)))
        cov = [num(x.get("coverage")) for x in r]
        cov = [c for c in cov if c is not None]
        over = sum(1 for c in costs if c > float(b))
        mc = mean_ci(costs)
        say(f" budget {b}% of a core: controller-measured mean cost per run = {[round(c, 2) for c in costs]}; "
            f"runs over budget: {over}/{len(costs)}; mean {mc[0]:.2f}; coverage mean {sum(cov) / len(cov):.3f}")
    h03, h10 = runs("final_budget_2.5"), runs("final_budget_2.5_h1.0")
    if h03 and h10:
        compare("budget 2.5: horizon 0.3 vs 1.0, coverage", pick(h03), pick(h10), "coverage", "h0.3", "h1.0")
        compare("budget 2.5: horizon 0.3 vs 1.0, cost", pick(h03), pick(h10), "cpu_pct_one_core", "h0.3", "h1.0", unit="% core")

    say("\n== HEALTHY RUNS (no anomalies): false-alarm rate ==")
    h = runs("final_healthy")
    if h:
        for load in ("low", "high"):
            rows = [r for r in h if r["load"] == load]
            hours = sum(num(r.get("duration_s")) or 0 for r in rows) / 3600.0 if rows and "duration_s" in rows[0] else None
            if hours is None:
                hours = len(rows) * 300.0 / 3600.0
            fa = sum((num(r.get("false_alarms_per_hour")) or 0) * ((num(r.get("duration_s")) or 300.0) / 3600.0) for r in rows)
            say(f" load={load}: {len(rows)} runs, {hours:.2f} h observed, false-alarm episodes = {fa:.0f}"
                + (f"; 95% upper bound on the rate ~ {3.0 / hours:.1f}/hour (rule of three)" if fa < 0.5 and hours > 0 else ""))

    with open(os.path.join(RES, "claims.txt"), "w") as f:
        f.write("\n".join(OUT) + "\n")
    print("\nwrote", os.path.join(RES, "claims.txt"))


if __name__ == "__main__":
    main()
