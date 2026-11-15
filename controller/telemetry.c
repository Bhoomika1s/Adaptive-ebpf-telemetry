/* Telemetry collector + adaptive controller.
 *
 * usage: sudo ./telemetry <mode> <sample_n> <seconds> [ring_kb=4096] [nowake=0] [interval_ms=200] [hz=200]
 *                         [--sw-med N] [--sw-high N] [--busy-med F] [--busy-high F]
 *                         [--hyst F] [--dwell-ms N] [--recover N]
 *   mode 0 = fixed low, 1 = fixed medium, 2 = fixed high, 3 = ADAPTIVE (starts low)
 *
 * Adaptive policy (checked every interval_ms):
 *   - Escalate immediately to the level whose ENTER thresholds are exceeded
 *       level 1 if switch_rate > sw-med  or busy > busy-med
 *       level 2 if switch_rate > sw-high or busy > busy-high
 *   - De-escalate one level only when the signals are below the EXIT thresholds
 *     (enter thresholds * hyst) for `recover` consecutive intervals AND the
 *     current level has been held for at least dwell-ms.
 *   Ablations: --hyst 1.0 (no hysteresis), --dwell-ms 0, --recover 1,
 *              --signal 1 (switch rate only) or --signal 2 (busy fraction only).
 * CSV on stdout, SUMMARY on stderr. ring_kb must be a power of two. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <sys/resource.h>
#include <linux/types.h>
#include <linux/perf_event.h>
#include <bpf/libbpf.h>
#include <bpf/bpf.h>
#include "telemetry.h"
#include "telemetry.skel.h"

struct ctl {
    double sw_med, sw_high, busy_med, busy_high, hyst, dwell_s;
    int recover;
    int signal;   /* 0 = both signals, 1 = switch rate only, 2 = busy fraction only */
    double budget;     /* 0 = off. Otherwise max telemetry cost, % of ONE core (closed loop) */
    double promote_s;  /* 0 = off. Promote medium->high after this long of sustained anomaly */
    double budget_win; /* seconds of budget the credit bucket can hold (averaging window) */
    double horizon;    /* seconds of a level's projected cost the credit must be able to pay */
};

static unsigned long long consumed;
static int on_event(void *ctx, void *data, size_t sz) { consumed++; return 0; }

