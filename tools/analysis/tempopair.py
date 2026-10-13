"""Does the two hands' coupling survive a rate mod? The same 40 beatmaps, nomod and sped up.

The open question from the coupling work: `arrival.py --coupling` found the two hands
correlated in both corpora and the *better* players less so, flat across tempo bands. But
every band there was a different set of beatmaps played by a different set of players, so
"tempo" and "who plays maps at that tempo" were the same axis. This file removes that.

`tools/reference/fetch.py --mods DT,HD` pulled the rate-mod boards of 40 beatmaps that the
nomod reference corpus already covers. Same 40 maps both sides, so the geometry is identical
object for object, and only the real-time tempo differs.

Glossary, one word per concept, as `streamfail.py` does:

    button error  ms, pressTime - startTime. Signed, negative is the button early.
    cursor error  ms, closestTime - startTime: when the cursor was deepest inside the circle.
                  Never "arrival" -- that is `enterTime`, a different column.
    rho           correlation between the two errors within a group. 0 is two hands each
                  independently locked to the music; 1 is one movement carrying both.
    condition     "nomod" or "rate" -- the same maps at 1.0x and under DT/NC.
    floor         an accuracy floor applied to both sides, to match play quality.

**Why no rate correction appears anywhere here.** Replay frame times are beatmap time, not
real time: lazer's rate mods drive the track, and `Rhythm.cs` reads `BeatLength` straight off
the beatmap's control points, so a 1/4 snap on a 190 BPM map is 79 ms in both corpora. The
beatmap-time demand is identical and the real-time demand is the mod's rate times harder.
rho is scale-free on top of that, so even a freely-set rate multiplier -- which the charter
warns never to assume is 1.5, and which the manifest does not record -- cannot move it. What
a missing rate *would* corrupt is a BPM label or any sigma in ms, so neither is a result
here; the sigma columns are printed for scale only and are not comparable across conditions.

**Two confounds, controlled rather than argued away.**

- *Population.* The rate boards are not the same players as the nomod boards: 818 against
  3,568, overlapping in 133 -- and in only 65 once the nomod side is cut to the shared 40
  maps, which is the number the paired table reports. Pairing within player costs most of
  the sample and answers the question the pooled table cannot.
- *Play quality.* This is the sharp one, and it runs the same way as the hypothesis. The
  nomod side is almost all S and X, median accuracy 0.998; the rate side runs down to D,
  median 0.962, because a nomod top-50 board is near-perfect plays and a DT board is
  whoever cleared it. Comparing those pooled would read a difference in sloppiness as a
  difference in tempo. `--floor` applies an accuracy floor to both sides and the sweep
  table moves it, because one floor is a coincidence and a trend across floors is not --
  the same discipline the pattern work needed when a single training floor inverted an
  answer.

Measured 2026-10-13 on 1,045 nomod and 1,051 rate-mod replays of the same 40 beatmaps,
188-204 BPM nomod and so 282-306 under the mod. rho centred within replay, with the
within-section and pooled columns run as controls.

**Tempo does not uncouple the hands.** Accuracy >= 0.98 on both sides, 1,042 against 310
replays:

    class              nomod rho   rate rho    gap
    1/4, 0-2 radii          0.08       0.09   0.02
    1/4, 2+ radii           0.11       0.14   0.03
    1/2, 0-2 radii          0.06       0.08   0.02
    1/2, 2+ radii           0.22       0.24   0.02
    every circle            0.13       0.13   0.00

The gap on every circle is 0.00 at floors 0.95, 0.97 and 0.98 and 0.01 at 0.99, and
-0.00 at every floor centred within section. Within player it is +0.02 over the 65
players present in both corpora, with the rate side *lower* in 43% of them -- a coin
flip, not a direction. Half the shape of the earlier coupling result survives untouched:
coupling still lives in the 2+ radii 1/2 jump (0.22) and not in the 1/4 stream (0.08),
in both conditions.

**What does move rho is the play coming apart**, inside the rate corpus alone, where the
maps, the mod and the tempo are all fixed and only the quality of the play varies:

    accuracy     replays   pooled   within replay   within section
    under .93        314     0.27            0.23             0.21
    .93-.96          197     0.17            0.15             0.13
    .96-.98          230     0.15            0.13             0.11
    .98+             310     0.15            0.13             0.11

One step, at the bottom, and flat above it: rho is the same from 0.96 accuracy upward and
rises by half again when the play is actually failing. The gradient survives all three
centring levels, so it is not a section's worth of common drift.

**And it is a state, not a skill proxy.** A sloppier play is also a weaker player, so that
table alone cannot tell "this person couples more" from "this play came apart". The 25
players holding both a sub-.93 and a .96+ play can, because each is compared to themselves:

    class              clean   failing     gap   players higher when failing
    1/4, 0-2 radii      0.05      0.22    0.17                          87%
    1/4, 2+ radii       0.10      0.18    0.08                          61%
    1/2, 0-2 radii      0.09      0.08   -0.01                          45%
    1/2, 2+ radii       0.23      0.30    0.07                          64%
    every circle        0.13      0.23    0.10                          88%

Same person, same mod, same tempo, same 40 maps, and the gap is +0.10 overall in 88% of
them individually -- so it is not a mean dragged by one outlier. It concentrates in the
**1/4 stream**, +0.17, where coupling is otherwise lowest, and barely touches the 1/2 jump
at section level. When a stream comes apart, the hands fall back into sync.

The tables reconcile. Those 65 paired players fall from 0.9973 accuracy nomod to 0.9737
under the mod, 59 of 65 of them, and the gradient prices that drop at roughly nothing --
which is the +0.02 the paired table reports.

**So uncoupling is not an adaptation to tempo.** The same players, on the same maps, at
1.5x the real tempo, are exactly as coupled. The earlier reading -- that elite uncoupling
is flat across tempo rather than a high-tempo adaptation -- now holds with the geometry
held fixed object for object instead of varying with the band.

Two findings that **stack rather than replace each other**, and conflating them would be
the easy mistake. Trait coupling is real and independent: zaksynack sits at 0.33-0.38 on
jumps within section against this corpus's 0.20-0.24, on replays whose accuracy is not
failing at all -- higher than a *failing* reference play's 0.26. State coupling is the
+0.10 above. A high trait figure is not a diagnosis of a collapsing play.

What this cannot say, in both directions. These are not mrekk-tier players: the 40 boards
are whoever cleared the map, and no floor turns a 282 BPM clear into a 282 BPM elite play.
Less obviously, it is also a *floor* caveat -- every one of the 65 paired players sits at
rho 0.1-0.25, so "tempo does not uncouple" is established for players who were never
strongly coupled to begin with. Whether a 0.4-coupled player keeps that at 282 BPM is
untested here, and it is the version of the question nearest to the one that prompted it.
Splitting the 65 by nomod-rho tercile would not settle it either, because regression to the
mean drops the top tercile whichever way the effect runs.
"""
import argparse
import collections
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (geometry, clicks, rhythm, manifest, manifest filter) per condition.
CONDITIONS = {
    "nomod": ("build/geometry-reference-nm.csv", "build/clicks-reference-nm.csv",
              "build/rhythm-reference-nm.csv", "build/reference/manifest.json", "NM"),
    "rate": ("build/geometry-reference-dt.csv", "build/clicks-reference-dt.csv",
             "build/rhythm-reference-dt.csv", "build/reference-dt/manifest.json", None),
}
# Snap and spacing, named the way the schema names them. A class is only a class if both
# conditions populate it, which `report` enforces rather than assumes.
SNAPS = ((0.25, "1/4"), (0.5, "1/2"), (1.0, "1/1"))
SPACINGS = ((0.0, 2.0, "0-2 radii"), (2.0, 1e9, "2+ radii"))
ALL = "every circle"
WINDOW = 10_000.0
LEAST = 5


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def replays_of(manifest_path, keep_filter, maps, floor):
    """Which replay files of a corpus to read: on the shared maps, above the accuracy floor.

    Returns file name -> (user_id, beatmap_md5, accuracy). The nomod corpus holds every
    reference pull, so it is filtered to the no-mod subset the way `extend_nm.sh` does:
    manifest filter NM *and* an empty mod list.
    """
    out = {}
    for v in json.load(open(os.path.join(ROOT, manifest_path))).values():
        if keep_filter is not None and (v.get("filter") != keep_filter or v["mods"]):
            continue
        if maps is not None and v["beatmap_md5"] not in maps:
            continue
        if floor is not None and (v["accuracy"] or 0.0) < floor:
            continue
        out[v["file"]] = (v["user_id"], v["beatmap_md5"], v["accuracy"])
    return out


