#!/usr/bin/env python3

import argparse, os, platform, random, shlex, subprocess, sys, time

MODE = {"low": 0, "med": 1, "high": 2, "adaptive": 3}


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
    except Exception as e:
        return f"(failed: {e})"


def write_env(a):
    name = "env.txt"
    if os.path.exists(os.path.join(a.out, name)):        # a re-run must not overwrite the original record
        name = "env_rerun_%d.txt" % int(time.time())
    with open(os.path.join(a.out, name), "w") as f:
        f.write("date: " + time.strftime("%Y-%m-%d %H:%M:%S %z") + "\n")
        f.write("args: " + " ".join(sys.argv) + "\n")
        for label, cmd in [("uname", "uname -a"), ("os", "head -2 /etc/os-release"),
                           ("cpu", "lscpu | grep -E 'Model name|^CPU\\(s\\)'"),
                           ("memory", "free -h | head -2"), ("clang", "clang --version | head -1"),
                           ("bpftool", "/usr/local/sbin/bpftool version | head -2"),
                           ("python", "python3 --version"),
                           ("git", "git rev-parse HEAD 2>/dev/null || echo none")]:
            f.write(f"--- {label}\n{sh(cmd)}\n")
        f.write(f"--- python platform\n{platform.platform()}\n")


def run_one(a, load, seed, rep, policy):
    stem = f"{load}_{policy}_s{seed}_r{rep}"
    base = os.path.join(a.out, stem)
    gen = [sys.executable, "workload/gen.py", "--seed", str(seed), "--duration", str(a.duration),
           "--load", load, "--out", base + ".gt.csv", "--bench-out", base + ".bench.json"]
    gen += shlex.split(a.gen_args)
    cmd = None
    if policy != "none":
        cmd = ["./telemetry", str(MODE[policy]), str(a.sample_n), str(a.duration + 2), "4096", "1"]
        if policy == "adaptive":
            cmd += shlex.split(a.ctl_args)
    if a.dry_run:
        print(" ".join(gen))
        if cmd:
            print("   " + " ".join(cmd))
        return
    g = subprocess.Popen(gen, stdout=subprocess.DEVNULL)
    if cmd:
        with open(base + ".tel.csv", "w") as out, open(base + ".sum.txt", "w") as err:
            subprocess.run(cmd, stdout=out, stderr=err)
    g.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--loads", nargs="+", default=["low", "high"])
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--policies", nargs="+", default=["none", "low", "med", "high", "adaptive"])
    ap.add_argument("--out", default="results/exp")
    ap.add_argument("--gen-args", default="")
    ap.add_argument("--ctl-args", default="")
    ap.add_argument("--order-seed", type=int, default=0)
    ap.add_argument("--sample-n", type=int, default=100, help="medium mode records 1 in N switches")
    ap.add_argument("--cooldown", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if any(p != "none" for p in a.policies) and os.geteuid() != 0 and not a.dry_run:
        sys.exit("eBPF needs root: run with  sudo python3 scripts/run_experiments.py ...")
    if any(p != "none" for p in a.policies) and not os.path.exists("./telemetry") and not a.dry_run:
        sys.exit("./telemetry not found: run  make telemetry  and start from the project folder")
    os.makedirs(a.out, exist_ok=True)
    if not a.dry_run:
        write_env(a)

    rnd = random.Random(a.order_seed)
    blocks = [(load, seed, rep) for rep in range(a.reps) for seed in a.seeds for load in a.loads]
    total = len(blocks) * len(a.policies)
    k = 0
    for load, seed, rep in blocks:
        pols = list(a.policies)
        rnd.shuffle(pols)
        for policy in pols:
            k += 1
            print(f"[{k}/{total}] load={load} seed={seed} rep={rep} policy={policy}", flush=True)
            run_one(a, load, seed, rep, policy)
            if not a.dry_run:
                time.sleep(a.cooldown)

    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid and not a.dry_run:
        subprocess.run(["chown", "-R", f"{uid}:{gid}", a.out])
    print("done ->", a.out)


if __name__ == "__main__":
    main()
