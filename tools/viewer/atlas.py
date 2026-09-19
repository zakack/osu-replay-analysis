"""Build a cell atlas: one slide per cell on a beatmap, every instance superimposed.

    python3 tools/viewer/atlas.py --beatmap a5492de7a342 --out build/viewer/atlas.html

The pattern card shows one cell you already know the name of. This shows *every* cell a
map lands in, so the question "what is a cell, actually" has a picture rather than a row
of a CSV.

Each instance is drawn in the cell's own frame: the turn at the origin, the approach
rotated onto +x, distances in circle radii. Because the features are rotation and scale
invariant by construction, instances from anywhere on the playfield -- and from maps at
any circle size -- superimpose exactly.

The frame needs no beatmap and no replay. For the row keyed at object n, Geometry.cs
measures the turn whose apex is object n-1, from object n-2 to object n, so the three
numbers that place the picture are already columns of the geometry CSV:

    entry = (S[n-1], 0)     apex = (0, 0)     exit = (S[n]*cos A[n], S[n]*sin A[n])

with S = spacingRadii and A = signedAngle. That is not an approximation of the geometry
the binning used; it *is* the geometry the binning used, so the picture and the findings
table cannot disagree about where a cell's boundary falls or what is inside it. The one
place the two numbers come from different origins is a slider apex -- see SLIDER_NOTE.
"""
from __future__ import annotations

import argparse
import collections
import csv
import itertools
import json
import math
import os
import statistics
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bins import cells, load  # noqa: E402
from bins.schema import DEG, MIN_N_PLAYER, MIN_N_REFERENCE, SCHEMES, SCHEMA_VERSION  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlas.html")
INDEX = os.path.join(ROOT, "build", "beatmap-index.json")
FINDINGS = os.path.join(ROOT, "build", "findings.csv")

# lazer clamps DifficultyHitObject delta times to this floor, so a row carrying exactly
# this value has lost the real gap and cannot be used to prove two rows are adjacent.
DELTA_FLOOR = 25.0

# How far the measured gap may sit from deltaTime * rate before the pair is rejected.
# Object times are whole milliseconds and the rate is inferred, so a little slack is
# needed; more than this and the two rows are not neighbours.
GAP_TOLERANCE = 0.6

SLIDER_NOTE = ("the exit radius is measured from where the map assumes you leave the "
               "slider, the exit direction from its head. The cell is defined that way; "
               "this draws the cell.")


@dataclass(frozen=True, slots=True)
class Link:
    """One turn: an observation, plus the length of the leg that arrived at its apex."""

    obs: object
    entry: float

    @property
    def frame(self) -> tuple[float, float, float]:
        """(entry radius, signed angle in radians, exit radius). The whole transform."""
        return (self.entry, self.obs.angle / DEG, self.obs.spacing)


def rate(rows: list) -> float:
    """The clock rate this replay was played at, inferred from the rows themselves.

    build/corpus.json carries no mod list, and a rate-changing mod moves both the velocity
    and the snap axis, so the rate has to come from somewhere. deltaTime is the gap divided
    by the clock rate, so their ratio is the rate -- except where deltaTime hit its floor
    and no longer records the gap at all.
    """
    ratios = [
        (b.start_time - a.start_time) / b.delta_time
        for a, b in itertools.pairwise(rows)
        if b.delta_time is not None and b.delta_time > DELTA_FLOOR + 1
        and b.start_time > a.start_time
    ]
    return statistics.median(ratios) if ratios else 1.0


