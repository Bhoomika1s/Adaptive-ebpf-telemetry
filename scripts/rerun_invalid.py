
import csv, glob, os, shlex, subprocess, sys

RES = next((a for a in sys.argv[1:] if not a.startswith("--")), "results")
GO = "--go" in sys.argv
DROP = {"--seeds", "--loads", "--policies"}
n = 0
for folder in sorted(glob.glob(os.path.join(RES, "final_*"))):
    rp, ep = os.path.join(folder, "runs.csv"), os.path.join(folder, "env.txt")
    if not (os.path.exists(rp) and os.path.exists(ep)):
        continue
    bad = [r for r in csv.DictReader(open(rp)) if r.get("valid") == "0"]
    if not bad:
        continue
    args = None
    for line in open(ep):
        if line.startswith("args: "):
            args = shlex.split(line[len("args: "):])
            break
    if not args:
        print("# no args line in", ep)
        continue
    # env.txt stores sys.argv joined by spaces, so quotes are lost: regroup the tokens by option
    KNOWN = {"--seeds", "--loads", "--reps", "--duration", "--policies", "--out", "--gen-args", "--ctl-args",
             "--order-seed", "--cooldown", "--sample-n", "--dry-run"}
    toks, keep, i = args[1:], [], 0          # args[0] is the script path
    while i < len(toks):
        opt, vals = toks[i], []
        i += 1
        while i < len(toks) and toks[i] not in KNOWN:
            vals.append(toks[i]); i += 1
        if opt in DROP:
            continue
        if opt in ("--gen-args", "--ctl-args"):
            keep += [opt, " ".join(vals)]    
        else:
            keep += [opt] + vals
    for r in bad:
        cmd = [sys.executable, "scripts/run_experiments.py"] + keep + [
            "--seeds", r["seed"], "--loads", r["load"], "--policies", r["policy"]]
        n += 1
        print(f"# {folder}: {r['load']}_{r['policy']}_s{r['seed']}")
        print(shlex.join(cmd))
        if GO:
            subprocess.run(cmd)
print(f"# {n} run(s) {'re-run' if GO else 'to re-run (add --go with sudo to do it)'}")