def classify(geometry, rhythm, allowed):
    """Key every circle of the allowed replays to its (snap, spacing) class.

    Two passes, because spacing is in the geometry table and snap is in the rhythm one.
    Keyed to a tenth of a millisecond, which is how `rush.py` and `arrival.py` join these.
    A circle whose snap the rhythm pass did not cover is dropped, so a missing snap cannot
    quietly land in a class.
    """
    spacing = {}
    with open(os.path.join(ROOT, geometry)) as f:
        for r in csv.DictReader(f):
            if r["replay"] not in allowed or r["kind"] != "circle" or r["exitSlackRadii"]:
                continue
            value = _num(r["spacingRadii"])
            if value is None:
                continue
            spacing[(sys.intern(r["replay"]), round(float(r["startTime"]), 1))] = value

    classes = {}
    with open(os.path.join(ROOT, rhythm)) as f:
        for r in csv.DictReader(f):
            if r["tolerance"] != "0.02" or r["clickIndex"] == "-1":
                continue
            key = (r["replay"], round(float(r["time"]), 1))
            value = spacing.get(key)
            if value is None:
                continue
            snap = _num(r["snap"])
            if snap is None:
                continue
            name = next((n for s, n in SNAPS if abs(snap - s) < 1e-6), None)
            if name is None:
                continue
            band = next(n for lo, hi, n in SPACINGS if lo <= value < hi)
            classes[(sys.intern(key[0]), key[1])] = f"{name}, {band}"
    return classes


