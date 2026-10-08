#!/bin/bash
# Bring the no-mod reference corpus up to date with whatever fetch.py has downloaded.
#
# build/reference holds every reference pull; build/reference-nm is its no-mod subset,
# as hard links (manifest filter NM and an empty mod list, which is what step 6 built
# by hand on 2026-09-13). From that directory: build/corpus-nm.json (survey),
# build/geometry-reference-nm.csv and build/rhythm-reference-nm.csv (default rhythm
# parameters, group gap 1 and tolerance 0.02, matching what is already there).
#
# Geometry and rhythm are per replay, so only replays absent from each CSV are extracted,
# to a side file whose header is checked before its rows are appended -- the same
# pattern as tools/corpus/sync.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."
ora=src/Cli/bin/Release/net10.0/ora

python3 - <<'PY'
import json, os
manifest = json.load(open("build/reference/manifest.json"))
linked = 0
for v in manifest.values():
    if v.get("filter") != "NM" or v["mods"]:
        continue
    target = f"build/reference-nm/{v['file']}"
    if not os.path.exists(target):
        os.link(f"build/reference/{v['file']}", target)
        linked += 1
print(f"reference-nm: {linked} new no-mod replays linked")
PY

before=$(python3 -c 'import json; print(len(json.load(open("build/corpus-nm.json"))))')
"$ora" survey --out build/corpus-nm.json.new build/reference-nm | grep -E '^(paired|decoded)'
python3 - <<'PY'
import json, os, sys
# By file name: the step-6 survey stored relative paths, a survey now stores absolute ones.
old = {os.path.basename(e["Path"]) for e in json.load(open("build/corpus-nm.json"))}
new = {os.path.basename(e["Path"]) for e in json.load(open("build/corpus-nm.json.new"))}
if old - new:
    sys.exit(f"corpus-nm.json.new lost {len(old - new)} entries; not replaced")
PY
mv build/corpus-nm.json.new build/corpus-nm.json
echo "corpus-nm: $before -> $(python3 -c 'import json; print(len(json.load(open("build/corpus-nm.json"))))')"

# extend <command> <csv>: extract only the replays missing from <csv>, then append.
extend() {
    local command=$1 csv=$2
    python3 - "$csv" <<'PY'
import csv, json, os, sys
have, last = set(), None
with open(sys.argv[1], newline="") as fh:
    rows = csv.reader(fh)
    next(rows)
    for row in rows:
        if row[0] != last:
            have.add(row[0])
            last = row[0]
new = [e for e in json.load(open("build/corpus-nm.json"))
       if e["Paired"] and os.path.basename(e["Path"]) not in have]
json.dump(new, open("build/corpus-nm-new.json", "w"))
print(f"{os.path.basename(sys.argv[1])}: {len(new)} replays to extract")
PY
    if [ "$(python3 -c 'import json; print(len(json.load(open("build/corpus-nm-new.json"))))')" -gt 0 ]; then
        "$ora" "$command" --corpus build/corpus-nm-new.json --out "$csv.new" | { grep -E '^(extracted|written)' || true; }
        if diff -q <(head -1 "$csv") <(head -1 "$csv.new") >/dev/null; then
            tail -n +2 "$csv.new" >> "$csv"
            rm "$csv.new"
        else
            # The extractor's columns changed since the CSV was written, so its old rows
            # cannot take new ones. Re-extract the whole corpus into a side file and swap.
            echo "$(basename "$csv"): columns changed; re-extracting every replay"
            "$ora" "$command" --corpus build/corpus-nm.json --out "$csv.new" | { grep -E '^(extracted|written)' || true; }
            mv "$csv.new" "$csv"
        fi
    fi
}

extend geometry build/geometry-reference-nm.csv
extend rhythm build/rhythm-reference-nm.csv
rm -f build/corpus-nm-new.json
