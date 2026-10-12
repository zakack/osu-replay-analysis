"""Split a map into repeated multi-object patterns, and see how much of it they cover.

A cell describes one object. A player does not practise an object, and `unitsize.py` showed
the object is not even the most predictive unit -- how deep into a run it sits beats what
shape its turn is. So the unit wanted here is a *pattern*: a short run of objects that the
map repeats, found from the map's own geometry and rhythm and from nothing else.

The method, in four steps:

1. **Spell.** Every clicked object becomes a symbol built from the axes the findings table
   already bins on, through the schema's own ``Axis.bin`` so the two cannot drift:

       (snap, spacing band, |angle| band, kind)

   Chirality is folded out of the spelling -- Mirror week settled it, and a pattern and its
   reflection are the same pattern. The sign stays in the data.

2. **Segment at group boundaries.** ``Rhythm.Groups`` already cuts the map wherever there is
   a gap over one beat. A repeat that straddles a rest is not a pattern anyone perceives, so
   patterns are sought inside groups and never across them. Objects in no group -- ones and
   twos between rests -- are uncovered by construction, and counted as such rather than
   forced in.

3. **Find repeats.** Every substring of length >= 2 that occurs at least twice within the
   map, counted over *non-overlapping* occurrences so a long stream is not scored once per
   starting offset.

4. **Tile.** Patterns are taken longest-first, by occurrences x length, each claiming only
   objects no earlier pattern claimed. Coverage is the share of a map's objects a pattern
   ends up claiming.

Coverage is reported twice, and the second number is the interesting one. A 128-note stream
spells as one symbol repeated 128 times and tiles itself perfectly, which is a real pattern
but tells nobody anything. **Non-constant** coverage -- tiles whose symbols are not all
identical -- is what says whether osu! maps repeat multi-object *shapes* at all.

Four alphabets are run, because the full spelling is about 1,150 symbols wide and exact
repeats at that resolution are rare. The falloff across the ladder is the finding, not a
nuisance: it prices what each axis costs in repeatability.

    python3 tools/analysis/patterns.py --maps <md5> <md5> ...
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bins.schema import ANGLE, SPACING  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOLERANCE = "0.02"      # the rhythm run segmentation the findings table uses
DEG = 180.0 / math.pi

# snap, spacing, |angle|, kind -- each level adds one component to the symbol.
ALPHABETS = ("snap", "snap+spacing", "snap+spacing+|angle|", "snap+spacing+|angle|+kind")


def _num(raw):
    return float(raw) if raw not in ("", None) else None


def spell(row, level):
    """One object's symbol at one alphabet level, or None if it cannot be spelled."""
    snap = row["snap"]
    if not snap:
        return None
    parts = [snap]
    if level >= 1:
        s = _num(row["spacingRadii"])
        b = SPACING.bin(s) if s is not None else None
        if b is None:
            return None
        parts.append(b)
    if level >= 2:
        a = _num(row["signedAngle"])
        # Folded. A pattern and its mirror image are the same pattern.
        b = ANGLE.bin(abs(a) * DEG) if a is not None else None
        if b is None:
            return None
        parts.append(b)
    if level >= 3:
        parts.append(row["kind"][:2])
    return "|".join(parts)


def read_maps(replays: dict[str, str]) -> dict[str, list[dict]]:
    """Every chosen map's clicked objects in map order, geometry joined to rhythm.

    Read from the CSVs directly rather than through the bins package: that drops objects
    this player missed and objects never clicked, and a map's pattern structure is a
    property of the map, not of how one run of it went.

    One pass per file for all of the maps at once. The two files are 1.4GB together, so a
    pass per map is most of half an hour spent re-reading rows nobody asked for.

    ``replays`` maps beatmap md5 -> the one replay name to take the map's geometry from.
    """
    wanted = {name: md5 for md5, name in replays.items()}

    rhythm: dict[str, dict] = {md5: {} for md5 in replays}
    with open(os.path.join(ROOT, "build", "rhythm.csv")) as f:
        for r in csv.DictReader(f):
            md5 = wanted.get(r["replay"])
            if md5 is None or r["tolerance"] != TOLERANCE or r["clickIndex"] == "-1":
                continue
            rhythm[md5][round(float(r["time"]), 3)] = (r["snap"] or None, int(r["groupId"]),
                                                       int(r["runIndex"]), int(r["runSize"]))

    # Rhythm.cs writes a row only for objects *inside* a group, so anything between rests
    # never reaches the join. That is the right segmentation but the wrong denominator:
    # coverage has to be a share of the map, so the geometry rows are counted too.
    objects: dict[str, list[dict]] = {md5: [] for md5 in replays}
    total: dict[str, int] = {md5: 0 for md5 in replays}
    with open(os.path.join(ROOT, "build", "geometry.csv")) as f:
        for r in csv.DictReader(f):
            md5 = wanted.get(r["replay"])
            if md5 is None:
                continue
            total[md5] += 1
            got = rhythm[md5].get(round(float(r["startTime"]), 3))
            if got is None:
                continue
            snap, group, run_index, run_size = got
            objects[md5].append({**r, "snap": snap, "group": group,
                                 "runIndex": run_index, "runSize": run_size,
                                 "t": float(r["startTime"])})

    for rows in objects.values():
        rows.sort(key=lambda o: o["t"])
    return objects, total


