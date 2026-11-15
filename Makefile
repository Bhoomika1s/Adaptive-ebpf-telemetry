.RECIPEPREFIX = >
.SECONDARY:
CLANG   = clang
BPFTOOL = /usr/local/sbin/bpftool
INC     = -I bpf -I/usr/local/include
LIBS    = -L/usr/local/lib64 -lbpf -lelf -lz

all: count_switch telemetry

bpf/%.bpf.o: bpf/%.bpf.c bpf/vmlinux.h bpf/telemetry.h
> $(CLANG) -g -O2 -target bpf -D__TARGET_ARCH_x86 $(INC) -c $< -o $@

bpf/%.skel.h: bpf/%.bpf.o
> $(BPFTOOL) gen skeleton $< > $@

count_switch: controller/count_switch.c bpf/count_switch.skel.h
> gcc -O2 $(INC) $< -o $@ $(LIBS)

telemetry: controller/telemetry.c bpf/telemetry.skel.h bpf/telemetry.h
> gcc -O2 $(INC) $< -o $@ $(LIBS)

clean:
> rm -f bpf/*.o bpf/*.skel.h count_switch telemetry
