# Running the experiments (WSL2 / Ubuntu 22.04, kernel with BTF)

Project folder: ~/ebpf-adaptive  (bpf/, controller/, workload/, analysis/, scripts/, results/)

1. Build:          make telemetry          (needs bpf/vmlinux.h, bpftool, libbpf; see setup notes)
2. Tuning runs:    sudo python3 scripts/run_experiments.py --seeds 1 2 3 --loads low high --duration 30 --out results/tune
3. Analyse:        python3 analysis/analyze.py results/tune
4. FINAL runs use different seeds (never tune on them):
                   sudo python3 scripts/run_experiments.py --seeds 101 102 103 104 105 --loads low high --duration 60 --out results/final
                   python3 analysis/analyze.py results/final

Ablations (adaptive only), e.g. no hysteresis / no dwell:
   --policies none adaptive --ctl-args "--hyst 1.0 --dwell-ms 0 --recover 1"
Workload drift:   --gen-args "--drift-factor 0.2"
Brief anomalies:  --gen-args "--anom 0.3 0.6"   (process start-up takes ~50-100 ms, so
                  anomalies shorter than ~0.3 s are distorted; report this limitation)

Outputs per run in the output folder: <load>_<policy>_s<seed>_r<rep>.{gt.csv,bench.json,tel.csv,sum.txt}
plus env.txt (machine/tool versions), runs.csv (raw per-run table), summary.csv (mean, 95% CI).
Close other heavy Windows/WSL programs during runs and keep the laptop plugged in.

## Final evaluation (stages)
   sudo bash scripts/final_eval.sh main        # ~55 min  none/low/med/high/adaptive, both loads
   sudo bash scripts/final_eval.sh h2          # ~21 min  hysteresis/dwell ablation on borderline anomalies
   sudo bash scripts/final_eval.sh ablation    # ~27 min  hysteresis, dwell, controller signal
   sudo bash scripts/final_eval.sh drift       # ~21 min  workload drift
   sudo bash scripts/final_eval.sh dur         # ~21 min  brief and long anomalies
   sudo bash scripts/final_eval.sh healthy     # ~30 min  false alarms per hour (no anomalies)
   sudo bash scripts/final_eval.sh sens        # ~26 min  threshold / interval / sampling-rate sensitivity
   sudo bash scripts/final_eval.sh budget      # ~20 min  NEW: credit-bucket budget 1.0 / 1.5 / 2.5 / 4.0 % of a core, horizon 0.3 s, plus a 1.0 s horizon ablation (~27 min)
   sudo bash scripts/final_eval.sh promote     # ~11 min  NEW: promote sustained borderline anomalies to high
   sudo bash scripts/final_eval.sh all         # about 4 hours in total

New controller options (off by default; the original controller is unchanged without them):
   --budget PCT      cap the AVERAGE telemetry cost at PCT % of one core (credit bucket; also --budget-win SEC, --horizon SEC)
   --promote-s SEC   medium -> high if the anomaly persists for SEC seconds
Tune them on seeds 1-3 BEFORE the final runs, e.g.
   sudo python3 scripts/run_experiments.py --seeds 1 2 3 --loads high --duration 30 --policies adaptive \
        --ctl-args "--budget 1.5" --out results/tune_budget15
The adaptive runs' sum.txt gets an extra line:  BUDGET budget_pct forced_down capped_intervals promotions
final_sample_n mean_cost_pct
Then:  python3 analysis/analyze.py results/final_main   (and likewise for each results/final_* folder)
Preview any stage without running it:  DRY=1 bash scripts/final_eval.sh main
Do not change controller code or defaults once final runs start.

## Added later (see DOCUMENTATION.md for the full description)
   python3 analysis/claims.py                  # paired comparisons with 95% CIs -> results/claims.txt
   python3 analysis/plots.py                   # figures -> results/figs/
   sudo python3 scripts/rerun_invalid.py --go  # re-run runs whose timestamps were corrupted by a clock jump
