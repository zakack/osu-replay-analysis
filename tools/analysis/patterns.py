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

Snap and spacing are always in the symbol. What varies across the rows is how finely the
turn is spelled, because that is the axis that decides whether anything repeats at all: a
30-degree grid over a continuous quantity breaks a repeat whenever one object of it sits
near a band edge, and measured here it costs more than half the shapes on some maps.

The bands worth having are not a grid. ppy's own aim evaluator does not bin angle -- it
ramps between 40 and 140 degrees on the unsigned angle -- so those two knees are the only
externally motivated place to cut, and `ppy 3` beats the 30-degree grid on every map tried.
Note what that does and does not say: coarsening is not approximate matching. Matching stays
exact, and which band an object is in stays a property of that object alone, which is the
charter's rule and the thing an edit-distance match would give up.

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

# How coarsely to spell the turn. The 30-degree grid is the findings table's, and it is an
# arbitrary grid: nothing about play changes at 60 degrees. ppy's own aim evaluator does not
# bin angle at all -- SnapAimEvaluator ramps smoothly between 40 and 140 degrees, on the
# unsigned angle, which is where "acute" stops and "wide" starts in the only published model
# of this. Those two numbers are the one externally motivated place to cut.
#
# A symbol has to be discrete, so the ramp cannot be used directly. The nearest honest thing
# is to cut at its knees and let the continuous value stay an attribute of the instance.
ANGLE_BANDS = {
    "none":    None,
    "2 band":  (90.0,),
    "ppy 3":   (40.0, 140.0),
    "6 band":  (30.0, 60.0, 90.0, 120.0, 150.0),
    # The schema's own, measured rather than chosen, so a pattern's spelling and the
    # findings table cannot disagree about what band an object is in.
    "schema4": tuple(ANGLE.edges[1:-1]),
    # For naming shapes rather than predicting error, which are different jobs. The cell
    # axis was cut where miss rate and aim error bend; a triangle and a square bend nothing
    # and are still obviously different patterns.
    #
    # The two extra edges are where the corpus is empty, not where geometry says a polygon
    # ought to be. Over 435 steady one-way-round runs of four turns, the mean turn has
    # troughs at 70-75 and 95-100 degrees and clusters either side; 50 is already a trough,
    # which is some reassurance about the cell axis. Measured before placing them, because
    # "triangles and squares are different" is exactly the kind of thing one can find in
    # data by expecting it.
    #
    # Note the set is a strict refinement of schema4: every naming band nests inside one
    # cell band, so a pattern's name and its cell can never disagree.
    "naming": (5.0, 50.0, 72.0, 97.0, 135.0, 160.0),
}

def angle_symbol(degrees, edges):
    """Which band an unsigned turn falls in. 180 is straight through, 0 doubles back."""
    if edges is None:
        return ""
    lo = 0.0
    for i, e in enumerate(edges):
        if degrees < e:
            return f"a{i}"
        lo = e
    return f"a{len(edges)}"


def _num(raw):
    return float(raw) if raw not in ("", None) else None


def flow(previous, current):
    """Did this turn go the same way round as the one before it?

    One bit, and it is not chirality. Per-object sign was folded out because Mirror week
    found a left turn costs what a right turn costs. Whether two consecutive turns *agree*
    is a different property, and a mirror cannot touch it: reflecting a map flips both
    signs, so same stays same. Without it a square and a 90-degree back-and-forth spell
    identically, and so do a star and a zigzag -- measured on the shared corpus, four
    turns of 50-135 degrees at 4.5-6 radii are 79% one-way-round, while the same spelling
    at 2-2.5 radii is 43% alternating.

    ``s`` same way round, ``o`` opposite, ``.`` nothing to compare against.
    """
    if previous is None or current is None:
        return "."
    return "s" if (previous >= 0) == (current >= 0) else "o"


def spell(row, edges, with_kind):
    """One object's symbol, or None if it cannot be spelled.

    Always snap and spacing; the turn at whatever resolution `edges` asks for. Folded,
    because a pattern and its mirror image are the same pattern.
    """
    snap = row["snap"]
    if not snap:
        return None
    s = _num(row["spacingRadii"])
    band = SPACING.bin(s) if s is not None else None
    if band is None:
        return None
    parts = [snap, band]
    if edges is not None:
        a = _num(row["signedAngle"])
        if a is None:
            return None
        parts.append(angle_symbol(abs(a) * DEG, edges))
    if with_kind:
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


