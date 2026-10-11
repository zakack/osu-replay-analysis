#!/bin/bash
# Bring build/zaksynack, build/corpus.json, build/geometry.csv and build/where.pickle up to date with lazer's store. Run after the
# game closes (see osu-lazer beside this), or by hand; it is idempotent and incremental.
#
# Why after close: index and scores both File.Copy the live client.realm, and a copy taken
# while lazer is writing it can be torn. That copy is the only step that races the game.
set -euo pipefail
cd "$(dirname "$0")/../.."

ora=src/Cli/bin/Release/net10.0/ora
player=zaksynack

exec 9>build/.corpus-sync.lock
flock -n 9 || { echo "another sync is running"; exit 0; }

# Lazer is single-instance: a second launch hands off over IPC and exits at once, so the
# wrapper returning does not mean the game has closed. The game's close will trigger again.
if pgrep -f 'osu\.AppImage|/\.mount_osu' >/dev/null; then
    echo "lazer is still running; skipping"
    exit 0
fi

fail() {
    echo "corpus sync failed: $1" >&2
    notify-send -a osu-replay-analysis "Corpus sync failed" "$1 — journalctl --user -t corpus-sync" || true
    exit 1
}

[ -x "$ora" ] || fail "no Release build at $ora (dotnet build src/Cli -c Release)"

before=$(ls build/$player | wc -l)

