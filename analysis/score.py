#!/usr/bin/env python3
"""Score one telemetry run against the workload's ground-truth anomaly log.

Telemetry CSV row i describes the interval [epoch_i - dt_i, epoch_i]; its `mode`
is the telemetry level that was IN EFFECT during that interval.

Metrics (all definitions are choices, state them in the report):
  coverage            events recorded (emitted) during anomalies / switches that
                      occurred during anomalies. Event-weighted ("coverage") and
                      mean over anomalies ("coverage_per_anomaly").
  esc_delay_s         adaptive only: time from anomaly start until the telemetry
                      level was >= 1 (high_delay_s: until level 2).
  ctr_delay_s         time from anomaly start until the always-on counters (switch
                      rate / busy fraction) cross the medium thresholds; the same
                      signal for every policy.
  *_missed            anomalies with no escalation / no counter crossing in
                      [start, end + grace].
  false_alarms        elevated episodes (contiguous mode >= 1) overlapping no anomaly.
  recall              (anomalies - esc_missed) / anomalies   (meaningful for adaptive only)
  precision           (episodes - false_alarms) / episodes   (meaningful for adaptive only; fixed
                      modes are "elevated" for the whole run, so their precision is trivial)
"""
import csv

NAN = float("nan")


def load_gt(path):
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            out.append({"kind": r["kind"], "start": float(r["start_epoch"]),
                        "end": float(r["end_epoch"])})
    return out