static double now_s(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static double epoch_s(void)
{
    struct timespec t; clock_gettime(CLOCK_REALTIME, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static int open_cpu_clock(int cpu, int hz)
{
    struct perf_event_attr a;
    memset(&a, 0, sizeof(a));
    a.type = PERF_TYPE_SOFTWARE;
    a.size = sizeof(a);
    a.config = PERF_COUNT_SW_CPU_CLOCK;
    a.freq = 1;
    a.sample_freq = hz;
    return syscall(SYS_perf_event_open, &a, -1, cpu, -1, PERF_FLAG_FD_CLOEXEC);
}

static void read_stats(int fd, int ncpu, struct stats *vals, struct stats *tot)
{
    __u32 k = 0;
    memset(tot, 0, sizeof(*tot));
    if (bpf_map_lookup_elem(fd, &k, vals)) return;
    for (int c = 0; c < ncpu; c++) {
        tot->switches     += vals[c].switches;
        tot->emitted      += vals[c].emitted;
        tot->dropped      += vals[c].dropped;
        tot->self_skipped += vals[c].self_skipped;
    }
}

static void read_busy(int fd, int ncpu, struct busy *vals, struct busy *tot)
{
    __u32 k = 0;
    memset(tot, 0, sizeof(*tot));
    if (bpf_map_lookup_elem(fd, &k, vals)) return;
    for (int c = 0; c < ncpu; c++) {
        tot->total += vals[c].total;
        tot->busy  += vals[c].busy;
    }
}

/* level justified by the signals; scale 1.0 = enter thresholds, hyst = exit thresholds */
static int level_for(double sw, double busy, const struct ctl *k, double scale)
{
    if (k->signal == 1) busy = 0.0;
    if (k->signal == 2) sw = 0.0;
    if (sw > k->sw_high * scale || busy > k->busy_high * scale) return 2;
    if (sw > k->sw_med  * scale || busy > k->busy_med  * scale) return 1;
    return 0;
}

/* in-kernel run time and run count of one eBPF program (needs bpf_enable_stats) */
static void prog_cost(int fd, __u64 *ns, __u64 *cnt)
{
    struct bpf_prog_info info;
    __u32 len = sizeof(info);
    memset(&info, 0, sizeof(info));
    if (bpf_prog_get_info_by_fd(fd, &info, &len) == 0) {
        *ns = info.run_time_ns;
        *cnt = info.run_cnt;
    } else {
        *ns = *cnt = 0;
    }
}

int main(int argc, char **argv)
{
    struct ctl k = { .sw_med = 20000, .sw_high = 200000, .busy_med = 0.5,
                     .busy_high = 0.9, .hyst = 0.7, .dwell_s = 2.0, .recover = 3,
                     .budget_win = 10.0, .horizon = 1.0 };
    char *pos[16]; int npos = 0;
    for (int i = 1; i < argc; i++) {
        if (!strncmp(argv[i], "--", 2)) {
            if (i + 1 >= argc) { fprintf(stderr, "missing value for %s\n", argv[i]); return 1; }
            const char *key = argv[i] + 2; double v = atof(argv[i + 1]);
            if      (!strcmp(key, "sw-med"))    k.sw_med = v;
            else if (!strcmp(key, "sw-high"))   k.sw_high = v;
            else if (!strcmp(key, "busy-med"))  k.busy_med = v;
            else if (!strcmp(key, "busy-high")) k.busy_high = v;
            else if (!strcmp(key, "hyst"))      k.hyst = v;
            else if (!strcmp(key, "dwell-ms"))  k.dwell_s = v / 1000.0;
            else if (!strcmp(key, "recover"))   k.recover = (int)v;
            else if (!strcmp(key, "signal"))    k.signal = (int)v;
            else if (!strcmp(key, "budget"))    k.budget = v;
            else if (!strcmp(key, "promote-s")) k.promote_s = v;
            else if (!strcmp(key, "budget-win")) k.budget_win = v;
            else if (!strcmp(key, "horizon"))    k.horizon = v;
            else { fprintf(stderr, "unknown option %s\n", argv[i]); return 1; }
            i++;
        } else if (npos < 16) {
            pos[npos++] = argv[i];
        }
    }
    if (npos < 3) {
        fprintf(stderr, "usage: %s mode sample_n seconds [ring_kb] [nowake] [interval_ms] [hz] [--options]\n", argv[0]);
        return 1;
    }
    int user_mode = atoi(pos[0]);
    int adaptive = (user_mode == 3);
    struct cfg cfg = { .mode = adaptive ? 0 : user_mode, .sample_n = atoi(pos[1]) };
    double seconds = atof(pos[2]);
    unsigned ring_kb = npos > 3 ? atoi(pos[3]) : 4096;
    cfg.no_wakeup = npos > 4 ? atoi(pos[4]) : 0;
    double interval = (npos > 5 ? atoi(pos[5]) : 200) / 1000.0;
    int hz = npos > 6 ? atoi(pos[6]) : 200;

    struct telemetry_bpf *skel = telemetry_bpf__open();
    if (!skel) { fprintf(stderr, "open failed\n"); return 1; }
    bpf_map__set_max_entries(skel->maps.rb, ring_kb * 1024);
    if (telemetry_bpf__load(skel)) { fprintf(stderr, "load failed\n"); return 1; }
    int stats_fd = bpf_enable_stats(BPF_STATS_RUN_TIME);   /* in-kernel run-time accounting */
    if (stats_fd < 0)
        fprintf(stderr, "warning: bpf_enable_stats failed; eBPF cost will read 0\n");

    __u32 zero = 0;
    int cfd = bpf_map__fd(skel->maps.cfg_map);
    bpf_map_update_elem(cfd, &zero, &cfg, BPF_ANY);
    if (telemetry_bpf__attach(skel)) { fprintf(stderr, "attach failed\n"); return 1; }

    /* start the fixed-rate busy sampler on every online CPU */
    int ncpu_on = sysconf(_SC_NPROCESSORS_ONLN);
    for (int c = 0; c < ncpu_on; c++) {
        int pfd = open_cpu_clock(c, hz);
        if (pfd < 0) { perror("perf_event_open"); return 1; }
        if (!bpf_program__attach_perf_event(skel->progs.on_tick, pfd)) {
            fprintf(stderr, "attach perf_event failed on cpu %d\n", c); return 1;
        }
    }

    struct ring_buffer *rb = ring_buffer__new(bpf_map__fd(skel->maps.rb), on_event, NULL, NULL);
    if (!rb) { fprintf(stderr, "ringbuf failed\n"); return 1; }

    int ncpu = libbpf_num_possible_cpus();
    struct stats *svals = calloc(ncpu, sizeof(*svals));
    struct busy  *bvals = calloc(ncpu, sizeof(*bvals));
    int sfd = bpf_map__fd(skel->maps.stats_map);
    int bfd = bpf_map__fd(skel->maps.busy_map);

    printf("t,epoch,mode,switch_rate,busy_frac,emitted,dropped,consumed,self_skipped\n");
    struct stats sp, sc;
    struct busy bp, bc;
    unsigned long long prev_cons = 0;
    read_stats(sfd, ncpu, svals, &sp);
    read_busy(bfd, ncpu, bvals, &bp);
    double t0 = now_s(), last = t0, last_change = t0;
    int level = cfg.mode, below = 0, transitions = 0;

    /* state of the optional budget controller / promotion rule */
    int sw_fd = bpf_program__fd(skel->progs.handle_switch);
    int tk_fd = bpf_program__fd(skel->progs.on_tick);
    __u32 base_n = cfg.sample_n ? cfg.sample_n : 1;
    double ns_ps[3] = { 120.0, 300.0, 600.0 };   /* priors: BPF ns per switch at each level */
    double ban_until[3] = { 0, 0, 0 };
    double cost_ew = -1.0, fixed_ew = 0.5, cost_sum = 0.0;
    double credit_max = k.budget * k.budget_win;      /* bucket size, % of a core * s */
    double credit = 0.5 * credit_max;                 /* starts half full */
    double n_changed = -10.0;                         /* time of the last sample_n change */
    int cost_n = 0, forced_down = 0, capped_iv = 0, promotions = 0, promoted = 0;
    double sustained = -1.0;
    __u64 psn = 0, psc = 0, ptn = 0, ptc = 0;
    double pcpu = 0.0;
    if (k.budget > 0) {
        struct rusage r0; getrusage(RUSAGE_SELF, &r0);
        pcpu = r0.ru_utime.tv_sec + r0.ru_utime.tv_usec / 1e6 + r0.ru_stime.tv_sec + r0.ru_stime.tv_usec / 1e6;
        prog_cost(sw_fd, &psn, &psc); prog_cost(tk_fd, &ptn, &ptc);
    }

    while (now_s() - t0 < seconds) {
        ring_buffer__poll(rb, cfg.no_wakeup ? 10 : 50);
        if (cfg.no_wakeup) ring_buffer__consume(rb);   /* no wakeups: drain by timer */
        double now = now_s();
        if (now - last < interval) continue;

        double dt = now - last;
        read_stats(sfd, ncpu, svals, &sc);
        read_busy(bfd, ncpu, bvals, &bc);
        double dtot = (double)(bc.total - bp.total);
        double sw = (sc.switches - sp.switches) / dt;
        double busy = dtot > 0 ? (bc.busy - bp.busy) / dtot : 0.0;

        printf("%.2f,%.3f,%d,%.0f,%.3f,%llu,%llu,%llu,%llu\n", now - t0, epoch_s(), level, sw, busy,
               (unsigned long long)(sc.emitted - sp.emitted),
               (unsigned long long)(sc.dropped - sp.dropped),
               consumed - prev_cons,
               (unsigned long long)(sc.self_skipped - sp.self_skipped));

        if (adaptive) {
            int up = level_for(sw, busy, &k, 1.0);
            int hold = level_for(sw, busy, &k, k.hyst);
            int newlevel = level;
            int allowed = 2, forced = 0;

            if (k.budget > 0) {
                /* measure the cost of the interval that just ended (level in effect = `level`) */
                __u64 sn, sc2, tn, tc;
                struct rusage r; getrusage(RUSAGE_SELF, &r);
                double cpu = r.ru_utime.tv_sec + r.ru_utime.tv_usec / 1e6 + r.ru_stime.tv_sec + r.ru_stime.tv_usec / 1e6;
                prog_cost(sw_fd, &sn, &sc2); prog_cost(tk_fd, &tn, &tc);
                double d_sw_ns = (double)(sn - psn), d_sw_cnt = (double)(sc2 - psc);
                double fixed = ((double)(tn - ptn) + (cpu - pcpu) * 1e9) / (dt * 1e9) * 100.0;
                double cost = d_sw_ns / (dt * 1e9) * 100.0 + fixed;
                cost_ew = cost_ew < 0 ? cost : 0.5 * cost_ew + 0.5 * cost;
                fixed_ew = 0.7 * fixed_ew + 0.3 * fixed;
                if (d_sw_cnt > 1000) ns_ps[level] = 0.7 * ns_ps[level] + 0.3 * (d_sw_ns / d_sw_cnt);
                cost_sum += cost; cost_n++;
                psn = sn; psc = sc2; ptn = tn; ptc = tc; pcpu = cpu;

                /* CREDIT BUCKET: the budget limits the AVERAGE cost over ~budget-win seconds, so short
                 * bursts are allowed as long as unspent budget has accumulated. */
                credit += (k.budget - cost) * dt;                  /* units: % of a core * seconds */
                if (credit > credit_max) credit = credit_max;
                if (credit < -credit_max) credit = -credit_max;

                /* highest level whose projected cost the credit can pay for during the next
                 * `horizon` seconds (shorter horizon to stay where we are = a little hysteresis) */
                allowed = 0;
                for (int L = 1; L <= 2; L++) {
                    double proj = sw * ns_ps[L] / 1e9 * 100.0 + fixed_ew;
                    double h = (L <= level) ? 0.5 * k.horizon : k.horizon;
                    if (credit + (k.budget - proj) * h >= 0 && now >= ban_until[L]) allowed = L;
                }
                /* credit exhausted: coarsen medium sampling first, then drop a level */
                if (level > 0 && credit < 0) {
                    if (level == 1 && cfg.sample_n < 100 * base_n) {
                        if (now - n_changed >= 1.0) {          /* at most one doubling per second */
                            cfg.sample_n *= 2; n_changed = now;
                            bpf_map_update_elem(cfd, &zero, &cfg, BPF_ANY);
                        }
                    } else {
                        newlevel = level - 1; forced = 1; forced_down++;
                        ban_until[level] = now + 2.0;
                    }
                } else if (cfg.sample_n > base_n && credit > 0.25 * credit_max && now - n_changed >= 1.0) {
                    cfg.sample_n = cfg.sample_n / 2 < base_n ? base_n : cfg.sample_n / 2;   /* relax */
                    n_changed = now;
                    bpf_map_update_elem(cfd, &zero, &cfg, BPF_ANY);
                }
            }

            int want = up;
            if (k.budget > 0 && want > allowed) { want = allowed; capped_iv++; }
            if (k.promote_s > 0) {                    /* sustained anomaly at medium -> high */
                if (hold >= 1) { if (sustained < 0) sustained = now; } else sustained = -1.0;
                if (level == 1 && sustained >= 0 && now - sustained >= k.promote_s &&
                    (k.budget <= 0 || allowed >= 2) && want < 2) {
                    want = 2; promotions++; promoted = 1;
                }
            }
            int hold_eff = (promoted && hold >= 1) ? 2 : hold;   /* promoted level lasts as long as the anomaly */

            if (forced) {                             /* budget forced a de-escalation */
                below = 0;
            } else if (want > level) {                /* escalate immediately */
                newlevel = want; below = 0;
            } else if (hold_eff < level) {            /* signals below exit thresholds */
                below++;
                if (below >= k.recover && (now - last_change) >= k.dwell_s) {
                    newlevel = level - 1; below = 0;
                }
            } else {
                below = 0;
            }
            if (newlevel != level) {
                level = newlevel; last_change = now; transitions++;
                if (level < 2) promoted = 0;
                if (k.budget > 0) cost_ew = -1.0;     /* old level's cost no longer applies */
                cfg.mode = level;
                bpf_map_update_elem(cfd, &zero, &cfg, BPF_ANY);
            }
        }
        sp = sc; bp = bc; prev_cons = consumed; last = now;
    }
    read_stats(sfd, ncpu, svals, &sc);
    struct rusage ru; getrusage(RUSAGE_SELF, &ru);
    fprintf(stderr, "SUMMARY mode=%d ring_kb=%u nowake=%u switches=%llu emitted=%llu dropped=%llu consumed=%llu self_skipped=%llu transitions=%d collector_cpu_s=%.3f\n",
            user_mode, ring_kb, cfg.no_wakeup,
            (unsigned long long)sc.switches, (unsigned long long)sc.emitted,
            (unsigned long long)sc.dropped, consumed, (unsigned long long)sc.self_skipped,
            transitions,
            ru.ru_utime.tv_sec + ru.ru_utime.tv_usec / 1e6 + ru.ru_stime.tv_sec + ru.ru_stime.tv_usec / 1e6);
    __u64 sw_ns, sw_cnt, tk_ns, tk_cnt;
    prog_cost(bpf_program__fd(skel->progs.handle_switch), &sw_ns, &sw_cnt);
    prog_cost(bpf_program__fd(skel->progs.on_tick), &tk_ns, &tk_cnt);
    fprintf(stderr, "COST ebpf_switch_ns=%llu ebpf_switch_cnt=%llu ebpf_tick_ns=%llu ebpf_tick_cnt=%llu ebpf_cpu_s=%.4f maxrss_kb=%ld\n",
            (unsigned long long)sw_ns, (unsigned long long)sw_cnt,
            (unsigned long long)tk_ns, (unsigned long long)tk_cnt,
            (sw_ns + tk_ns) / 1e9, ru.ru_maxrss);
    if (adaptive && (k.budget > 0 || k.promote_s > 0))
        fprintf(stderr, "BUDGET budget_pct=%.2f forced_down=%d capped_intervals=%d promotions=%d final_sample_n=%u mean_cost_pct=%.3f\n",
                k.budget, forced_down, capped_iv, promotions, cfg.sample_n, cost_n ? cost_sum / cost_n : 0.0);
    if (stats_fd >= 0) close(stats_fd);
    ring_buffer__free(rb);
    telemetry_bpf__destroy(skel);
    return 0;
}
