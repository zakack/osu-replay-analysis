#!/bin/bash
# Four lazer sessions back to back, half limited to four cores and half not, to settle
# whether kosu's core limit is what moved hit timing by ~4 ms (INTERESTING.md, 2026-10-07).
#
# A is `taskset -c 0-3`, which is exactly what Lutris prepends for kosu's limit_cpu_count
# (lutris/runner_interpreter.py). B is the desktop entry's launch, all 24 cores. Everything
# else is held equal: both go through game-performance, and the PipeWire clock is set once
# at the start the way the wrapper sets it.
#
# The order is drawn at random between ABBA and BAAB, which both cancel a linear warm-up or
# fatigue drift, and is never printed: the player asked not to know. It is written to
# build/experiments/, and lazer's own runtime log records the core count of every session
# anyway, so the analysis does not depend on that file.
#
# Lazer is launched directly, not through tools/corpus/osu-lazer, so the corpus sync does
# not run between sessions and compete for the CPU being measured. It runs once at the end.
set -u
cd "$(dirname "$0")/../.."

lazer=/usr/bin/osu-lazer
running() { pgrep -f 'osu\.AppImage|/\.mount_osu' >/dev/null; }

if running; then
    echo "lazer is already running; close it first."
    exit 1
fi

[ -x ~/.local/bin/pw-force ] && RATE=48000 QUANTUM=0 ~/.local/bin/pw-force >/dev/null

if (( RANDOM % 2 )); then order=(A B B A); else order=(B A A B); fi

mkdir -p build/experiments
record="build/experiments/cores-ab-$(date +%Y%m%dT%H%M%S).log"
echo "order ${order[*]}" > "$record"

finish() {
    echo "finished $(date -Iseconds)" >> "$record"
    systemctl --user start --no-block corpus-sync.service
    echo "Done. The corpus sync is running; tell Claude the test is finished."
}
trap 'echo; echo "Stopped early."; finish; exit 130' INT

for i in 1 2 3 4; do
    condition=${order[i-1]}
    echo "Session $i of 4. Play the test maps, then quit lazer."
    started=$(date +%s)
    echo "session $i start $(date -Iseconds)" >> "$record"

    if [ "$condition" = A ]; then
        game-performance taskset -c 0-3 "$lazer" >/dev/null 2>&1
    else
        game-performance "$lazer" >/dev/null 2>&1
    fi

    # A launch that returns at once handed off to a lazer that was already open, or
    # crashed; either way that session did not happen, so wait it out rather than skip.
    while running; do sleep 2; done
    echo "session $i end $(date -Iseconds) ($(( $(date +%s) - started ))s)" >> "$record"

    if (( $(date +%s) - started < 30 )); then
        echo "That session lasted under 30 seconds; it will not count. Re-run the script to start over."
        finish
        exit 1
    fi

    if (( i < 4 )); then
        echo "Next session starts in 5 seconds (Ctrl-C to stop)."
        sleep 5
    fi
done

finish
