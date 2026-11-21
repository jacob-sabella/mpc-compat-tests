/* The fixture plugins MPC really loads: a real VST2 plugin that says it was loaded, and by which process, by appending a line to its
 * log: "main <process> <pid>" when MPC asks it for an instance, "open <process> <pid>" when MPC opens that instance.
 * Built as (build.sh, -D...):
 *   Emu FX (the default): an effect, stereo in, stereo out, passing its input through, for a project's inserts (the MPC Sample tests);
 *     log /tmp/emu-fx.log.
 *   Emu Probe (-DPROBE_SYNTH -DPROBE_NAME='"Emu Probe"' -DPROBE_UID=0x456d5072 -DPROBE_LOG='"/tmp/emu-probe.log"'): an instrument with
 *     no inputs, for a plugin track (MPC OS); silent.
 * The VST2 types are declared here by their published layout (no SDK). */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

struct AEffect;
typedef intptr_t (*host_cb)(struct AEffect *, int32_t, int32_t, intptr_t, void *, float);
typedef intptr_t (*dispatch_fn)(struct AEffect *, int32_t, int32_t, intptr_t, void *, float);
typedef void (*process_fn)(struct AEffect *, float **, float **, int32_t);
typedef void (*setp_fn)(struct AEffect *, int32_t, float);
typedef float (*getp_fn)(struct AEffect *, int32_t);
typedef void (*processd_fn)(struct AEffect *, double **, double **, int32_t);
struct AEffect {
    int32_t magic;
    dispatch_fn dispatcher;
    process_fn process;
    setp_fn setParameter;
    getp_fn getParameter;
    int32_t numPrograms, numParams, numInputs, numOutputs, flags;
    intptr_t resvd1, resvd2;
    int32_t initialDelay, realQualities, offQualities;
    float ioRatio;
    void *object, *user;
    int32_t uniqueID, version;
    process_fn processReplacing;
    processd_fn processDoubleReplacing;
    char future[56];
};
#define MAGIC 0x56737450          /* 'VstP' */
#ifndef PROBE_NAME
#define PROBE_NAME "Emu FX"
#endif
#ifndef PROBE_UID
#define PROBE_UID 0x456d4658      /* 'EmFX', as in the package's plugin-meta.xml */
#endif
#ifndef PROBE_LOG
#define PROBE_LOG "/tmp/emu-fx.log"
#endif
#ifdef PROBE_SYNTH
#define INPUTS 0
#define CATEGORY 2                /* kPlugCategSynth */
#define FLAGS ((1 << 4) | (1 << 8))   /* effFlagsCanReplacing, effFlagsIsSynth */
#else
#define INPUTS 2
#define CATEGORY 1                /* kPlugCategEffect */
#define FLAGS (1 << 4)
#endif

static void note(const char *what)
{
    char comm[64] = "?";
    FILE *f = fopen("/proc/self/comm", "r");
    if (f) { if (fgets(comm, sizeof comm, f)) comm[strcspn(comm, "\n")] = 0; fclose(f); }
    f = fopen(PROBE_LOG, "a");
    if (f) { fprintf(f, "%s %s %d\n", what, comm, (int)getpid()); fclose(f); }
}

static void copy_str(void *dst, const char *s, size_t n)
{
    if (dst) { strncpy((char *)dst, s, n - 1); ((char *)dst)[n - 1] = 0; }
}

static intptr_t dispatch(struct AEffect *e, int32_t op, int32_t idx, intptr_t val, void *ptr, float opt)
{
    (void)e; (void)idx; (void)val; (void)opt;
    switch (op) {
    case 0: note("open"); return 0;                              /* effOpen */
    case 35: return CATEGORY;                                    /* effGetPlugCategory */
    case 45: copy_str(ptr, PROBE_NAME, 32); return 1;            /* effGetEffectName */
    case 47: copy_str(ptr, "acme", 64); return 1;                /* effGetVendorString */
    case 48: copy_str(ptr, PROBE_NAME, 64); return 1;            /* effGetProductString */
#ifdef PROBE_SYNTH
    case 51: return ptr && (!strcmp((char *)ptr, "receiveVstEvents") || !strcmp((char *)ptr, "receiveVstMidiEvent")) ? 1 : 0;   /* effCanDo */
#endif
    case 49: return 1000;                                        /* effGetVendorVersion */
    case 58: return 2400;                                        /* effGetVstVersion */
    default: return 0;
    }
}

static void run(struct AEffect *e, float **in, float **out, int32_t n)
{
    (void)e;
    for (int c = 0; c < 2; c++) {
        if (INPUTS == 0) memset(out[c], 0, (size_t)n * sizeof(float));
        else if (in[c] != out[c]) memcpy(out[c], in[c], (size_t)n * sizeof(float));
    }
}

static void setp(struct AEffect *e, int32_t i, float v) { (void)e; (void)i; (void)v; }
static float getp(struct AEffect *e, int32_t i) { (void)e; (void)i; return 0; }

static struct AEffect fx;

__attribute__((visibility("default"))) struct AEffect *VSTPluginMain(host_cb host)
{
    (void)host;
    note("main");
    memset(&fx, 0, sizeof fx);
    fx.magic = MAGIC; fx.dispatcher = dispatch; fx.process = run; fx.processReplacing = run;
    fx.setParameter = setp; fx.getParameter = getp;
    fx.numInputs = INPUTS; fx.numOutputs = 2; fx.flags = FLAGS;
    fx.uniqueID = PROBE_UID; fx.version = 1000; fx.ioRatio = 1;
    return &fx;
}