def chain(rows: list, drops: collections.Counter) -> list[Link]:
    """Pair each row with the one before it, rejecting pairs that are not neighbours.

    observations() drops rows the shim never judged, and the shim emits no row at all for
    a spinner, so consecutive rows in the stream are not necessarily consecutive objects
    on the map. Splicing across such a hole would invent a turn that nobody played. The
    gap check catches it: deltaTime is the real gap to the real previous object, so a pair
    whose start times disagree with it is not a pair.
    """
    r = rate(rows)
    out = []
    for previous, obs in itertools.pairwise(rows):
        if obs.angle is None or obs.spacing is None:
            drops["noAngleOrSpacing"] += 1
            continue
        if previous.spacing is None:
            drops["noEntryLeg"] += 1
            continue
        # A leg of length zero has no direction, so the turn built on it has no angle.
        # Two things produce one: lazer returns early from setDistances when a spinner
        # came before, leaving LazyJumpDistance at zero, and a perfectly stacked pair puts
        # two objects at the same point. Either way the zero is a missing measurement
        # wearing the costume of a very tight jump. It bins into 0:1 -- the first row of
        # the findings table -- and, worse, carries whatever bearing the subpixel
        # difference happened to produce into an angle cell chosen at random.
        if previous.spacing == 0.0:
            drops["zeroEntryLeg"] += 1
            continue
        if obs.spacing == 0.0:
            drops["zeroExitLeg"] += 1
            continue
        if obs.delta_time is None:
            drops["noDeltaTime"] += 1
            continue
        if obs.delta_time > DELTA_FLOOR:
            gap = obs.start_time - previous.start_time
            if abs(gap - obs.delta_time * r) > GAP_TOLERANCE:
                drops["notAdjacent"] += 1
                continue
        out.append(Link(obs, previous.spacing))
    return out


def links(source, drops: collections.Counter, *, maps=None, players=None,
          note=None) -> list[Link]:
    """Every valid turn in a source, chained within each replay."""
    out: list[Link] = []
    seen = 0
    stream = load.observations(source, maps=maps, players=players)
    for replay, rows in itertools.groupby(stream, key=lambda o: o.replay):
        rows = list(rows)
        seen += 1
        if note and seen % 100 == 0:
            note(f"  chained {seen} replays, {len(out)} turns")
        if len(rows) < 2:
            drops["shortReplay"] += 1
            continue
        out.extend(chain(rows, drops))
    drops["replays"] = seen
    return out


# ---- the corpus table ------------------------------------------------------------

def corpus_table(all_links: list[Link], drops: collections.Counter) -> dict:
    """One canonical turn per (beatmap, object), voted across every replay of that map.

    Cell geometry is a property of the map, so every play of one object should produce the
    same triple. Where they do not, a geometry-changing mod is in the corpus: HR mirrors
    the playfield, which flips the sign of the angle and lands the object in the mirror
    image of its cell. Keep the majority reading and count the rest, because a silent
    minority of mirrored turns would show up as a handedness finding.
    """
    votes: dict = collections.defaultdict(dict)
    for link in all_links:
        key = (link.obs.beatmap_md5, round(link.obs.start_time, 3))
        triple = (round(link.entry, 4), round(link.obs.angle, 4), round(link.obs.spacing, 4))
        slot = votes[key].setdefault(triple, [0, link])
        slot[0] += 1

    table = {}
    for key, options in votes.items():
        if len(options) > 1:
            drops["modDisagree"] += 1
        table[key] = max(options.values(), key=lambda s: s[0])[1]
    return table


def occupancy(table: dict, scheme: str) -> dict:
    """(target, fromSlider, cell) -> the keys of the turns in it."""
    out: dict = collections.defaultdict(list)
    for key, link in table.items():
        cell = cells.key(link.obs, scheme)
        if cell is None:
            continue
        out[(link.obs.target, link.obs.from_slider, cell)].append(key)
    return out


def census(counts: list[int], threshold: int) -> dict:
    """The shape of the occupancy distribution, which is the first thing the deck shows."""
    total = sum(counts)
    kept = [n for n in counts if n >= threshold]
    return {
        "cells": len(counts),
        "singletons": sum(1 for n in counts if n == 1),
        "pairs": sum(1 for n in counts if n == 2),
        "median": statistics.median(counts) if counts else 0,
        "mean": round(statistics.fmean(counts), 2) if counts else 0,
        "max": max(counts) if counts else 0,
        "slides": len(kept),
        "instances": total,
        "covered": sum(kept),
        "histogram": sorted(collections.Counter(
            min(n, 64) for n in counts).items()),
    }


