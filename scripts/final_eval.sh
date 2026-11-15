#!/bin/bash
# Final evaluation. Run from the project folder (~/ebpf-adaptive):
#     sudo bash scripts/final_eval.sh <stage> [<stage> ...]      or      sudo bash scripts/final_eval.sh all
#
# stages:  main  h2  ablation  drift  dur  healthy  sens  budget  promote
#
# RULES: final seeds are 101-105 (and 201-203 for the healthy run); the controller was tuned on

set -e

if [ -z "$DRY" ] && [ "$EUID" -ne 0 ]; then
  echo "run with sudo:  sudo bash scripts/final_eval.sh <stage>|all"; exit 1
fi
if [ $# -eq 0 ]; then
  sed -n '2,6p' "$0"; exit 1
fi

S="101 102 103 104 105"     # final seeds
S3="101 102 103"            # sensitivity uses fewer seeds
R="python3 scripts/run_experiments.py ${DRY:+--dry-run}"

# main comparison: none / low / med / high / adaptive, two background loads (~55 min)
stage_main() {
  $R --seeds $S --loads low high --duration 60 --out results/final_main
}

# H2 on a borderline signal: hysteresis / dwell ablations (~21 min)
stage_h2() {
  for cfg in "default:" "nohyst:--hyst 1.0" "nodwell:--dwell-ms 0" "neither:--hyst 1.0 --dwell-ms 0 --recover 1"; do
    name=${cfg%%:*}; args=${cfg#*:}
    $R --seeds $S --loads low --duration 60 --policies adaptive \
       --gen-args "--kinds flicker --anom 3 6" --ctl-args "$args" --out results/final_h2_$name
  done
}

# ablations on the normal anomalies: hysteresis, dwell, controller signal (~27 min)
stage_ablation() {
  for cfg in "nohyst:--hyst 1.0" "nodwell:--dwell-ms 0" "neither:--hyst 1.0 --dwell-ms 0 --recover 1" \
             "sigsw:--signal 1" "sigbusy:--signal 2"; do
    name=${cfg%%:*}; args=${cfg#*:}
    $R --seeds $S --loads high --duration 60 --policies adaptive --ctl-args "$args" --out results/final_abl_$name
  done
}

# workload drift: at half time the service gets much busier (healthy load shifts) (~21 min)
stage_drift() {
  $R --seeds $S --loads high --duration 60 --policies none low high adaptive \
     --gen-args "--drift-factor 0.05" --out results/final_drift
}

# anomaly duration sensitivity: brief and long anomalies (the 1-3 s default is in "main") (~21 min)
stage_dur() {
  $R --seeds $S --loads low --duration 60 --policies high adaptive \
     --gen-args "--anom 0.3 0.6 --gap 3 6" --out results/final_dur_short
  $R --seeds $S --loads low --duration 60 --policies high adaptive \
     --gen-args "--anom 4 8" --out results/final_dur_long
}

# healthy-only runs (no anomalies at all) to estimate false alarms per hour (~30 min)
stage_healthy() {
  $R --seeds 201 202 203 --loads low high --duration 300 --policies adaptive \
     --gen-args "--gap 100000 100000" --out results/final_healthy
}

# sensitivity: thresholds, control interval, medium sampling rate (~26 min)
stage_sens() {
  for cfg in "busy03:--busy-med 0.3" "busy07:--busy-med 0.7" "sw5k:--sw-med 5000" "sw50k:--sw-med 50000" \
             "poll100:100" "poll500:500"; do
    name=${cfg%%:*}; args=${cfg#*:}
    $R --seeds $S3 --loads high --duration 60 --policies adaptive --ctl-args "$args" --out results/final_sens_$name
  done
  for n in 10 1000; do
    $R --seeds $S3 --loads high --duration 60 --policies med --sample-n $n --out results/final_sens_n$n
  done
}

# NEW controller features (opt-in; defaults of the original controller are unchanged).
# budget: closed-loop overhead budget, % of one core. 1.0 is near the cost floor (expected to be
# infeasible/costly in coverage -> honest negative result), 1.5 and 2.5 are realistic. (~20 min)
stage_budget() {
  for b in 1.0 1.5 2.5 4.0; do
    $R --seeds $S --loads high --duration 60 --policies adaptive \
       --ctl-args "--budget $b --horizon 0.3" --out results/final_budget_$b
  done
  # ablation: the default 1 s horizon (tuned choice is 0.3 s)
  $R --seeds $S --loads high --duration 60 --policies adaptive \
     --ctl-args "--budget 2.5 --horizon 1.0" --out results/final_budget_2.5_h1.0
}

# promotion rule on borderline (flicker) anomalies: default vs --promote-s 2 (~11 min)
stage_promote() {
  $R --seeds $S --loads low --duration 60 --policies adaptive \
     --gen-args "--kinds flicker --anom 3 6" --ctl-args "--promote-s 2" --out results/final_promote
}

for st in "$@"; do
  if [ "$st" = "all" ]; then
    for x in main h2 ablation drift dur healthy sens budget promote; do echo "=== stage $x ==="; stage_$x; done
  else
    echo "=== stage $st ==="; stage_$st
  fi
done
echo "all requested stages finished"
