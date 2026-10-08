"""The player's most-played beatmaps that the crowd-timing reference does not cover yet.

Ranked by paired plays in build/corpus.json, which is where a per-object crowd median
has the most of the player's data to stand against. "Covered" means the map already has
rows in build/map-bias.csv or no-mod replays in build/reference's manifest; the fetcher
would skip those scores anyway, but each skipped map still costs a listing request.

    python3 tools/reference/top_maps.py [count]      # comma-separated checksums
"""
import collections
import csv
import json
import sys

count = int(sys.argv[1]) if len(sys.argv) > 1 else 200

plays = collections.Counter(e["BeatmapMd5"] for e in json.load(open("build/corpus.json")) if e["Paired"])
covered = {r["beatmapMd5"] for r in csv.DictReader(open("build/map-bias.csv"))}
covered |= {v["beatmap_md5"] for v in json.load(open("build/reference/manifest.json")).values()
            if v.get("filter") == "NM"}

wanted = [md5 for md5, _ in plays.most_common(count) if md5 not in covered]
print(f"{len(wanted)} of the top {count} maps lack a reference", file=sys.stderr)
print(",".join(wanted))