def accumulate(clicks, classes, allowed):
    """Six sufficient statistics per (class, replay, section), for every centring level.

    Everything the report needs derives from this one accumulator: pooled sums the raw
    moments and centres once, within-replay centres each replay, within-section each section.
    """
    acc = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0.0])
    with open(os.path.join(ROOT, clicks)) as f:
        for r in csv.DictReader(f):
            name = r["replay"]
            if name not in allowed or r["pressInside"] != "1":
                continue
            start = _num(r["startTime"])
            press, deep = _num(r["pressTime"]), _num(r["closestTime"])
            if start is None or press is None or deep is None:
                continue
            got = classes.get((name, round(start, 1)))
            if got is None:
                continue
            x, y = press - start, deep - start
            section = int(start // WINDOW)
            for label in (got, ALL):
                c = acc[(label, name, section)]
                c[0] += 1
                c[1] += x
                c[2] += y
                c[3] += x * x
                c[4] += y * y
                c[5] += x * y
    return acc


def correlate(acc, label, level, pick=None):
    """Pooled within-group rho at one centring level, from each group's own centred moments.

    `level` is how much is removed first. "pooled" centres once over everything and so still
    carries whatever displaces *both* hands together -- a mistimed section, a map's own
    offset, the audio path. Those are common causes, not bilateral coordination, and they
    inflate rho with none involved, which is why the controlled columns are the other two.
    """
    groups = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for (name, replay, section), c in acc.items():
        if name != label or (pick is not None and replay not in pick):
            continue
        key = {"pooled": 0, "within replay": replay,
               "within section": (replay, section)}[level]
        g = groups[key]
        for i in range(6):
            g[i] += c[i]
    sxx = syy = sxy = 0.0
    n = 0
    for g in groups.values():
        if g[0] < LEAST:
            continue
        sxx += g[3] - g[1] * g[1] / g[0]
        syy += g[4] - g[2] * g[2] / g[0]
        sxy += g[5] - g[1] * g[2] / g[0]
        n += g[0]
    if n < LEAST or sxx <= 0 or syy <= 0:
        return None, 0, 0.0, 0.0
    return sxy / (sxx * syy) ** 0.5, n, (sxx / n) ** 0.5, (syy / n) ** 0.5




def load(condition, maps):
    """Read one condition once, unfiltered by accuracy.

    Every table here is a different accuracy floor or a different set of players, and
    `correlate` takes a `pick`, so one pass over the CSVs serves all of them. Re-reading
    two gigabytes per floor is what the first version of this did.
    """
    geometry, clicks, rhythm, manifest, keep = CONDITIONS[condition]
    allowed = replays_of(manifest, keep, maps, None)
    classes = classify(geometry, rhythm, allowed)
    return allowed, accumulate(clicks, classes, allowed)


def shared_maps():
    """The beatmaps the rate corpus covers, which is what both sides are restricted to."""
    _, _, _, manifest, _ = CONDITIONS["rate"]
    return {v["beatmap_md5"] for v in
            json.load(open(os.path.join(ROOT, manifest))).values()}


def labels(accs):
    """Classes populated in every condition, in schema order, with `every circle` last."""
    order = [f"{n}, {b}" for _, n in SNAPS for _, _, b in SPACINGS] + [ALL]
    present = [set(k[0] for k in acc) for acc in accs]
    return [c for c in order if all(c in p for p in present)]


def pick_at(allowed, floor):
    """The replay files of a condition whose play was at least this accurate."""
    return {f for f, (_, _, a) in allowed.items() if (a or 0.0) >= floor}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--floor", type=float, default=0.98,
                        help="accuracy floor for the headline table (default 0.98, which "
                             "matches the rate side's play quality to the nomod side's)")
    parser.add_argument("--level", default="within replay",
                        choices=("pooled", "within replay", "within section"),
                        help="how much common displacement to remove before correlating")
    parser.add_argument("--only", choices=("headline", "sweep", "paired", "accuracy",
                                           "state"),
                        help="print one table instead of all five")
    args = parser.parse_args()
    want = (lambda name: args.only is None or args.only == name)

    # The accuracy and state tables live inside the rate corpus alone, so asking for one of
    # them on its own does not pay for the nomod side.
    maps = shared_maps()
    need = ("rate",) if args.only in ("accuracy", "state") else ("nomod", "rate")
    loaded = {c: load(c, maps) for c in need}
    accs = [loaded[c][1] for c in need]
    classes = labels(accs)
    print(f"the {len(maps)} beatmaps the rate corpus covers"
          + (", both conditions restricted to them" if len(need) > 1 else "")
          + f".\nrho centred {args.level}.\n")
    for c in need:
        allowed = loaded[c][0]
        print(f"  {c:<6} {len(allowed):,} replays, "
              f"{len({u for u, _, _ in allowed.values()}):,} players")
    print()

    if want("headline"):
        picks = [pick_at(loaded[c][0], args.floor) for c in ("nomod", "rate")]
        print(f"-- the same maps at two tempos, accuracy >= {args.floor} both sides "
              f"({len(picks[0]):,} and {len(picks[1]):,} replays)\n")
        print("sigma is beatmap-time ms and is printed for scale only: the conditions "
              "measure it in\ndifferent real-time units, so only rho compares across them.\n")
        print(f"  {'class':<18}" + "".join(f"{h:>11}" for h in
                                           ("nomod n", "sb", "sc", "rho",
                                            "rate n", "sb", "sc", "rho", "gap")))
        for label in classes:
            cells = [correlate(a, label, args.level, p) for a, p in zip(accs, picks)]
            if any(c[0] is None for c in cells):
                continue
            line = f"  {label:<18}"
            for rho, n, sb, sc in cells:
                line += f"{n:>11,}{sb:>11.1f}{sc:>11.1f}{rho:>11.2f}"
            print(line + f"{cells[1][0] - cells[0][0]:>11.2f}")
        print()

    if want("sweep"):
        print("-- the same table as the accuracy floor rises\n")
        print("The rate boards are a much sloppier population than the nomod boards, and "
              "sloppiness\nruns the same way as the hypothesis. A gap that survives the "
              "floor rising is tempo;\none that closes was quality.\n")
        print(f"  {'class':<18}" + "".join(f"{f:>26}" for f in (0.95, 0.97, 0.98, 0.99)))
        print(f"  {'':<18}" + "".join(f"{h:>8}" for _ in range(4)
                                      for h in ("n", "rho", "gap")))
        for label in classes:
            line = f"  {label:<18}"
            for floor in (0.95, 0.97, 0.98, 0.99):
                picks = [pick_at(loaded[c][0], floor) for c in ("nomod", "rate")]
                cells = [correlate(a, label, args.level, p) for a, p in zip(accs, picks)]
                if any(c[0] is None for c in cells):
                    line += f"{'-':>8}{'-':>8}{'-':>8}"
                    continue
                line += (f"{cells[1][1]:>8,}{cells[1][0]:>8.2f}"
                         f"{cells[1][0] - cells[0][0]:>8.2f}")
            print(line)
        print("\n  n and rho are the rate side; gap is rate minus nomod at that floor.\n")

    if want("paired"):
        people = []
        for c in ("nomod", "rate"):
            by = collections.defaultdict(set)
            for name, (user, _, _) in loaded[c][0].items():
                by[user].add(name)
            people.append(by)
        both = sorted(set(people[0]) & set(people[1]))
        print(f"-- within player: the {len(both):,} players present in both corpora\n")
        print("Each player's rho is measured in each condition and the pair differenced, "
              "so nothing\nhere compares one player to another. No accuracy floor, because "
              "the player is the\ncontrol. Costs most of the sample and answers what the "
              "pooled table cannot.\n")
        print(f"  {'class':<18}{'players':>9}{'nomod':>9}{'rate':>9}{'gap':>9}"
              f"{'rate lower':>12}")
        for label in classes:
            gaps = []
            for user in both:
                pair = [correlate(a, label, args.level, by[user])
                        for a, by in zip(accs, people)]
                if any(p[0] is None for p in pair):
                    continue
                gaps.append((pair[0][0], pair[1][0]))
            if len(gaps) < 10:
                continue
            nomod = sum(a for a, _ in gaps) / len(gaps)
            rate = sum(b for _, b in gaps) / len(gaps)
            lower = sum(1 for a, b in gaps if b < a) / len(gaps)
            print(f"  {label:<18}{len(gaps):>9,}{nomod:>9.2f}{rate:>9.2f}"
                  f"{rate - nomod:>9.2f}{lower:>11.0%}")
        print()

    if want("accuracy"):
        allowed, acc = loaded["rate"]
        bands = ((0.0, 0.93, "under .93"), (0.93, 0.96, ".93-.96"),
                 (0.96, 0.98, ".96-.98"), (0.98, 1.01, ".98+"))
        print("-- inside the rate corpus alone: rho against how clean the play was\n")
        print("The calibration for the sweep. If rho tracks accuracy here, a gap measured "
              "at one\nfloor is partly quality rather than tempo.\n")
        print(f"  {'accuracy':<12}{'replays':>9}"
              + "".join(f"{c:>18}" for c in classes))
        for lo, hi, name in bands:
            pick = {f for f, (_, _, a) in allowed.items() if lo <= (a or 0) < hi}
            if not pick:
                continue
            line = f"  {name:<12}{len(pick):>9,}"
            for label in classes:
                rho, n, _, _ = correlate(acc, label, args.level, pick)
                line += f"{rho:>18.2f}" if rho is not None else f"{'-':>18}"
            print(line)
        print()

    if want("state"):
        allowed, acc = loaded["rate"]
        by = collections.defaultdict(list)
        for f, (user, _, a) in allowed.items():
            by[user].append((a or 0.0, f))
        pairs = [(lo, hi) for rows in by.values()
                 for lo, hi in [({f for a, f in rows if a < 0.93},
                                 {f for a, f in rows if a >= 0.96})] if lo and hi]
        print(f"-- state or trait: the {len(pairs)} players with both a failing and a "
              f"clean play\n")
        print("The accuracy gradient above cannot tell a player who couples more from a "
              "play that\ncame apart, because a sloppier play is also a weaker player. "
              "This can: every row\ncompares one person's own failing plays to their own "
              "clean ones, at one tempo, under\none mod, on the same 40 maps.\n")
        print(f"  {'class':<18}{'players':>9}{'clean':>8}{'failing':>9}{'gap':>8}"
              f"{'worse higher':>14}")
        for label in classes:
            got = []
            for lo, hi in pairs:
                clean = correlate(acc, label, args.level, hi)[0]
                fail = correlate(acc, label, args.level, lo)[0]
                if clean is None or fail is None:
                    continue
                got.append((clean, fail))
            if len(got) < 8:
                continue
            clean = sum(a for a, _ in got) / len(got)
            fail = sum(b for _, b in got) / len(got)
            share = sum(1 for a, b in got if b > a) / len(got)
            print(f"  {label:<18}{len(got):>9}{clean:>8.2f}{fail:>9.2f}"
                  f"{fail - clean:>8.2f}{share:>13.0%}")
        print("\n  `worse higher` is the share of players whose rho is higher in their "
              "failing plays,\n  which is what says the mean is not one outlier.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