# ---- the corpus overlay ----------------------------------------------------------

# The overlay grid. Resolution is a property of the cell rather than a constant: a bin
# thirty degrees wide wants one-degree buckets, and a scheme that bounds no angle at all
# spans the whole circle and would pay three hundred and sixty of them for a picture no
# sharper. Cap the grid instead and let the widths follow, with floors so a tight cell
# never gets a grid finer than the thing being drawn.
ENTRY_BUCKET = 0.05     # radii, the 1-D entry rug; cheap enough to leave fixed
EXIT_A_FLOOR = 1.0      # degrees
EXIT_R_FLOOR = 0.05     # radii
EXIT_GRID = 48          # buckets per side, at most


def exit_widths(a_span: float, r_span: float) -> tuple[float, float]:
    return (max(EXIT_A_FLOOR, a_span / EXIT_GRID),
            max(EXIT_R_FLOOR, r_span / EXIT_GRID))


def density(table: dict, keys: list, origin: tuple[float, float],
            widths: tuple[float, float]) -> dict:
    """The overlay, as a histogram rather than a sample.

    A sample would need a rule for which instances survive and would drop the rest; a
    histogram keeps every one of them, is smaller than a few hundred sampled instances
    once a cell is busy, and is exactly reproducible because there is no choice in it.
    """
    entry: collections.Counter = collections.Counter()
    exit_: collections.Counter = collections.Counter()
    a0, r0 = origin
    aw, rw = widths
    for key in keys:
        link = table[key]
        entry[int(link.entry / ENTRY_BUCKET)] += 1
        exit_[(int((link.obs.angle - a0) / aw),
               int((link.obs.spacing - r0) / rw))] += 1
    return {
        "n": len(keys),
        "origin": [round(a0, 4), round(r0, 4)],
        "widths": [round(aw, 4), round(rw, 4)],
        "entry": [v for b, c in sorted(entry.items()) for v in (b, c)],
        "exit": [v for (a, r), c in sorted(exit_.items()) for v in (a, r, c)],
    }


# ---- the findings table ----------------------------------------------------------

def findings_index(path: str) -> dict:
    """(scheme, cell, target, fromSlider) -> {metric: row}, read in one pass.

    The pattern card rescans the whole file per cell, which is free for one cell and is
    not for three hundred. Filters match it exactly so the two pages quote the same rows.
    """
    out: dict = collections.defaultdict(dict)
    if not os.path.exists(path):
        return out
    with open(path) as handle:
        for row in csv.DictReader(handle):
            if row["stratum"] != "all" or row["detrended"] != "0":
                continue
            key = (row["scheme"], row["cell"], row["target"], row["fromSlider"])
            out[key][row["metric"]] = {
                k: row[k] for k in
                ("playerN", "playerValue", "referenceN", "referenceValue",
                 "effect", "effectKind", "z", "controlEffect", "relativeEffect")
            }
    return out


# ---- extent ----------------------------------------------------------------------

# Quantised so that a per-slide fit can never land on a value between two slides and look
# like a difference in the data. Roughly x1.4 apart, which is far enough that a change of
# rung is visible in the ring spacing rather than something you have to read off a label.
EXT_LADDER = (2.5, 3.5, 5.0, 7.0, 10.0, 14.0, 20.0)


def pick_extent(need: float) -> float:
    for rung in EXT_LADDER:
        if rung >= need:
            return rung
    return EXT_LADDER[-1]


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def geometry_of(scheme: str) -> dict:
    """Which axis of a scheme constrains the angle, and which the radius.

    Only spacing pins a radius. Velocity is spacing over time and leaves the radius free,
    so its cell is an unbounded pie slice rather than an annulus; snap and run position
    constrain no geometry at all and have no boundary to draw. Saying so is better than
    drawing a box that is not the cell.
    """
    names = cells.describe(scheme)
    return {
        "angle": names.index("angle") if "angle" in names else None,
        "radius": names.index("spacing") if "spacing" in names else None,
        "note": ("velocity bounds the radius over time, not the radius"
                 if "velocity" in names else
                 None if "spacing" in names else
                 "this scheme constrains no geometry, so the cell has no boundary to draw"),
    }