def segments(objects, edges, with_kind, with_flow=False):
    """Spelled runs of objects, cut at group boundaries and wherever a symbol is missing.

    Yields (start index into `objects`, list of symbols).
    """
    out, current, start = [], [], 0
    last_group = None
    previous_angle = None
    for i, o in enumerate(objects):
        sym = spell(o, edges, with_kind)

        if sym is None or o["group"] != last_group:
            if len(current) >= 2:
                out.append((start, current))
            current, start = [], i
            # A phrase begins with nothing to compare against. Carrying the previous
            # segment's turn across a rest would read a relationship between two objects
            # with a gap between them.
            previous_angle = None
            last_group = o["group"] if sym is not None else None
            if sym is None:
                last_group = None
                continue

        angle = _num(o["signedAngle"])
        if with_flow:
            sym += "|" + flow(previous_angle, angle)
        previous_angle = angle

        if not current:
            start = i
        current.append(sym)
    if len(current) >= 2:
        out.append((start, current))
    return out


def repeats(segs, min_len=2, max_len=24, keep=None):
    """Every substring of one map, with where it occurs.

    Occurrences are collected over every segment, so a pattern appearing once in each of two
    phrases counts as repeated. Overlap is resolved later, at tiling time, not here.

    With ``keep``, a pattern survives because the *corpus* vouches for it -- it was seen on
    enough other beatmaps -- rather than because this map happened to repeat it. That is the
    difference between a map's own quirk and a pattern in the language: a figure played on
    forty maps is a thing worth naming and practising even where a map uses it once.
    """
    found = collections.defaultdict(list)
    for start, syms in segs:
        n = len(syms)
        for i in range(n):
            for L in range(min_len, min(max_len, n - i) + 1):
                found[tuple(syms[i:i + L])].append(start + i)
    if keep is not None:
        return {p: locs for p, locs in found.items() if p in keep}
    return {p: locs for p, locs in found.items() if len(locs) >= 2}


