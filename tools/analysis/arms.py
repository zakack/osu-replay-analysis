"""Deep latch arms that were not meant, counted where the map leaves no doubt.

The player is trying to arm the latch less by accident. Most arms are rides, and from the trace
alone a ride and a misfire look the same: both keys on the backplate in one report. The map
tells them apart. A ride needs something to ride: a pair of objects on a third of a beat or
finer, counting a slider's tail to the next head, since riding out of a slider starts with the
slider's key already planted. So each arm during a traced replay is put in one cell by the
objects around it, judged against the beatmap, never against the player:

  straight four the only fine gaps near the arm belong to runs of exactly four taps on one
                snap of 1/3 beat or finer, three
                circles and a circle or slider head, spaced LINE radii or more (not a stack),
                whose two middle circles bend STRAIGHT degrees or more (180 = a straight line). The player taps these and
                never rides them (tried as a ridden triple plus a tap; it does not work for
                them), so an arm there is a misfire however fine the snap. Example:
                NO GIRL NO CRY [DON'T CRY!] objects 595-622, six of them on 1/3.
                Exception: the Xevel [Extreme] 452-455 line, which the player rides on
                purpose as a triple plus a tap, so its arms land here but are meant.
  doublet       the same for runs of exactly two circles on a fine snap: the player taps
                these (or tries to), so a plant on one is a misfire. Example: the goreshit
                o'er the flood kiai (objects 828-1074), where triples and longer are ridden
                every time and the 1/4 doublets are planted in about half the runs.
  rideable     a gap of <= 1/3 beat (+TOL) among the objects from the one before the arm to
                RIDE_AHEAD objects after it. Meant, or at least plausibly meant.
  spinner       inside a spinner (+-SPIN_PAD). Holding both keys through a spinner is normal.
  slider hold   inside a slider's span with nothing rideable nearby: the held key is already
                on the backplate, so one press of the other arms it.
  unintended    everything else: during play, outside spinners and slider holds, with every
                gap around it 1/2 beat or wider. Split by the smallest jump into the next
                objects: at JUMP radii or more the player resets into tapping by their own
                account, so "unintended, jump" is a confident cell, with "straight four";
                "unintended, close" may hold deliberate rides of close 1/2 patterns.
  break         more than GAP ms from any object (breaks, after the last object), or before
                the first object: keys held through the lead-in.

An arm is placed at its plant, the report where both keys reached the backplate, not where
the latch armed: with latch_debounce_ms the arm waits for the next report still on the
backplate, which on a silent keyboard is the first lift, ~70 ms later at any debounce, and
judged there it slips past the objects it was planted for. The plant comes from the floor
crossings at the trace's recorded backplate; an arm with no plant there keeps its own time.

The confident cell's rate is per 1000 jump objects (JUMP+ radii, nothing on a fine snap around
them), because how many arms a session can misfire depends on how jump-heavy its maps were.
By day and by the thresholds in each trace header. Arms come from the stored events (the replay of each trace under its recorded
thresholds, which is what the live daemon did).

Every arm is also written to build/arms.jsonl: replay, cell, map ms, and the index of the
object it leads into (the viewer's numbering), for marking on a replay.

    python3 tools/analysis/arms.py [--out build/arms.txt]
"""
from __future__ import annotations

import bisect
import collections
import csv
import glob
import io
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from attribution_riding import sessions                       # noqa: E402
from riding import thresholds_by_replay                       # noqa: E402
from sliderled import LENIENCY, TOL, timing                   # noqa: E402

RIDE_AHEAD = 3       # objects after the arm that may hold the rideable gap
SPIN_PAD = 100.0     # ms either side of a spinner
GAP = 2000.0         # ms from the nearest object that counts as not playing
FINE = 1 / 3 + TOL   # beats
JUMP = 3.0           # radii: the player taps, never rides, jumps this wide
STRAIGHT = 150.0     # degrees: a turn at least this open counts as a straight line
LINE = 0.5           # radii: closer than this between circles is a stack, whose turns are noise

csv.field_size_limit(10**7)


def spinners(path):
    """(start, end) map ms of every spinner in a .osu."""
    sec, out = None, []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line.startswith("["):
            sec = line
            continue
        p = line.split(",")
        if sec == "[HitObjects]" and len(p) >= 6 and int(p[3]) & 8:
            out.append((float(p[2]), float(p[5])))
    return out