def segments(objects, level):
    """Spelled runs of objects, cut at group boundaries and wherever a symbol is missing.

    Yields (start index into `objects`, list of symbols).
    """
    out, current, start = [], [], 0
    last_group = None
    for i, o in enumerate(objects):
        sym = spell(o, level)
        if sym is None or o["group"] != last_group:
            if len(current) >= 2:
                out.append((start, current))
            current, start = [], i
            last_group = o["group"] if sym is not None else None
            if sym is None:
                last_group = None
                continue
        if not current:
            start = i
        current.append(sym)
    if len(current) >= 2:
        out.append((start, current))
    return out


def repeats(segs, min_len=2, max_len=24):
    """Every substring occurring at least twice across the map, with its occurrences.

    Occurrences are collected over every segment, so a pattern that appears once in each of
    two phrases counts as repeated -- that is the whole point. Overlap is resolved later, at
    tiling time, not here.
    """
    found = collections.defaultdict(list)
    for start, syms in segs:
        n = len(syms)
        for i in range(n):
            for L in range(min_len, min(max_len, n - i) + 1):
                found[tuple(syms[i:i + L])].append(start + i)
    return {p: locs for p, locs in found.items() if len(locs) >= 2}


def tile(found, total, min_len):
    """Claim objects for patterns, best first, without overlaps.

    Value is occurrences x length: the number of objects a pattern would explain. Within a
    pattern, occurrences are taken left to right and one that collides with an already-claimed
    object is dropped, so a stream is tiled rather than counted once per offset.
    """
    claimed = [None] * total
    kept = []
    order = sorted(found, key=lambda p: (-len(p) * len(found[p]), -len(p)))
    for pattern in order:
        if len(pattern) < min_len:
            continue
        taken = []
        for at in sorted(found[pattern]):
            span = range(at, at + len(pattern))
            if any(claimed[j] is not None for j in span):
                continue
            taken.append(at)
        if len(taken) < 2:
            continue
        for at in taken:
            for j in range(at, at + len(pattern)):
                claimed[j] = pattern
        kept.append((pattern, taken))
    return claimed, kept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--maps", nargs="+", required=True, help="beatmap md5 prefixes")
    parser.add_argument("--min-len", type=int, default=3)
    parser.add_argument("--examples", type=int, default=1)
    parser.add_argument("--cache", help="parse once into this JSON and reuse it after; the "
                                        "two source CSVs are 1.4GB and the parse dominates")
    args = parser.parse_args()

    corpus = json.load(open(os.path.join(ROOT, "build", "corpus.json")))
    index = json.load(open(os.path.join(ROOT, "build", "beatmap-index.json")))

    chosen = {}
    for prefix in args.maps:
        md5 = next(r["BeatmapMd5"] for r in corpus if r["BeatmapMd5"].startswith(prefix))
        path = next(r["Path"] for r in corpus if r["BeatmapMd5"] == md5 and r.get("Paired"))
        chosen[md5] = os.path.basename(path)

    if args.cache and os.path.exists(args.cache):
        blob = json.load(open(args.cache))
        loaded = {k: blob["objects"][k] for k in chosen if k in blob["objects"]}
        total = {k: blob["total"][k] for k in chosen if k in blob["total"]}
    else:
        loaded, total = read_maps(chosen)
        if args.cache:
            json.dump({"objects": loaded, "total": total}, open(args.cache, "w"))

    print(f"min pattern length {args.min_len}, chirality folded, patterns confined to "
          f"rhythm groups.\n'objs' is every object on the map; 'grp' the share of them "
          f"inside a group, which is all a pattern\ncan ever claim. 'shape' is coverage "
          f"by tiles that are not one symbol repeated.\n")
    header = (f"{'map':<32}{'objs':>6}{'grp':>5}  {'alphabet':<22}{'syms':>6}"
              f"{'pats':>6}{'cover':>7}{'shape':>7}{'med':>5}")
    print(header)

    for md5 in chosen:
        objects = loaded[md5]
        objs = total[md5]
        meta = index.get(md5, {})
        name = f"{str(meta.get('Title','?'))[:22]} [{str(meta.get('Difficulty','?'))[:8]}]"
        for level, alphabet in enumerate(ALPHABETS):
            segs = segments(objects, level)
            found = repeats(segs, min_len=args.min_len)
            claimed, kept = tile(found, len(objects), args.min_len)
            cover = sum(1 for c in claimed if c is not None) / max(objs, 1)
            nonconst = sum(1 for c in claimed if c is not None
                           and len(set(c)) > 1) / max(objs, 1)
            alpha = len({s for _, syms in segs for s in syms})
            lens = sorted(len(p) for p, _ in kept)
            med = lens[len(lens)//2] if lens else 0
            grouped = f"{len(objects)/max(objs,1):.0%}" if level == 0 else ""
            print(f"{name if level==0 else '':<32}{objs if level==0 else '':>6}{grouped:>5}  "
                  f"{alphabet:<22}{alpha:>6}{len(kept):>6}{cover:>6.0%}{nonconst:>7.0%}{med:>5}")

            if level == len(ALPHABETS) - 1 and args.examples:
                shaped = [(p, locs) for p, locs in kept if len(set(p)) > 1]
                shaped.sort(key=lambda kv: -len(kv[1]) * len(kv[0]))
                for p, locs in shaped[:args.examples]:
                    print(f"{'':<39}{'most-repeated shape: ':<22}"
                          f"x{len(locs)}  " + "  ".join(p[:4]) + (" ..." if len(p) > 4 else ""))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