def vocabulary(spelled, min_len, max_len, min_maps):
    """Sequences the corpus shares: {pattern: number of beatmaps it appears on}.

    Counted once per beatmap, never once per occurrence, so a map that repeats one figure
    two hundred times cannot vote itself a vocabulary.
    """
    counts: collections.Counter = collections.Counter()
    for segs in spelled.values():
        # One map's distinct substrings, then one increment each. The same answer as a set
        # of beatmap ids per substring and a great deal smaller: at corpus scale there are
        # millions of distinct substrings, and holding a set for every one of them costs
        # gigabytes that buy nothing, since the ids are never read back.
        here = set()
        for _, syms in segs:
            n = len(syms)
            for i in range(n):
                for L in range(min_len, min(max_len, n - i) + 1):
                    here.add(tuple(syms[i:i + L]))
        counts.update(here)
    return {p: c for p, c in counts.items() if c >= min_maps}


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
    parser.add_argument("--maps", nargs="*", default=[], help="beatmap md5 prefixes")
    parser.add_argument("--shared", action="store_true",
                        help="instead, every beatmap the reference corpus also covers")
    parser.add_argument("--cross", type=int, default=0,
                        help="only count patterns the corpus shares across at least this "
                             "many beatmaps; 0 keeps the within-map rule")
    parser.add_argument("--max-maps", type=int, default=0, help="cap the map count")
    parser.add_argument("--min-len", type=int, default=3)
    parser.add_argument("--examples", type=int, default=1)
    parser.add_argument("--show", default="ppy 3", choices=list(ANGLE_BANDS),
                        help="which banding to print example patterns from")
    parser.add_argument("--flow", action="store_true",
                        help="add the same-way-round bit, which tells a star from a zigzag")
    parser.add_argument("--cache", help="parse once into this JSON and reuse it after; the "
                                        "two source CSVs are 1.4GB and the parse dominates")
    args = parser.parse_args()

    corpus = json.load(open(os.path.join(ROOT, "build", "corpus.json")))
    index = json.load(open(os.path.join(ROOT, "build", "beatmap-index.json")))

    chosen = {}
    if args.shared:
        reference = {r["BeatmapMd5"] for r in
                     json.load(open(os.path.join(ROOT, "build", "corpus-nm.json")))}
        for record in corpus:
            if record["BeatmapMd5"] in reference and record.get("Paired"):
                chosen.setdefault(record["BeatmapMd5"], os.path.basename(record["Path"]))
    for prefix in args.maps:
        md5 = next(r["BeatmapMd5"] for r in corpus if r["BeatmapMd5"].startswith(prefix))
        path = next(r["Path"] for r in corpus if r["BeatmapMd5"] == md5 and r.get("Paired"))
        chosen[md5] = os.path.basename(path)
    if args.max_maps:
        chosen = dict(list(chosen.items())[:args.max_maps])
    if not chosen:
        raise SystemExit("no beatmaps chosen; pass --maps or --shared")

    if args.cache and os.path.exists(args.cache):
        blob = json.load(open(args.cache))
        loaded = {k: blob["objects"][k] for k in chosen if k in blob["objects"]}
        total = {k: blob["total"][k] for k in chosen if k in blob["total"]}
    else:
        loaded, total = read_maps(chosen)
        if args.cache:
            json.dump({"objects": loaded, "total": total}, open(args.cache, "w"))

    if args.cross:
        edges = ANGLE_BANDS[args.show]
        spelled = {md5: segments(loaded[md5], edges, False, with_flow=args.flow)
                   for md5 in chosen}
        shared = vocabulary(spelled, args.min_len, 8, args.cross)

        print(f"{len(chosen)} beatmaps, turn spelled {args.show!r}"
              f"{' + flow' if args.flow else ''}, pattern length {args.min_len}-8, "
              f"kept when seen on {args.cross}+ beatmaps\n")
        print(f"sequences shared across the corpus: {len(shared):,}")

        claimed = tiled = objects = 0
        spread: collections.Counter = collections.Counter()
        for md5 in chosen:
            found = repeats(spelled[md5], min_len=args.min_len, max_len=8, keep=shared)
            # Ranked by how widely the corpus uses a pattern, then by how much of this map
            # it would explain. A figure seen on forty maps is a better name for these
            # objects than a longer one seen on three.
            order = sorted(found, key=lambda p: (-shared[p], -len(p) * len(found[p])))
            taken = [None] * len(loaded[md5])
            for pattern in order:
                hits = []
                for at in sorted(found[pattern]):
                    if any(taken[j] is not None for j in range(at, at + len(pattern))):
                        continue
                    hits.append(at)
                if not hits:
                    continue
                for at in hits:
                    for j in range(at, at + len(pattern)):
                        taken[j] = pattern
                spread[pattern] += len(hits)
            claimed += sum(1 for t in taken if t is not None)
            tiled += sum(1 for t in taken if t is not None and len(set(t)) > 1)
            objects += total[md5]

        print(f"objects covered by a shared pattern: {claimed / objects:.0%}  "
              f"(not one symbol repeated: {tiled / objects:.0%})")
        print(f"distinct patterns actually used as tiles: {len(spread):,}\n")
        print(f"{'maps':>6}{'instances':>11}  pattern")
        for pattern, n in sorted(spread.items(),
                                 key=lambda kv: -(shared[kv[0]] * kv[1]))[:10]:
            print(f"{shared[pattern]:>6}{n:>11}  " + "  ".join(pattern[:4])
                  + (" ..." if len(pattern) > 4 else ""))
        return 0

    print(f"min pattern length {args.min_len}, chirality folded, patterns confined to "
          f"rhythm groups.\nSnap and spacing are always in the symbol; the rows vary how "
          f"finely the turn is spelled.\n'shape' is coverage by tiles that are not one "
          f"symbol repeated -- the number that matters,\nsince a stream tiles itself at "
          f"any resolution. 'grp' is the share of objects inside a group.\n")
    header = (f"{'map':<30}{'objs':>6}{'grp':>5}  {'turn spelled as':<16}{'kind':>5}"
              f"{'syms':>6}{'pats':>6}{'cover':>7}{'shape':>7}{'med':>5}")
    print(header)

    for md5 in chosen:
        objects = loaded[md5]
        objs = total[md5]
        meta = index.get(md5, {})
        name = f"{str(meta.get('Title','?'))[:20]} [{str(meta.get('Difficulty','?'))[:6]}]"
        first = True
        best = None
        for label, edges in ANGLE_BANDS.items():
            for with_kind in (False, True):
                segs = segments(objects, edges, with_kind)
                found = repeats(segs, min_len=args.min_len)
                claimed, kept = tile(found, len(objects), args.min_len)
                cover = sum(1 for c in claimed if c is not None) / max(objs, 1)
                shape = sum(1 for c in claimed if c is not None
                            and len(set(c)) > 1) / max(objs, 1)
                alpha = len({s for _, syms in segs for s in syms})
                lens = sorted(len(p) for p, _ in kept)
                med = lens[len(lens)//2] if lens else 0
                grouped = f"{len(objects)/max(objs,1):.0%}" if first else ""
                print(f"{name if first else '':<30}{objs if first else '':>6}{grouped:>5}  "
                      f"{label:<16}{('yes' if with_kind else 'no'):>5}{alpha:>6}"
                      f"{len(kept):>6}{cover:>6.0%}{shape:>7.0%}{med:>5}")
                first = False
                if label == args.show and with_kind and args.examples:
                    best = kept
        if best and args.examples:
            shaped = [(p, locs) for p, locs in best if len(set(p)) > 1]
            shaped.sort(key=lambda kv: -len(kv[1]) * len(kv[0]))
            for p, locs in shaped[:args.examples]:
                print(f"{'':<37}{args.show + ' shapes: ':<16}"
                      f"x{len(locs)}  " + "  ".join(p) if len(p) <= 5 else
                      f"{'':<37}{args.show + ' shapes: ':<16}x{len(locs)}  "
                      + "  ".join(p[:5]) + " ...")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