# ---- the deck --------------------------------------------------------------------

def build_scheme(scheme: str, focus: dict, corpus: dict, slot: dict,
                 threshold: int, findings: dict) -> dict:
    """Every slide for one scheme: the cells above the threshold, then the residue."""
    focus_cells = occupancy(focus, scheme)
    corpus_cells = occupancy(corpus, scheme)
    geom = geometry_of(scheme)

    slides, sparse_keys, sparse_cells = [], [], []
    for facet_cell, keys in sorted(focus_cells.items(),
                                   key=lambda kv: (-len(kv[1]), kv[0])):
        target, from_slider, cell = facet_cell
        if len(keys) < threshold:
            sparse_keys.extend(keys)
            sparse_cells.append({"labels": list(cell), "n": len(keys),
                                 "bounds": _bounds(scheme, cell)})
            continue

        others = [k for k in corpus_cells.get(facet_cell, []) if k not in focus]
        entries = [focus[k].entry for k in keys]
        exits = [focus[k].obs.spacing for k in keys]
        bounds = _bounds(scheme, cell)

        clip = None
        outer = 0.0
        if geom["radius"] is not None:
            lo, hi = bounds[geom["radius"]]
            if math.isinf(hi):
                pool = exits + [corpus[k].obs.spacing for k in others]
                clip = math.ceil(max(pool + [lo]) * 2) / 2
                outer = clip
            else:
                outer = hi

        # What the slide is about is the cell, so the cell's own outer edge and the exits
        # inside it set the frame. The entry radius is unconstrained and its tail runs a
        # long way -- a stacked stream is approached from right across the playfield --
        # and sizing the frame to hold all of it would shrink the cell to a dot. Take a
        # typical approach rather than the furthest one and let the rest clip, which the
        # rug shows by piling up against the edge.
        need = max([outer, quantile(exits, 0.98), quantile(entries, 0.60), 1.6]) * 1.12
        ext = pick_extent(need)

        # The overlay grid spans the cell where the cell is bounded and the drawn frame
        # where it is not, so its resolution tracks what the slide can actually show.
        if geom["angle"] is not None:
            a0, a1 = bounds[geom["angle"]]
        else:
            a0, a1 = -180.0, 180.0
        if geom["radius"] is not None:
            r0 = bounds[geom["radius"]][0]
            r1 = clip if clip is not None else bounds[geom["radius"]][1]
        else:
            r0, r1 = 0.0, ext

        slides.append({
            "labels": list(cell),
            "cell": cells.label(cell),
            "target": target,
            "fromSlider": from_slider,
            "idx": sorted(slot[k] for k in keys),
            "bounds": bounds,
            "clip": clip,
            "ext": ext,
            "corpusN": len(corpus_cells.get(facet_cell, [])),
            "overlay": density(corpus, others, (a0, r0),
                               exit_widths(a1 - a0, r1 - r0)),
            "finding": findings.get((scheme, cells.label(cell), target, from_slider), {}),
        })

    residue = None
    if sparse_keys:
        residue = {
            "idx": sorted(slot[k] for k in sparse_keys),
            "cells": sorted(sparse_cells, key=lambda c: (-c["n"], c["labels"])),
            "ext": pick_extent(max(
                quantile([focus[k].obs.spacing for k in sparse_keys], 0.98),
                quantile([focus[k].entry for k in sparse_keys], 0.60), 1.6) * 1.12),
        }

    unbinned = sorted(slot[k] for k, link in focus.items()
                      if cells.key(link.obs, scheme) is None)

    return {
        "axes": list(cells.describe(scheme)),
        "geometry": geom,
        "slides": slides,
        "residue": residue,
        "unbinned": unbinned,
        "census": census([len(v) for v in focus_cells.values()], threshold),
    }


def _bounds(scheme: str, cell: tuple) -> list:
    return [None if b is None else [b[0], None if math.isinf(b[1]) else b[1]]
            for b in cells.bounds(scheme, cell)]


