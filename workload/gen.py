#!/usr/bin/env python3

import argparse, csv, json, os, random, select, threading, time
from multiprocessing import Process, Value, Array


def busy(end):
    while time.time() < end:
        pass


def service(end, base_sleep, work, idx, counts, lat, drift):
    n, lsum = 0, 0.0
    while time.time() < end:
        t = time.perf_counter()
        x = 0
        for i in range(work):
            x += i * i
        time.sleep(base_sleep * drift.value)
        lsum += time.perf_counter() - t
        n += 1
        counts[idx] = n
        lat[idx] = lsum


def pingpong(end, rfd, wfd, starter):
    if starter:
        os.write(wfd, b"x")
    while time.time() < end:
        r, _, _ = select.select([rfd], [], [], 0.2)
        if r:
            os.read(rfd, 1)
            os.write(wfd, b"x")


def flicker(end, seed):
    """Borderline contention: each process draws a new duty cycle (30-70%) every
    0.15-0.4 s, so the machine-wide busy fraction hovers around 0.5, near the
    controller's medium threshold. Seeded, so the pattern is repeatable."""
    r = random.Random(seed)
    while time.time() < end:
        duty = r.uniform(0.3, 0.7)
        seg = min(end, time.time() + r.uniform(0.15, 0.4))
        while time.time() < seg:
            stop = time.time() + 0.02 * duty
            while time.time() < stop:
                pass
            time.sleep(0.02 * (1 - duty))


def run_anomaly(kind, dur, seed=0):
    end = time.time() + dur
    ncpu = os.cpu_count()
    procs, fds = [], []
    if kind == "cpu":
        procs = [Process(target=busy, args=(end,)) for _ in range(ncpu)]
    elif kind == "flicker":
        procs = [Process(target=flicker, args=(end, seed * 1000 + i)) for i in range(ncpu)]
    else:  # "ctx": context-switch burst
        for _ in range(ncpu):
            p1, p2 = os.pipe(), os.pipe()
            fds += [*p1, *p2]
            procs.append(Process(target=pingpong, args=(end, p2[0], p1[1], True)))
            procs.append(Process(target=pingpong, args=(end, p1[0], p2[1], False)))
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    for fd in fds:
        os.close(fd)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--seed", type=int, default=1)
    a.add_argument("--duration", type=float, default=60, help="total seconds")
    a.add_argument("--load", choices=["low", "high"], default="low")
    a.add_argument("--workers", type=int, default=None, help="override service workers")
    a.add_argument("--sleep-ms", type=float, default=None, help="override service sleep per request")
    a.add_argument("--work", type=int, default=3000, help="compute loop iterations per request")
    a.add_argument("--drift-factor", type=float, default=1.0,
                   help="at half time the service sleep is multiplied by this (workload drift)")
    a.add_argument("--gap", type=float, nargs=2, default=[5, 10], help="normal gap range (s)")
    a.add_argument("--anom", type=float, nargs=2, default=[1, 3], help="anomaly length range (s)")
    a.add_argument("--kinds", nargs="+", default=["cpu", "ctx"])
    a.add_argument("--out", default="results/groundtruth.csv")
    a.add_argument("--bench-out", default="")
    a = a.parse_args()

    rng = random.Random(a.seed)
    sched, t = [], 0.0
    while True:
        t += rng.uniform(*a.gap)
        d = rng.uniform(*a.anom)
        if t + d > a.duration:
            break
        sched.append((t, d, rng.choice(a.kinds)))
        t += d

    workers, sleep_ms = (2, 5.0) if a.load == "low" else (6, 0.5)
    if a.workers is not None:
        workers = a.workers
    if a.sleep_ms is not None:
        sleep_ms = a.sleep_ms

    counts = Array("d", [0.0] * workers, lock=False)
    lat = Array("d", [0.0] * workers, lock=False)
    drift = Value("d", 1.0, lock=False)

    t0 = time.time()
    end = t0 + a.duration
    bg = [Process(target=service, args=(end, sleep_ms / 1000.0, a.work, i, counts, lat, drift))
          for i in range(workers)]
    for p in bg:
        p.start()
    if a.drift_factor != 1.0:
        tm = threading.Timer(a.duration / 2, lambda: setattr(drift, "value", a.drift_factor))
        tm.daemon = True
        tm.start()

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "kind", "start_epoch", "end_epoch", "planned_duration_s", "seed", "load"])
        for i, (off, d, kind) in enumerate(sched):
            time.sleep(max(0, t0 + off - time.time()))
            s = time.time()
            run_anomaly(kind, d, a.seed * 100 + i)
            w.writerow([i, kind, f"{s:.6f}", f"{time.time():.6f}", f"{d:.3f}", a.seed, a.load])
            f.flush()
            print(f"anomaly {i}: {kind} at +{off:.1f}s for {d:.1f}s")
    time.sleep(max(0, end - time.time()))
    for p in bg:
        p.join()

    total = sum(counts)
    if a.bench_out:
        os.makedirs(os.path.dirname(a.bench_out) or ".", exist_ok=True)
        with open(a.bench_out, "w") as f:
            json.dump({"seed": a.seed, "load": a.load, "duration_s": a.duration,
                       "workers": workers, "sleep_ms": sleep_ms, "work": a.work,
                       "drift_factor": a.drift_factor, "requests": total,
                       "req_per_s": total / a.duration,
                       "mean_latency_ms": 1000.0 * sum(lat) / total if total else None}, f)
    print(f"done: {len(sched)} anomalies, {total:.0f} requests, log in {a.out}")


if __name__ == "__main__":
    main()
