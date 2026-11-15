#!/bin/bash
# Extra evaluation stages (run AFTER final_eval.sh). Fresh seeds 301-305 (never used for design or tuning).
#   sudo bash scripts/extra_eval.sh wlclass   # 3 other background workload classes, with and without a budget (~70 min)
#   sudo bash scripts/extra_eval.sh budlow    # budget at LOW load (~30 min)
#   sudo bash scripts/extra_eval.sh dwell     # dwell-time sensitivity: 0.5 / 1 / 4 s (~10 min)
#   sudo bash scripts/extra_eval.sh subint    # anomalies shorter than the 200 ms control interval (~17 min)
#   sudo bash scripts/extra_eval.sh all
set -e
cd "$(dirname "$0")/.."
S="301 302 303 304 305"
S3="101 102 103"
R="python3 scripts/run_experiments.py ${DRY:+--dry-run}"
BUD="--budget 2.5 --horizon 0.3"
rep() { [ -z "$DRY" ] && python3 analysis/wl_report.py "$@" || true; }

# background workload classes, made only from existing generator options
declare -A CLS
CLS[cpubound]="--workers 4 --sleep-ms 0.1 --work 30000"   # long compute bursts, few switches, high busy fraction
CLS[switchheavy]="--workers 16 --sleep-ms 0.2 --work 300" # many threads, very high background switch rate
CLS[sparse]="--workers 1 --sleep-ms 20"                   # almost idle service

stage_wlclass() {
  for c in cpubound switchheavy sparse; do
    $R --seeds $S --loads high --duration 60 --policies med high adaptive --gen-args "${CLS[$c]}" --out results/wl_$c
    $R --seeds $S --loads high --duration 60 --policies adaptive --gen-args "${CLS[$c]}" --ctl-args "$BUD" --out results/wl_${c}_b2.5
    rep results/wl_$c results/wl_${c}_b2.5
  done
}
stage_budlow() {
  $R --seeds $S --loads low --duration 60 --policies med high adaptive --out results/low_base
  for b in 1.5 2.5; do
    $R --seeds $S --loads low --duration 60 --policies adaptive --ctl-args "--budget $b --horizon 0.3" --out results/low_b$b
  done
  rep results/low_base results/low_b1.5 results/low_b2.5
}
stage_dwell() {
  for d in 500 1000 4000; do
    $R --seeds $S3 --loads high --duration 60 --policies adaptive --ctl-args "--dwell-ms $d" --out results/dwell_$d
  done
  rep results/dwell_500 results/dwell_1000 results/dwell_4000
}
stage_subint() {
  $R --seeds $S --loads high --duration 60 --policies high adaptive --gen-args "--anom 0.1 0.25" --out results/subint
  $R --seeds $S --loads high --duration 60 --policies adaptive --gen-args "--anom 0.1 0.25" --ctl-args "100" --out results/subint_100ms
  rep results/subint results/subint_100ms
}
case "${1:-}" in
  wlclass) stage_wlclass ;; budlow) stage_budlow ;; dwell) stage_dwell ;; subint) stage_subint ;;
  all) stage_wlclass; stage_budlow; stage_dwell; stage_subint ;;
  *) echo "usage: [DRY=1] bash scripts/extra_eval.sh wlclass|budlow|dwell|subint|all"; exit 1 ;;
esac