# Lazer's runtime log is the only record of attempts that never became a score: every quick
# retry and quit shows as a SoloPlayer entered/exited pair under the map name. Lazer keeps
# about a week of them, so copy them out; never delete from the archive.
mkdir -p build/lazer-logs
cp -n ~/.config/osu-lazer/logs/*.log build/lazer-logs/ 2>/dev/null || true

# index first: a replay of a map downloaded since the last run only pairs once the beatmap
# index knows the map.
"$ora" index >/dev/null || fail "ora index"

# Per-beatmap offsets shift recorded hit error and no score stores them; realm keeps only
# the current value. Diff it against the last snapshot and log each change, bounded by the
# two snapshot times, so a change can be placed in the session it happened in.
python3 - <<'PY' || fail "offset history"
import csv, json, os, time
state, history = "build/offset-state.json", "build/offset-history.csv"
now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
index = json.load(open("build/beatmap-index.json"))
current = {k: v.get("Offset", 0) for k, v in index.items()}
if os.path.exists(state):
    last = json.load(open(state))
    new_file = not os.path.exists(history)
    changed = [k for k, o in current.items() if abs(o - last["offsets"].get(k, 0)) > 1e-9]
    with open(history, "a", newline="") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(["after", "before", "md5", "title", "difficulty", "old", "new"])
        for k in changed:
            v = index[k]
            w.writerow([last["time"], now, k, v["Title"], v["Difficulty"], last["offsets"].get(k, 0), current[k]])
    print(f"offsets: {len(changed)} beatmaps changed since {last['time']}")
json.dump({"time": now, "offsets": current}, open(state, "w"))
PY

# Never --since. Export numbers filename collisions against the scores selected in the same
# run, so a partial selection would count a colliding new score as already present and
# drop it. The full selection is cheap because existing files are skipped.
"$ora" scores --user "$player" --export "build/$player" | grep -E '^(selected|exported|  already|  no file)' \
    || fail "ora scores"

# Survey to a side file and only replace the corpus if nothing it had is gone, so a run
# against a realm schema the pinned submodule cannot read never quietly empties it.
"$ora" survey --out build/corpus.json.new "build/$player" | grep -E '^(paired|decoded|written)' \
    || fail "ora survey"

python3 - <<'PY' || fail "new corpus.json lost entries; left at build/corpus.json.new"
import json, sys
old = {e["Path"]: e for e in json.load(open("build/corpus.json"))}
new = {e["Path"]: e for e in json.load(open("build/corpus.json.new"))}
lost = [p for p in old if p not in new]
unpaired = [p for p in old if old[p]["Paired"] and not new[p]["Paired"]] if not lost else []
print(f"corpus: {len(old)} -> {len(new)}")
sys.exit(1 if lost or unpaired else 0)
PY
mv build/corpus.json.new build/corpus.json

# Geometry is per replay, so only replays not yet in geometry.csv are extracted, to a side
# file first so a failed run never leaves a half-written block in the 1.7GB CSV. Then
# where.py's cache is rebuilt from it; together about a minute after a session.
python3 - <<'PY' || fail "selecting replays for geometry"
import csv, json, os
have, last = set(), None
with open("build/geometry.csv", newline="") as fh:
    rows = csv.reader(fh)
    next(rows)
    for row in rows:
        if row[0] != last:
            have.add(row[0])
            last = row[0]
new = [e for e in json.load(open("build/corpus.json"))
       if e["Paired"] and os.path.basename(e["Path"]) not in have]
json.dump(new, open("build/corpus-geom-new.json", "w"))
print(f"geometry: {len(new)} replays to extract")
PY
if [ "$(python3 -c 'import json; print(len(json.load(open("build/corpus-geom-new.json"))))')" -gt 0 ]; then
    "$ora" geometry --corpus build/corpus-geom-new.json --out build/geometry-new.csv \
        | grep -E '^(extracted|angle)' || fail "ora geometry"
    diff -q <(head -1 build/geometry.csv) <(head -1 build/geometry-new.csv) >/dev/null \
        || fail "geometry-new.csv header differs from geometry.csv; not appended"
    tail -n +2 build/geometry-new.csv >> build/geometry.csv
    python3 tools/analysis/where.py reduce || fail "where.py reduce"
fi

# Onset times live in their own file and are extended the same way. Unlike geometry this
# runs on every core: the whole corpus took 38 s. If ora's columns no longer match the file,
# appended rows would not line up, so the file is rebuilt instead. That is checked against
# an empty run every time, not only when there is something to add. A replay with no
# directed jumps writes no rows and is re-selected every run, which costs about a second.
# Per-replay extractors with their own files (ora onset, ora clicks): extend each with the
# replays it lacks, or rebuild it whole when the extractor's columns have changed, since rows
# with a different column set cannot be appended. Both run on every core.
incremental() {
    local cmd=$1 csv=build/$1.csv
    shift
    local extra=("$@")
    [ -f "$csv" ] || return 0
    echo '[]' > "build/corpus-$cmd-new.json"
    "$ora" "$cmd" "${extra[@]}" --corpus "build/corpus-$cmd-new.json" --out "build/$cmd-new.csv" >/dev/null || fail "ora $cmd (header)"
    if ! diff -q <(head -1 "$csv") <(head -1 "build/$cmd-new.csv") >/dev/null; then
        echo "$cmd: columns changed; rebuilding $csv for the whole corpus"
        "$ora" "$cmd" "${extra[@]}" --out "$csv.new" | grep -E '^extracted' || fail "ora $cmd (rebuild)"
        mv "$csv.new" "$csv"
        grew="$grew $cmd"
        return 0
    fi
    python3 - "$cmd" "$csv" <<'PY' || fail "selecting replays for $cmd"
import csv, json, os, sys
cmd, path = sys.argv[1:]
have, last = set(), None
with open(path, newline="") as fh:
    rows = csv.reader(fh)
    next(rows)
    for row in rows:
        if row[0] != last:
            have.add(row[0])
            last = row[0]
new = [e for e in json.load(open("build/corpus.json"))
       if e["Paired"] and os.path.basename(e["Path"]) not in have]
json.dump(new, open(f"build/corpus-{cmd}-new.json", "w"))
print(f"{cmd}: {len(new)} replays to extract")
PY
    if [ "$(python3 -c "import json; print(len(json.load(open('build/corpus-$cmd-new.json'))))")" -gt 0 ]; then
        "$ora" "$cmd" "${extra[@]}" --corpus "build/corpus-$cmd-new.json" --out "build/$cmd-new.csv" \
            | grep -E '^extracted' || fail "ora $cmd"
        tail -n +2 "build/$cmd-new.csv" >> "$csv"
        # A replay with nothing to extract writes no rows and is picked again next run.
        if [ "$(wc -l < "build/$cmd-new.csv")" -gt 1 ]; then grew="$grew $cmd"; fi
    fi
}
grew=""
incremental onset
incremental clicks
# The stream analyses (streams.py, drift.py, recovery.py) read rhythm.csv and presses.csv.
# rhythm's thresholds are the ones the stream analyses were built on: group gap 1 beat,
# tolerances 0.01/0.02/0.04. Its cache is rebuilt from both whenever either grew (~50 s).
incremental rhythm 1 0.01 0.02 0.04
incremental presses
if [[ "$grew" == *rhythm* || "$grew" == *presses* ]]; then
    python3 tools/analysis/streams.py cache | tail -1 || fail "streams.py cache"
fi

# Line each session's doubletap and cursor traces up with the replays saved during it
# (tools/traces/align.py, which needs presses.csv current). Once per trace; a trace whose
# zstd stream does not test clean is still being written, and the next sync takes it.
for t in build/traces/*.csv.zst; do
    [[ "$t" == *.cursor.csv.zst ]] && continue
    stem=${t%.csv.zst}
    [ -e "$stem.align.json" ] && continue
    zstd -tq "$t" 2>/dev/null || continue
    [ ! -e "$stem.cursor.csv.zst" ] || zstd -tq "$stem.cursor.csv.zst" 2>/dev/null || continue
    echo "align $(basename "$stem")"
    python3 tools/traces/align.py "$stem" | sed 's/^/  /' || fail "align.py $(basename "$stem")"
done

# The tapping-hand regression check (tools/analysis/drift.py): the latest sessions against
# the trailing 30 days. It stays silent below the beatmap count a CI needs, so the pool of
# recent sessions widens until it holds enough maps; a notification only on REGRESSION.
for n in 1 2 3 5 8 12 20; do
    drift=$(python3 tools/analysis/drift.py latest --sessions $n 2>&1) || fail "drift.py latest"
    grep -q "cannot carry a CI" <<<"$drift" || break
done
echo "$drift" | tail -4
if grep -q REGRESSION <<<"$drift"; then
    notify-send -a osu-replay-analysis -u critical "Tapping regression?" \
        "$(grep REGRESSION <<<"$drift" | head -3)" || true
fi

added=$(( $(ls build/$player | wc -l) - before ))
echo "added $added replays"
[ "$added" -gt 0 ] && notify-send -a osu-replay-analysis "Corpus updated" "+$added replays" || true
