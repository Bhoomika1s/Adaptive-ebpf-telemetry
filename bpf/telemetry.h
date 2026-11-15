#ifndef TELEMETRY_H
#define TELEMETRY_H

enum { MODE_LOW = 0, MODE_MED = 1, MODE_HIGH = 2 };
struct cfg   { __u32 mode; __u32 sample_n; __u32 pad; __u32 no_wakeup; };
struct stats { __u64 switches, emitted, dropped, self_skipped; };
struct busy  { __u64 total, busy; };
struct event { __u64 ts; __u32 cpu, prev_pid, next_pid; };
#endif