def load_tel(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                row = {k: float(r[k]) for k in
                       ("t", "epoch", "switch_rate", "busy_frac", "emitted", "dropped", "consumed")}
                row["mode"] = int(r["mode"])
                # older CSVs have no self_skipped column; treat as 0
                row["self_skipped"] = float(r["self_skipped"]) if r.get("self_skipped") else 0.0
            except (TypeError, ValueError, KeyError):
                continue          # truncated line from an interrupted run: skip it
            rows.append(row)
    for i, r in enumerate(rows):
        # interval length from the steady clock t (CLOCK_MONOTONIC), never from the wall clock:
        # the wall clock can jump (WSL2 time sync / VM pause) and then dt would be hundreds of seconds
        r["dt"] = (r["t"] - rows[i - 1]["t"]) if i > 0 else (r["t"] if r["t"] > 0 else 0.2)
    return rows


def gt_to_t(gt, tel):
    import bisect
    eps = [r["epoch"] for r in tel]
    monotone = all(eps[i] <= eps[i + 1] for i in range(len(eps) - 1))
    amb = 0

    def conv(g):
        nonlocal amb
        if not monotone:                       # backward clock step: cannot map reliably
            amb += 1
            return g - eps[0]
        i = bisect.bisect_left(eps, g)
        if i == 0:
            return tel[0]["t"] - (tel[0]["epoch"] - g)
        if i == len(tel):
            return tel[-1]["t"] + (g - tel[-1]["epoch"])
        e0, e1, t0, t1 = tel[i - 1]["epoch"], tel[i]["epoch"], tel[i - 1]["t"], tel[i]["t"]
        if abs((e1 - e0) - (t1 - t0)) > 0.3:   # g lies inside a wall-clock step
            amb += 1
            return t1
        return t1 - (e1 - g)

    out = [dict(a, start=conv(a["start"]), end=conv(a["end"])) for a in gt]
    return out, amb


def _mean(v):
    return sum(v) / len(v) if v else NAN


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def score_run(tel, gt, sw_med=20000.0, busy_med=0.5, grace=1.0):
    if not tel:
        return {}
    gt, ambiguous = gt_to_t(gt, tel)          # from here on every time is on the steady clock t
    total_dur = sum(r["dt"] for r in tel)
    cov_num = cov_den = 0.0
    per, esc_d, high_d, ctr_d = [], [], [], []
    esc_missed = ctr_missed = 0

    for a in gt:
        num = den = 0.0
        esc = high = ctr = None
        for r in tel:
            s0 = r["t"] - r["dt"]
            w = _overlap(s0, r["t"], a["start"], a["end"])
            if w > 0:
                f = w / r["dt"]
                num += f * r["emitted"]
                # the collector's own switches are never recorded, so they are not "missed"
                den += f * max(0.0, r["switch_rate"] * r["dt"] - r["self_skipped"])
            if r["t"] <= a["start"] or s0 > a["end"] + grace:
                continue
            if esc is None and r["mode"] >= 1:
                esc = max(0.0, s0 - a["start"])
            if high is None and r["mode"] >= 2:
                high = max(0.0, s0 - a["start"])
            if ctr is None and (r["switch_rate"] > sw_med or r["busy_frac"] > busy_med):
                ctr = max(0.0, r["t"] - a["start"])
        cov_num += num
        cov_den += den
        if den > 0:
            per.append(num / den)
        if esc is None:
            esc_missed += 1
        else:
            esc_d.append(esc)
        if high is not None:
            high_d.append(high)
        if ctr is None:
            ctr_missed += 1
        else:
            ctr_d.append(ctr)

    # elevated episodes and false alarms
    episodes, cur = [], None
    for r in tel:
        s0 = r["t"] - r["dt"]
        if r["mode"] >= 1:
            if cur is None:
                cur = [s0, r["t"]]
            else:
                cur[1] = r["t"]
        elif cur is not None:
            episodes.append(cur)
            cur = None
    if cur is not None:
        episodes.append(cur)
    false_eps = sum(1 for e in episodes
                    if not any(a["start"] < e[1] and a["end"] > e[0] for a in gt))

    frac = [0.0, 0.0, 0.0]
    for r in tel:
        frac[min(r["mode"], 2)] += r["dt"]
    transitions = sum(1 for i in range(1, len(tel)) if tel[i]["mode"] != tel[i - 1]["mode"])
    emitted = sum(r["emitted"] for r in tel)
    dropped = sum(r["dropped"] for r in tel)

    # wall-clock step detector: epoch (CLOCK_REALTIME) must advance like t (CLOCK_MONOTONIC)
    step = 0.0
    for i in range(1, len(tel)):
        step = max(step, abs((tel[i]["epoch"] - tel[i - 1]["epoch"]) - (tel[i]["t"] - tel[i - 1]["t"])))

    return {
        "clock_step_s": step,
        "gt_ambiguous": ambiguous,
        "n_anomalies": len(gt),
        "coverage": cov_num / cov_den if cov_den > 0 else NAN,
        "coverage_per_anomaly": _mean(per),
        "esc_delay_s": _mean(esc_d),
        "high_delay_s": _mean(high_d),
        "ctr_delay_s": _mean(ctr_d),
        "esc_missed": esc_missed,
        "ctr_missed": ctr_missed,
        "episodes": len(episodes),
        "false_alarms": false_eps,
        # recall: share of injected anomalies that got an escalation (medium or high) in time
        "recall": (len(gt) - esc_missed) / len(gt) if gt else NAN,
        # precision: share of elevated episodes (contiguous mode >= 1) that overlap a real anomaly
        "precision": (len(episodes) - false_eps) / len(episodes) if episodes else NAN,
        "false_alarms_per_hour": false_eps / (total_dur / 3600.0) if total_dur > 0 else NAN,
        "frac_low": frac[0] / total_dur,
        "frac_med": frac[1] / total_dur,
        "frac_high": frac[2] / total_dur,
        "transitions": transitions,
        "transitions_per_min": transitions / (total_dur / 60.0) if total_dur > 0 else NAN,
        "emitted": emitted,
        "dropped": dropped,
        "drop_rate": dropped / (emitted + dropped) if (emitted + dropped) > 0 else 0.0,
        "duration_s": total_dur,
    }


if __name__ == "__main__":
    import sys, json
    print(json.dumps(score_run(load_tel(sys.argv[1]), load_gt(sys.argv[2])), indent=2))
