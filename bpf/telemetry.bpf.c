#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include "telemetry.h"

struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, 1);
         __type(key, __u32); __type(value, struct cfg); } cfg_map SEC(".maps");

struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 1);
         __type(key, __u32); __type(value, struct stats); } stats_map SEC(".maps");

struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 1);
         __type(key, __u32); __type(value, struct busy); } busy_map SEC(".maps");

/* size is overridden from user space before load */
struct { __uint(type, BPF_MAP_TYPE_RINGBUF); __uint(max_entries, 1 << 22); } rb SEC(".maps");


static __always_inline int is_self(const char *comm)
{
    char c[16];
    if (bpf_probe_read_kernel(c, sizeof(c), comm))
        return 0;
    return c[0]=='t' && c[1]=='e' && c[2]=='l' && c[3]=='e' && c[4]=='m' &&
           c[5]=='e' && c[6]=='t' && c[7]=='r' && c[8]=='y' && c[9]==0;
}

SEC("tracepoint/sched/sched_switch")
int handle_switch(struct trace_event_raw_sched_switch *ctx)
{
    __u32 zero = 0;
    struct cfg *c = bpf_map_lookup_elem(&cfg_map, &zero);
    struct stats *s = bpf_map_lookup_elem(&stats_map, &zero);
    if (!c || !s)
        return 0;

    s->switches++;                       
    if (c->mode == MODE_LOW)
        return 0;


    if (is_self(ctx->prev_comm) || is_self(ctx->next_comm)) {
        s->self_skipped++;
        return 0;
    }
    if (c->mode == MODE_MED) {
        __u32 n = c->sample_n ? c->sample_n : 1;
        if ((s->switches % n) != 0)
            return 0;
    }

    struct event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) {
        s->dropped++;
        return 0;
    }
    e->ts = bpf_ktime_get_ns();
    e->cpu = bpf_get_smp_processor_id();
    e->prev_pid = ctx->prev_pid;
    e->next_pid = ctx->next_pid;
    bpf_ringbuf_submit(e, c->no_wakeup ? BPF_RB_NO_WAKEUP : 0);
    s->emitted++;
    return 0;
}

/* Fires at a fixed rate per CPU (software cpu-clock perf event).
 * Unbiased busy estimate: the idle task has tid 0. */
SEC("perf_event")
int on_tick(void *ctx)
{
    __u32 zero = 0;
    struct busy *b = bpf_map_lookup_elem(&busy_map, &zero);
    if (!b)
        return 0;
    b->total++;
    if ((__u32)bpf_get_current_pid_tgid() != 0)
        b->busy++;
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
