"""List the beatmaps the collected archives reference but the local library does not hold.

A replay is useless without its beatmap, and the firehose reaches well past what any one
player has imported. This writes what is needed to go and get them.

The md5 in an `.osr` header identifies a beatmap but cannot be searched for, so the online
id is resolved too -- from the firehose's own per-replay `beatmap_id`, and for the dailies
from the `room-*.jsonl` snapshots, whose `meta` block carries the id and the name of each
day's map. Rows are sorted by how many replays each would unlock, so a partial acquisition
is still the most useful partial one.

    python3 tools/reference/missingmaps.py --out build/missing-beatmaps.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collected import ARCHIVES, RATE_MODS, acronyms, osr_md5  # noqa: E402


def daily_rooms(root: str) -> dict[int, dict]:
    """room id -> that day's map, from the leaderboard snapshots."""
    rooms = {}
    for path in glob.glob(os.path.join(root, "room-*.jsonl")):
        for line in open(path):
            meta = json.loads(line).get("meta") or {}
            if meta.get("room_id") and meta.get("beatmap_id"):
                rooms[meta["room_id"]] = meta
    return rooms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build/missing-beatmaps.csv")
    ap.add_argument("--allow-rate", action="store_true",
                    help="count DT/HT replays too; off, to match the corpus builder")
    args = ap.parse_args()

    index = json.load(open(os.path.join(ROOT, "build", "beatmap-index.json")))
    found: dict[str, dict] = {}

    for name, root in ARCHIVES:
        rooms = daily_rooms(root) if name == "dailies" else {}
        meta = {}
        listing = os.path.join(root, "replays.jsonl")
        if os.path.exists(listing):
            for line in open(listing):
                record = json.loads(line)
                meta[record["file"]] = record

        for path in sorted(glob.glob(os.path.join(root, "replays", "*.osr"))):
            record = meta.get(os.path.basename(path), {})
            if not args.allow_rate and (acronyms(record) & RATE_MODS):
                continue
            try:
                md5 = osr_md5(path)
            except (OSError, IndexError):
                continue
            if not md5 or md5 in index:
                continue

            row = found.setdefault(md5, {
                "md5": md5, "beatmapId": "", "title": "", "stars": "",
                "source": name, "replays": 0, "misses": 0,
            })
            row["replays"] += 1
            row["misses"] += (record.get("statistics") or {}).get("miss", 0) or 0

            if not row["beatmapId"]:
                if name == "firehose" and record.get("beatmap_id"):
                    row["beatmapId"] = record["beatmap_id"]
                elif name == "dailies":
                    day = rooms.get(record.get("room_id"), {})
                    if day.get("beatmap_id"):
                        row["beatmapId"] = day["beatmap_id"]
                        row["title"] = (f"{day.get('artist','')} - {day.get('title','')} "
                                        f"[{day.get('version','')}]").strip()
                        row["stars"] = f"{day.get('difficulty_rating', 0):.2f}"

    rows = sorted(found.values(), key=lambda r: -r["replays"])
    out = os.path.join(ROOT, args.out)
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, ["md5", "beatmapId", "title", "stars",
                                    "source", "replays", "misses"])
        writer.writeheader()
        writer.writerows(rows)

    named = sum(1 for r in rows if r["beatmapId"])
    by = collections.Counter(r["source"] for r in rows)
    print(f"{len(rows):,} beatmaps not held, worth {sum(r['replays'] for r in rows):,} "
          f"replays and {sum(r['misses'] for r in rows):,} misses")
    print(f"  with an online id resolved: {named:,} of {len(rows):,}")
    print(f"  by archive: {dict(by)}")
    print(f"\ntop by replays unlocked:")
    for r in rows[:8]:
        label = r["title"] or f"beatmap {r['beatmapId'] or '?'}"
        print(f"  {r['replays']:>5} replays  {r['misses']:>7,} misses  "
              f"{str(r['beatmapId']):>9}  {label[:52]}")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
