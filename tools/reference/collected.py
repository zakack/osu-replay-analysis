"""Build a corpus manifest from the firehose and daily archives: players who miss.

The reference corpus is top-50 boards, and top-50 players essentially do not miss -- their
miss rate is 0.05%, so per-cell miss estimates off it are Poisson noise and a miss-rate
finding cannot be validated against anybody but the subject. That is the gap this fills.
The collectors have been running since September and the two archives between them hold
thousands of ordinary players, 91% of whose runs miss at least once.

The two have complementary shapes and both are worth having:

* **dailies** -- thousands of players over a couple of dozen daily-challenge maps. Deep per
  map, the reference-lap structure, but across the whole skill range instead of the top of
  it.
* **firehose** -- roughly one replay each over fifteen hundred maps. Broad, which is what a
  held-out-beatmap split needs.

Nothing here touches the API. A replay's `.osr` header carries the beatmap's MD5, so the
join to the local library is a read of the first few bytes of each file; the archives'
JSONL does not carry the md5 at all and `beatmap_id` would need resolving.

Two filters, and the second is the one that costs replays:

* The beatmap has to be in the local library, since nothing can be computed without it.
* No rate-changing mod. Spacing per millisecond is the axis half this analysis turns on,
  and DT moves it. Mods that leave the clock alone are kept -- the geometry pass reads the
  mod list and applies it, so Hidden or Mirror are no obstacle.

    python3 tools/reference/collected.py --out build/corpus-collected.json
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVES = (("dailies", "/mnt/datasets/osu-dailies"),
            ("firehose", "/mnt/datasets/osu-firehose"))
RATE_MODS = {"DT", "NC", "HT", "DC"}


def osr_md5(path: str) -> str | None:
    """The beatmap MD5 out of an .osr header.

    Layout: one byte of ruleset, a four-byte version, then osu!'s string encoding -- 0x00
    for absent, else 0x0b and a ULEB128 length. Read rather than decoded through the shim
    because this runs over every file in the archive and only needs the first few bytes.
    """
    with open(path, "rb") as f:
        f.read(5)
        if f.read(1) != b"\x0b":
            return None
        n = shift = 0
        while True:
            byte = f.read(1)[0]
            n |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        return f.read(n).decode("ascii", "replace")


def acronyms(record) -> set[str]:
    out = set()
    for mod in record.get("mods", []):
        out.add(mod if isinstance(mod, str) else mod.get("acronym", ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build/corpus-collected.json")
    ap.add_argument("--allow-rate", action="store_true",
                    help="keep DT/HT runs too; off, because they move spacing per ms")
    args = ap.parse_args()

    index = json.load(open(os.path.join(ROOT, "build", "beatmap-index.json")))
    out, stats = [], collections.Counter()
    missing_maps = collections.Counter()

    for name, root in ARCHIVES:
        meta = {}
        listing = os.path.join(root, "replays.jsonl")
        if os.path.exists(listing):
            for line in open(listing):
                record = json.loads(line)
                meta[record["file"]] = record

        for path in sorted(glob.glob(os.path.join(root, "replays", "*.osr"))):
            stats[f"{name}: found"] += 1
            record = meta.get(os.path.basename(path), {})

            if not args.allow_rate and (acronyms(record) & RATE_MODS):
                stats[f"{name}: rate mod"] += 1
                continue

            try:
                md5 = osr_md5(path)
            except (OSError, IndexError):
                stats[f"{name}: unreadable"] += 1
                continue

            entry = index.get(md5) if md5 else None
            if entry is None:
                stats[f"{name}: beatmap not held"] += 1
                if md5:
                    missing_maps[md5] += 1
                continue

            out.append({
                "Path": path,
                "BeatmapMd5": md5,
                "BeatmapPath": entry["Path"],
                "Paired": True,
                "Source": name,
                "UserId": record.get("user_id"),
                "Misses": record.get("statistics", {}).get("miss", 0),
                "Accuracy": record.get("accuracy"),
                "Rank": record.get("rank"),
                "Mods": sorted(acronyms(record)),
            })
            stats[f"{name}: usable"] += 1

    json.dump(out, open(os.path.join(ROOT, args.out), "w"))

    for key in sorted(stats):
        print(f"  {key:<34}{stats[key]:>7,}")
    players = {r["UserId"] for r in out if r["UserId"]}
    maps = {r["BeatmapMd5"] for r in out}
    print(f"\n{len(out):,} usable replays, {len(players):,} players, {len(maps):,} beatmaps, "
          f"{sum(r['Misses'] or 0 for r in out):,} misses")
    print(f"beatmaps referenced but not held: {len(missing_maps):,} "
          f"({sum(missing_maps.values()):,} replays)")
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
