#!/bin/bash
# Four lazer sessions back to back, ordered ABBA or BAAB, to ask which clock each hand keeps:
# the music, or the objects on screen. Two modes, two different questions.
#
# offset  A is the player's global audio offset + DELTA, B is offset - DELTA. An offset moves
#         the music against everything drawn and judged, so the two clocks come apart by a
#         known amount at a known time. The button hand is the check that the manipulation
#         took: if it presses to the music, its hit error moves by the whole separation. The
#         cursor is the question: one timed by the approach circles stays put, one timed by
#         the music (or by the button hand) moves with the presses. Asks which clock is *in
#         use* when both are available.
#
# muted   N is normal play, M is the Muted mod with the metronome and inverse muting off, so
#         no beat is left. There is no reference left to shift against, so this reads
#         consistency instead: which hand's spread grows when the music goes. Asks how much
#         each hand *depends* on the music, which is weaker evidence about normal play, since
#         a player without music leans on the approach circles harder by choice. Lazer's
#         Muted turns the metronome on by default; the script cannot toggle mods or their
#         settings, so the player does, and the analysis checks MU against each score.
#
# Historical steps could not settle either because both hands' timing drifts together from
# play to play (INTERESTING.md, 2026-10-18), which ABBA cancels when it is linear and a
# before/after split does not.
#
# The offset is written to game.ini while lazer is closed (it reads the file at start and
# writes it back on exit) and the original value is put back at the end, on Ctrl-C too. In
# offset mode the order is not printed; a 30 ms separation is likely to be felt anyway, so
# this is not a blind test, only an unannounced one. In muted mode the player has to know.
#
# Every session launches lazer the same way and directly, not through tools/corpus/osu-lazer,
# so no corpus sync runs between sessions. Analyse with tools/analysis/clockab.py once the
# sync at the end has finished.
#
#   tools/experiments/clock-ab.sh offset [DELTA_MS]      (default 15)
#   tools/experiments/clock-ab.sh muted
set -u
cd "$(dirname "$0")/../.."

lazer=/usr/bin/osu-lazer
ini=~/.config/osu-lazer/game.ini
mode=${1:-}
delta=${2:-15}
running() { pgrep -f 'osu\.AppImage|/\.mount_osu' >/dev/null; }

case "$mode" in
    offset) first=A second=B ;;
    muted) first=N second=M ;;
    *) echo "usage: $0 offset [DELTA_MS] | muted"; exit 1 ;;
esac

if running; then
    echo "lazer is already running; close it first."
    exit 1
fi

base=$(sed -nE 's/^AudioOffset = (-?[0-9.]+)$/\1/p' "$ini")
if [ -z "$base" ]; then
    echo "no AudioOffset line in $ini; set the offset in lazer once so the line exists."
    exit 1
fi

set_offset() { sed -i -E "s/^AudioOffset = .*/AudioOffset = $1/" "$ini"; }
offset_for() {
    [ "$mode" = muted ] && { echo "$base"; return; }
    awk -v b="$base" -v d="$delta" -v c="$1" 'BEGIN { printf "%.1f", c == "A" ? b + d : b - d }'
}

[ -x ~/.local/bin/pw-force ] && RATE=48000 QUANTUM=0 ~/.local/bin/pw-force >/dev/null

if (( RANDOM % 2 )); then order=($first $second $second $first); else order=($second $first $first $second); fi

mkdir -p build/experiments
stamp=$(date +%Y%m%dT%H%M%S)
record="build/experiments/clock-ab-$mode-$stamp.log"
{
    echo "mode $mode"
    echo "order ${order[*]}"
    echo "base $base"
    [ "$mode" = offset ] && { echo "A $(offset_for A)"; echo "B $(offset_for B)"; }
} > "$record"

finish() {
    set_offset "$base"
    echo "finished $(date -Iseconds)" >> "$record"
    # A setup change like any other: plays in these sessions are not comparable to plays
    # around them without knowing which condition they ran under.
    if [ "$mode" = offset ]; then
        change="global AudioOffset alternated $base +/- $delta ms over four sessions, restored after"
    else
        change="Muted (no metronome) in two of four sessions"
    fi
    printf '%s,%s,audio,"clock-ab experiment: %s; per-session condition in %s",tools/experiments/clock-ab.sh,measured\n' \
        "$(date +%F)" "$(date +%F)" "$change" "$record" >> setup-timeline.csv
    systemctl --user start --no-block corpus-sync.service
    echo "Done. AudioOffset is back to $base. The corpus sync is running; tell Claude the test is finished."
}
trap 'echo; echo "Stopped early."; finish; exit 130' INT

echo "Each session: play the same stream map three times or more (goreshit - look at me tenderly"
echo "[()]: 51 plays on record, ~1,100 stream circles each), then the same jump map if you like,"
echo "then quit lazer. Keep the maps, their order and every other mod the same in every session."
[ "$mode" = offset ] && echo "Don't touch the offset in-game."
echo

for i in 1 2 3 4; do
    condition=${order[i-1]}
    set_offset "$(offset_for "$condition")"
    echo "Session $i of 4. Play the test maps, then quit lazer."
    case "$condition" in
        M) echo "  Muted ON: metronome OFF, inverse muting OFF, mute hit sounds as you normally have them." ;;
        N) echo "  Muted OFF." ;;
    esac
    started=$(date +%s)
    echo "session $i $condition start $(date -Iseconds)" >> "$record"

    game-performance "$lazer" >/dev/null 2>&1

    # A launch that returns at once handed off to a lazer that was already open, or
    # crashed; either way that session did not happen, so wait it out rather than skip.
    while running; do sleep 2; done
    echo "session $i $condition end $(date -Iseconds) ($(( $(date +%s) - started ))s)" >> "$record"

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