# ---- assembly --------------------------------------------------------------------

def beatmap_meta(md5: str) -> dict:
    with open(INDEX) as handle:
        entry = json.load(handle).get(md5, {})
    return {k.lower(): entry.get(k, "?") for k in ("Artist", "Title", "Difficulty")}


def write(payload: dict, out: str) -> int:
    with open(TEMPLATE) as handle:
        html = handle.read()
    if "/*__PAYLOAD__*/" not in html:
        raise SystemExit(f"{TEMPLATE} has no /*__PAYLOAD__*/ slot; the page would be dead")
    blob = json.dumps(payload, separators=(",", ":")).replace("</", r"<\/")
    html = html.replace("/*__PAYLOAD__*/", blob, 1)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as handle:
        handle.write(html)
    return len(html)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--beatmap", required=True, help="beatmap md5, or a unique prefix")
    parser.add_argument("--min-n", type=int, default=3,
                        help="cells with fewer instances collapse into the residue slide")
    parser.add_argument("--player", default="zaksynack")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    started = time.time()
    note = lambda m: print(m, flush=True)

    drops: collections.Counter = collections.Counter()
    note("reading the player corpus (this walks geometry.csv and rhythm.csv) ...")
    everything = links(load.PLAYER, drops, players={args.player}, note=note)
    note(f"  {drops['replays']} replays, {len(everything)} turns, "
         f"{time.time() - started:.0f}s")

    table = corpus_table(everything, drops)
    note(f"  {len(table)} distinct (beatmap, object) turns after voting")

    md5s = {link.obs.beatmap_md5 for link in table.values()}
    matches = sorted(m for m in md5s if m.startswith(args.beatmap))
    if len(matches) != 1:
        raise SystemExit(f"--beatmap {args.beatmap!r} matched {len(matches)} maps in the "
                         f"corpus; give more of the md5")
    md5 = matches[0]

    focus = {k: v for k, v in table.items() if k[0] == md5}
    if not focus:
        raise SystemExit(f"no turns on {md5}")

    ordered = sorted(focus.items(), key=lambda kv: kv[0][1])
    slot = {k: i for i, (k, _) in enumerate(ordered)}
    instances = {
        "t": [int(link.obs.start_time) for _, link in ordered],
        "e": [round(link.entry * 100) for _, link in ordered],
        "a": [round(link.obs.angle * 10) for _, link in ordered],
        "x": [round(link.obs.spacing * 100) for _, link in ordered],
        "v": [round((link.obs.velocity or 0) * 10000) for _, link in ordered],
    }

    findings = findings_index(FINDINGS)
    note(f"  {len(findings)} cells carry a corpus finding")

    schemes = {}
    for scheme in SCHEMES:
        schemes[scheme] = build_scheme(scheme, focus, table, slot,
                                       args.min_n, findings)
        c = schemes[scheme]["census"]
        note(f"  {scheme:<20} {c['cells']:>4} cells  {c['slides']:>4} slides  "
             f"{c['singletons']:>4} singletons  "
             f"{100 * c['covered'] / max(1, c['instances']):.0f}% covered")

    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "beatmap": beatmap_meta(md5) | {"md5": md5},
        "player": args.player,
        "threshold": args.min_n,
        "instances": instances,
        "schemes": schemes,
        "extLadder": list(EXT_LADDER),
        "buckets": {"entry": ENTRY_BUCKET},
        "gates": {"player": MIN_N_PLAYER, "reference": MIN_N_REFERENCE},
        "sliderNote": SLIDER_NOTE,
        "corpus": {"replays": drops["replays"], "objects": len(table),
                   "maps": len(md5s), "turns": len(everything)},
        "dropped": {k: v for k, v in sorted(drops.items()) if k != "replays"},
    }

    size = write(payload, args.out)
    note(f"\n{len(ordered)} instances on {payload['beatmap']['title']} "
         f"[{payload['beatmap']['difficulty']}]")
    note(f"dropped: {payload['dropped']}")
    note(f"written: {args.out}  ({size / 1024 / 1024:.2f} MB)  "
         f"in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
