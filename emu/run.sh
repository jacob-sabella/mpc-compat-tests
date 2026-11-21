#!/usr/bin/env bash
# The emulator tests, from one command: the Plugin Manager inside real MPC firmware (QEngine).
#   emu/run.sh [--tier quick|changed|full] [--since REF] [--cap N] [--jobs N] [-i INSTANCE]... [--label BUILD] [-k NAME]... [--prepare] [--list] [--plan]
# --tier quick    a few scenarios on one Gen1 and one Gen2       --tier full     every scenario on all six machines
# --tier changed  what the files changed since --since (default origin/main) need; the full matrix when that cannot be told
# No tier: every scenario on mpc-g1-391 and mpc-g2-3100. -i and -k override a tier's machines and scenarios.
# --cap N: emulators at once across all machines (jobs.py; default from the cores and memory, about 10 on 32 cores; or PM_EMU_CAP).
# --jobs N: at most N of them on one machine. --plan: the order and the estimated time, booting nothing. CI has shards: one at a time.
# See emu/README.md for the setup.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
ALL=(mpc-g2-3101 mpc-g2-3100 mpc-g1-3101 mpc-x-391 mpc-g1-391 mpc-sample-130)
QUICK=(fresh_install plugin_install_update_remove self_update catalog_retry)
inst=(); rest=(); tier=; since=origin/main; jobs="${PM_EMU_JOBS:-}"; cap=; keys=0; dry=0; plan=0
while [ $# -gt 0 ]; do case "$1" in
    -i) inst+=("$2"); shift 2 ;;
    --tier) tier=$2; shift 2 ;;
    --since) since=$2; shift 2 ;;
    --jobs) jobs=$2; shift 2 ;;
    --cap) cap=$2; shift 2 ;;
    --plan) plan=1; shift ;;
    -k) keys=1; rest+=("$1" "$2"); shift 2 ;;
    --list|--machine) dry=1; rest+=("$1"); shift ;;
    *) rest+=("$1"); shift ;;
esac; done
case "$jobs" in ''|[1-9]|[1-9][0-9]) ;; *) echo "--jobs: a number" >&2; exit 2 ;; esac
case "$cap" in ''|[1-9]|[1-9][0-9]) ;; *) echo "--cap: a number" >&2; exit 2 ;; esac
case "$tier" in
    '') ;;
    quick) [ ${#inst[@]} -gt 0 ] || inst=(mpc-g1-3101 mpc-g2-3101)
           if [ $keys = 0 ]; then for k in "${QUICK[@]}"; do rest+=(-k "$k"); done; fi ;;
    full)  [ ${#inst[@]} -gt 0 ] || inst=("${ALL[@]}") ;;
    changed)
        plan=$(python3 "$here/tiers.py" --since "$since") || exit 2
        if [ "$plan" = nothing ]; then echo "nothing to run for the changes since $since"; exit 0; fi
        read -r -a got <<< "$(sed -n 's/^instances //p' <<< "$plan")"
        [ ${#inst[@]} -gt 0 ] || inst=("${got[@]}")
        if [ $keys = 0 ]; then for k in $(sed -n 's/^scenarios *//p' <<< "$plan"); do rest+=(-k "$k"); done; fi ;;
    *) echo "--tier: quick, changed or full" >&2; exit 2 ;;
esac
[ ${#inst[@]} -gt 0 ] || inst=(mpc-g1-391 mpc-g2-3100)
[ -z "$tier" ] || echo "tier $tier: ${inst[*]}"
[ -n "$jobs" ] || [ -z "${GITHUB_ACTIONS:-}" ] || jobs=1
# PM_EMU_TIMESCALE: how much slower than a desktop the guest runs here; every timeout is multiplied by it. A GitHub-hosted runner
# is about 3.3 times slower (Gen1 slower again), so 4 there unless given.
[ -z "${GITHUB_ACTIONS:-}" ] || export PM_EMU_TIMESCALE="${PM_EMU_TIMESCALE:-4}"
label=work; for ((k = 0; k < ${#rest[@]}; k++)); do [ "${rest[k]}" = --label ] && label="${rest[k+1]}"; done
pool=(); [ -z "$cap" ] || pool+=(--cap "$cap"); [ -z "$jobs" ] || pool+=(--per-machine "$jobs")
if [ "$plan" = 1 ]; then exec python3 "$here/jobs.py" --plan ${pool[@]+"${pool[@]}"} ${rest[@]+"${rest[@]}"} "${inst[@]}"; fi
if [ "$dry" = 0 ] && { [ -z "${GITHUB_ACTIONS:-}" ] || [ "${jobs:-1}" -gt 1 ]; }; then
    exec python3 "$here/jobs.py" ${pool[@]+"${pool[@]}"} ${rest[@]+"${rest[@]}"} "${inst[@]}"
fi
mkdir -p "$here/results"
pids=()
for i in "${inst[@]}"; do
    # one run per instance at a time (QEngine keeps one running copy of an instance): a second build's run waits for the first
    ( mkdir -p "$here/state"; exec 9> "$here/state/$i.lock"; flock 9
      cmd=("$here/in-container" "pm-emu-$i-$label" python3 run.py "$i")
      "${cmd[@]}" ${rest[@]+"${rest[@]}"} 2>&1 | tee "$here/results/$i-$label.log" | grep --line-buffered -E "^[0-9:]+ $i |^      [-*] |Traceback" ; exit "${PIPESTATUS[0]}" ) &
    pids+=($!)
done
rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
exit $rc
