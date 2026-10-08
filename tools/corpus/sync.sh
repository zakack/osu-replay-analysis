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

# index first: a replay of a map downloaded since the last run only pairs once the beatmap
# index knows the map.
"$ora" index >/dev/null || fail "ora index"

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

added=$(( $(ls build/$player | wc -l) - before ))
echo "added $added replays"
[ "$added" -gt 0 ] && notify-send -a osu-replay-analysis "Corpus updated" "+$added replays" || true