def object_times(path):
    """Start time of every hit object in a .osu, in file order: index k is the viewer's object k."""
    sec, out = None, []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line.startswith("["):
            sec = line
            continue
        p = line.split(",")
        if sec == "[HitObjects]" and len(p) >= 4:
            out.append(float(p[2]))
    return out


def plants(crossings):
    """(start, end) monotonic ms of every stretch with both keys on the backplate."""
    on, out, start = {"k1": False, "k2": False}, [], None
    for t, key, edge in crossings:
        on[key] = edge == "land"
        if on["k1"] and on["k2"]:
            start = t
        elif start is not None:
            out.append((start, t))
            start = None
    return out


def main():
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/arms.txt"
    traced = sessions()
    period_of = thresholds_by_replay()
    corpus = {os.path.basename(r["Path"]): r for r in json.load(open("build/corpus.json")) if r["Paired"]}

    objects = collections.defaultdict(list)
    with open("build/geometry.csv", newline="") as f:
        rd = csv.reader(f)
        hdr = next(rd)
        ix = [hdr.index(k) for k in ("startTime", "kind", "travelTime", "spacingRadii", "result", "signedAngle")]
        for r in rd:
            if r[0] in traced:
                angle = abs(math.degrees(float(r[ix[5]]))) if r[ix[5]] else None
                objects[r[0]].append((float(r[ix[0]]), r[ix[1]], float(r[ix[2]] or 0), float(r[ix[3]] or 0),
                                      r[ix[4]] != "Unjudged", angle))

    counts = collections.defaultdict(collections.Counter)   # (group key) -> cell -> n
    exposure = collections.defaultdict(lambda: [0.0, 0, 0])  # (group key) -> [hours, objects, jump objects]
    examples, dump = [], []
    tempo = collections.Counter()   # unintended arms by the tightest head-to-head gap around them, 25 ms bins
    planted = {}                    # id(crossings) -> plant stretches, one trace's worth
    for name, (to_mono, spans, crossings) in traced.items():
        if name not in corpus or not objects.get(name):
            continue
        path = corpus[name]["BeatmapPath"]
        starts, beats = timing(path)
        if not starts:
            continue

        def beat(t):
            return beats[max(bisect.bisect_right(starts, t + 1) - 1, 0)]

        # A quit or failed run leaves the rest of the map Unjudged, and what the keys did after
        # that is the next attempt or the menus: cut each replay at its last judged object.
        index_of = {t: k for k, t in enumerate(object_times(path))}
        o = sorted(objects[name])
        last = max((i for i, x in enumerate(o) if x[4]), default=-1)
        o = o[:last + 1]
        if len(o) < 2:
            continue
        spin = [(to_mono(a) - SPIN_PAD, to_mono(b) + SPIN_PAD) for a, b in spinners(path)]
        heads = [to_mono(t) for t, *_ in o]
        ends = [to_mono(t + (tr + LENIENCY if k == "slider" else 0)) for t, k, tr, *_ in o]
        # gap after object i, in beats, from its end (a slider's tail) to the next head
        gaps = [(o[i + 1][0] - (o[i][0] + (o[i][2] + LENIENCY if o[i][1] == "slider" else 0))) / beat(o[i][0])
                for i in range(len(o) - 1)]
        # Straight fours and doublets: maximal runs on one fine snap, four or two taps long.
        straight, doublet = set(), set()
        i = 0
        while i < len(gaps):
            if not 0 < gaps[i] <= FINE:
                i += 1
                continue
            j = i
            while j + 1 < len(gaps) and abs(gaps[j + 1] - gaps[i]) <= TOL and o[j + 1][1] == "circle":
                j += 1
            run = list(range(i, j + 2))
            # lazer's signedAngle on object n is the turn at n-1 (between n-2, n-1 and n), so the
            # two middle circles' turns are stored on the run's third and fourth objects.
            if (len(run) == 4 and all(o[x][1] == "circle" for x in run[:3])
                    and all(o[x][3] >= LINE for x in run[1:])
                    and all(o[x][5] is not None and o[x][5] >= STRAIGHT for x in run[2:4])):
                straight.update(run)
            elif len(run) == 2 and all(o[x][1] == "circle" for x in run):
                doublet.update(run)
            i = j + 1
        lo, hi = heads[0] - GAP, ends[-1] + GAP
        day = re.findall(r"\((\d{4}-\d\d-\d\d)_", name)[-1]
        keys = (("period", period_of.get(name, "unknown")), ("day", day))
        # Exposure for the confident cell: objects that are themselves a JUMP+ radii jump with
        # nothing on a fine snap around them, i.e. where an arm would land in that cell.
        jumps = sum(1 for q in range(1, len(o)) if o[q][3] >= JUMP
                    and not any(0 < gaps[i] <= FINE for i in range(max(q - 1, 0), min(q + RIDE_AHEAD, len(o) - 1))))
        for key in keys:
            exposure[key][0] += (ends[-1] - heads[0]) / 3.6e6
            exposure[key][1] += len(o)
            exposure[key][2] += jumps

        if id(crossings) not in planted:
            planted[id(crossings)] = plants(crossings)
        stretches = planted[id(crossings)]
        starts_p = [a for a, _ in stretches]
        for on, _ in spans:
            k = bisect.bisect_right(starts_p, on + 1) - 1
            if k >= 0 and stretches[k][1] >= on - 1:
                on = stretches[k][0]
            if not lo <= on <= hi:
                continue
            q = bisect.bisect_left(heads, on - 30.0)      # the object the arm leads into
            near = min(abs(on - heads[min(q, len(o) - 1)]), abs(on - ends[max(q - 1, 0)]))
            window = range(max(q - 1, 0), min(q + RIDE_AHEAD, len(o) - 1))
            if any(a <= on <= b for a, b in spin):
                cell = "spinner"
            elif near > GAP or on < heads[0] - SPIN_PAD:
                cell = "break"
            elif (fine := [i for i in window if 0 < gaps[i] <= FINE]) and all(
                    i in straight and i + 1 in straight for i in fine):
                cell = "straight four"      # the only thing near it to ride is a line never ridden
            elif fine and all(i in doublet and i + 1 in doublet for i in fine):
                cell = "doublet"            # the same for two taps
            elif fine:
                cell = "rideable"
            elif q > 0 and heads[q - 1] <= on <= ends[q - 1] and o[q - 1][1] == "slider":
                cell = "slider hold"
            else:
                ms = [(o[i + 1][0] - o[i][0]) for i in window if o[i + 1][0] > o[i][0]]
                sp = min((o[i + 1][3] for i in window), default=0)
                cell = "unintended, jump" if sp >= JUMP else "unintended, close"
                if sp >= JUMP:
                    examples.append((name, o[min(q, len(o) - 1)][0]))
                tempo[(min(ms) // 25 * 25 if ms else -1, "<1.5" if sp < 1.5 else "1.5-3" if sp < 3 else "3-5" if sp < 5 else "5+")] += 1
            for key in keys:
                counts[key][cell] += 1
            led = o[min(q, len(o) - 1)]
            dump.append({"replay": name, "cell": cell, "object": index_of.get(led[0]),
                         "map_ms": round(led[0] + (on - heads[min(q, len(o) - 1)]))})

    cells = ("rideable", "spinner", "slider hold", "unintended, close", "unintended, jump", "straight four", "doublet",
             "break")
    buf = io.StringIO()
    p = lambda *a: print(*a, file=buf)
    for group in ("period", "day"):
        p(f"\nby {group}")
        p(f"  {'':28}{'hours':>7}{'objects':>9}" + "".join(f"{c:>19}" for c in cells)
          + f"{'jump objs':>11}{'per 1000 jumps':>16}")
        for key in sorted(k for k in counts if k[0] == group):
            h, n, nj = exposure[key]
            c = counts[key]
            p(f"  {key[1]:28}{h:>7.1f}{n:>9,}" + "".join(f"{c[x]:>19,}" for x in cells)
              + f"{nj:>11,}{1000 * c['unintended, jump'] / nj if nj else float('nan'):>16.2f}")
    p("\nunintended arms by the tightest head-to-head gap around them (ms) x the smallest jump into the next objects (radii)")
    sps = ("<1.5", "1.5-3", "3-5", "5+")
    p(f"  {'':12}" + "".join(f"{x:>8}" for x in sps))
    for b in sorted({b for b, _ in tempo}):
        p(f"  {b:>5.0f}-{b + 25:<5.0f} " + "".join(f"{tempo[(b, x)]:>8}" for x in sps))
    p("\nfirst few confident (unintended, jump) arms (replay, map ms of the object they led into), for checking by eye:")
    for name, t in examples[:8]:
        p(f"  {t:>9.0f}  {name}")
    with open("build/arms.jsonl", "w") as f:
        for d in dump:
            f.write(json.dumps(d) + "\n")
    text = buf.getvalue()
    print(text)
    with open(out_path, "w") as f:
        f.write(text)


if __name__ == "__main__":
    main()
