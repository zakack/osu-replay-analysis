"""Which tap was meant for which circle in a stream, and what a string of 50s really was.

In a stream the circles overlap and the cursor sits on several at once, so position cannot
say which circle a press was meant for. Rhythm can. A stream of N circles asks for N taps on
a fixed grid, so the k-th tap in time was meant for the k-th circle, with room for a tap
that never came or one too many. That is a monotonic alignment of two time series, solved
here by a small dynamic programme with fixed costs. Lazer's own account of each press comes
from build/presses.csv (`ora presses`): what it landed on and whether notelock refused it.
Intent against outcome is the whole measurement.

Why it matters for a string of 50s. Lazer gives a press to the earliest unjudged circle
under the cursor. Drop one tap in an overlapping stream and the tap meant for circle n+1
lands on circle n, a whole interval late; the tap for n+2 lands on n+1, and so on. Every
circle after the gap is judged by its successor's tap, each one an interval late, until a
spare tap realigns them. From inside the game that is an inexplicable string of 50s. Here
it is a run of presses whose intended circle is one ahead of the circle that took them.

Runs. Constant-snap runs from build/rhythm.csv at tolerance 0.02 (the value load.py
settled on), snap of a third of a beat or finer, split into maximal stretches of tapped
objects (circles and slider heads) exactly one snap apart, kept when five or longer. A run
of kick sliders whose heads sit two ticks apart is not tapped as a stream and drops out.

Alignment costs, in units of the run's tap interval I (ms between circles). The model is one
stroke per circle, in order: a gap in the alignment is a claim that a stroke is missing or
spare, and it is cheap only where the press times themselves show one.
  match a press to a circle      |press - circle| / I, allowed up to MAX_MATCH = 1.5
  leave a press unmatched        EXTRA = 0.6 when it is within BOUNCE = 0.3 I of a neighbouring
                                 press (a bounce), after the last circle is matched
                                 (overtapping past the stream's end), or more than I/2 before
                                 the first circle; otherwise EXTRA_FAR = 8
  leave a circle unmatched       DROP = 0.6 when the presses either side of it are at least
                                 HOLE = 1.8 I apart, after the last press, or before a first
                                 press that comes more than I/2 late; otherwise DROP_FAR = 8
The FAR costs are soft walls, not bans: a run whose press count cannot fit its circles without
a gap still aligns, with the gap where it costs least. Higher walls score marginally better
against the fingers but start escaping through the edges -- unmatching whole stretches as
leading extras and trailing drops -- which the finger score cannot see, since it only grades
presses that were matched.

Why not flat costs. With EXTRA = DROP = 0.6 everywhere (the first version), a stream tapped
progressively late was cheaper to read as a dropped tap followed by every circle taking its
successor's tap than as each circle taking its own late one, and a rushed stream that
overtapped at its end was cheaper to read as a double tap in the middle. Where the player
alternates by hand the button follows the circle slot, and it is an independent grade of the
alignment, kept out of the costs for that reason: every press matched within 16 slots of a
4-tap alternating anchor, on the button its slot predicts. Flat costs scored 86.4% (plain
buttons era), 86.5% (glidepad), 92.1% (exclusive); these costs 96.2%, 94.6%, 96.7%.

That grade cannot see a whole run shifted by one slot, since a constant shift keeps every
press on its parity, and an edge exemption on both ends buys exactly that for two cheap
gaps. It scored higher (98%) by doing it. Hence the guards at the front: a leading extra or
a leading drop needs the press times to show one. The tail keeps its exemption, because
overtapping past a stream's end is real, and so a spare stroke mid-run can still be taken as
the next circle's tap with the tail shifted, leaving the last stroke as overtapping
(test_streams.py pins this as a known limit).

Tried and rejected against the same grade, kept here so they are not tried again: charging
for matching a press anywhere but the circle lazer judged it on (every penalty above 0 scored
lower: it erases real cursor-caused shifts with the false ones); a match cost steeper past
half an interval (lower at every knee and slope tried); a "split" rule taking any press that divides one interval between
its neighbours as an inserted stroke (flat to lower). BOUNCE 0.3 edged 0.5; FAR 8 beat 4.

On a tie, matches go as early as possible and gaps last: a press exactly between two circles
is the earlier circle's late tap, two presses on one circle make the second the extra. The
result is a function of the times.

Run checks before any of that is trusted. The median gap between successive presses in the
run window, in beats, against the run's snap:
  within 12% of the snap          aligned normally
  within 6% of another snap       "phase shift": tapped at that fraction (1/3 for 1/4...)
  neither                         "unalignable": flagged and not classified

Per circle in an aligned run, two facts (see classify): what became of the tap meant for
it (own, elsewhere, notelock, stray, dropped) and which tap judged it, by intent (self, the
next circle's, the previous circle's, an extra one, none).

A string of three or more consecutive Meh judgements in a run is explained by the majority
of who judged its members: "pushback" when the tap meant for the next circle did (the chain
above) and some earlier circle in the run took its own tap, so the grid is anchored;
otherwise a shift present from the run's first circle is read as lateness on entry, "own-late"/"own-early" when each circle took its own tap and the tap was simply
off, "ahead" when the previous circle's tap did, "phase-shift" or "unalignable" from the run
checks; "ahead" is the mirror of pushback, each circle taken a whole interval early by the
tap meant for the one before. For a shifted chain the origin is the failed tap (dropped,
notelocked, stray), double tap, or for "ahead" a tap that landed on the next circle
because the cursor had already left its own ("cursor left early"), at the head of the chain,
which can be well before the first 50, or "entered shifted" when the chain runs back to the run's first circle.

    python3 tools/analysis/streams.py cache     # once: build/streams.pickle from rhythm.csv
    python3 tools/analysis/streams.py report [--out build/streams.txt]
"""
from __future__ import annotations

import collections
import csv
import json
import math
import os
import pickle
import random
import statistics
import sys

RHYTHM = "build/rhythm.csv"
PRESSES = "build/presses.csv"
CACHE = "build/streams.pickle"
PLAYER = "zaksynack playing"
TOLERANCE = "0.02"

MAX_SNAP = 1 / 3 + 0.01
MIN_RUN = 5
EXTRA = 0.6
DROP = 0.6
EXTRA_FAR = 8.0
DROP_FAR = 8.0
BOUNCE = 0.3
HOLE = 1.8
MAX_MATCH = 1.5
RATE_OK = 0.12
RATE_SNAP = 0.06
LATTICE = (0.125, 1 / 6, 0.25, 1 / 3, 0.5)
FRACTION_NAMES = {0.125: "1/8", 1 / 6: "1/6", 0.25: "1/4", 1 / 3: "1/3", 0.5: "1/2"}


# ------------------------------------------------------------------------------------------
# Core: pure functions over times, so the tests can drive them without files
# ------------------------------------------------------------------------------------------

COSTS = ("EXTRA", "DROP", "EXTRA_FAR", "DROP_FAR", "BOUNCE", "HOLE", "MAX_MATCH")


def align(objects: list[float], presses: list[float], interval: float,
          costs: dict | None = None) -> list[tuple[int | None, int | None]]:
    """Monotonic alignment. Returns pairs (object index or None, press index or None) in
    time order: a match, a dropped circle (press None) or an extra press (object None).
    ``costs`` overrides any of COSTS by name, for sensitivity runs (drift.py sensitivity)."""
    c_ = {k: globals()[k] for k in COSTS} | (costs or {})
    EXTRA, DROP, EXTRA_FAR, DROP_FAR = c_["EXTRA"], c_["DROP"], c_["EXTRA_FAR"], c_["DROP_FAR"]
    BOUNCE, HOLE, MAX_MATCH = c_["BOUNCE"], c_["HOLE"], c_["MAX_MATCH"]
    n, m = len(objects), len(presses)
    inf = float("inf")

    def extra_cost(j):
        t = presses[j]
        near = min((abs(t - presses[k]) for k in (j - 1, j + 1) if 0 <= k < m), default=inf)
        return EXTRA if near < BOUNCE * interval else EXTRA_FAR

    def drop_cost(j):
        # Circle dropped between press j-1 and press j (the next one not yet consumed). Past
        # the last press it is free to stop (a stream abandoned, or dragged so far that the
        # strokes ran out before the circles did). Before the first press it is a late entry,
        # but only if that press really is late: otherwise a leading drop plus an overtapped
        # trailing extra would phase-shift a whole run, which nothing grading the alignment
        # by alternation could see (a constant shift keeps every press on its parity).
        if j == m:
            return DROP
        if j == 0:
            return DROP if not objects or presses[0] > objects[0] + interval / 2 else DROP_FAR
        return DROP if presses[j] - presses[j - 1] >= HOLE * interval else DROP_FAR

    extras = [extra_cost(j) for j in range(m)]
    drops = [drop_cost(j) for j in range(m + 1)]
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    step = [[0] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == inf:
                continue
            if i < n and j < m:
                d = abs(presses[j] - objects[i]) / interval
                if d <= MAX_MATCH and c + d < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], step[i + 1][j + 1] = c + d, 1
            # <= on the gap moves: on a tie, match as early as possible and put gaps last.
            # Overtapping past a stream's end is the cheap kind of spare stroke, anywhere after
            # the last circle is matched. At the front only a press that really is before the
            # run counts: with both ends cheap, a leading "extra" plus a trailing drop would
            # phase-shift a whole run for the price of two edge gaps.
            if j < m:
                lead = i == 0 and presses[j] < objects[0] - interval / 2
                e = EXTRA if i == n or lead else extras[j]
                if c + e <= cost[i][j + 1]:
                    cost[i][j + 1], step[i][j + 1] = c + e, 2
            if i < n and c + drops[j] <= cost[i + 1][j]:
                cost[i + 1][j], step[i + 1][j] = c + drops[j], 3
    pairs = []
    i, j = n, m
    while i or j:
        s = step[i][j]
        if s == 1:
            i, j = i - 1, j - 1
            pairs.append((i, j))
        elif s == 2:
            j -= 1
            pairs.append((None, j))
        else:
            i -= 1
            pairs.append((i, None))
    pairs.reverse()
    return pairs


def rate_check(presses: list[float], snap: float, beat: float) -> tuple[str, float | None]:
    """("ok", None), ("phase-shift", fraction) or ("unalignable", None)."""
    if len(presses) < 3:
        return "unalignable", None
    gaps = sorted(b - a for a, b in zip(presses, presses[1:]))
    r = statistics.median(gaps) / beat
    if abs(r - snap) <= RATE_OK * snap:
        return "ok", None
    for f in LATTICE:
        if abs(f - snap) > 1e-6 and abs(r - f) <= RATE_SNAP * f:
            return "phase-shift", f
    return "unalignable", None


def classify(objects: list[float], results: list[str], errors: list[float | None],
             presses: list[tuple[float, str, float | None]], snap: float, beat: float,
             costs: dict | None = None) -> dict:
    """One run. ``presses`` are (time, outcome, target start time) in the run's window.

    Two facts per circle, kept apart because a cascade needs both:
      tap     what became of the press meant for it: own (it judged this circle), elsewhere
              (it judged a neighbour), notelock, stray, dropped (no press meant for it)
      judge   which press judged it, by intent: self, next (the tap meant for the next
              circle: "absorbed"), prev ("ahead"), extra (an unmatched press), none
    Plus ``double``: circles nearest an unmatched press."""
    interval = snap * beat
    times = [p[0] for p in presses]
    verdict, fraction = rate_check(times, snap, beat)
    n = len(objects)
    tap = ["?"] * n
    judge = ["none"] * n
    double = [False] * n

    if verdict == "ok":
        pairs = align(objects, times, interval, costs)
        intent = {j: i for i, j in pairs if i is not None and j is not None}
        index_of = {round(t, 1): k for k, t in enumerate(objects)}
        for i, j in pairs:
            if i is not None and j is None:
                tap[i] = "dropped"
        for j, (t, outcome, target) in enumerate(presses):
            k = index_of.get(round(target, 1)) if target is not None else None
            i = intent.get(j)
            if outcome == "hit" and k is not None:
                judge[k] = ("extra" if i is None else "self" if i == k else
                            "next" if i == k + 1 else "prev" if i == k - 1 else "far")
            if i is None:
                continue
            if outcome == "notelock":
                tap[i] = "notelock"
            elif outcome in ("nothing", "window"):
                tap[i] = "stray"
            else:
                tap[i] = "own" if k == i else "elsewhere"
        for i, j in pairs:
            if i is None:
                k = min(range(n), key=lambda q: (abs(objects[q] - times[j]), q))
                double[k] = True

    strings = []
    k = 0
    while k < len(results):
        if results[k] != "Meh":
            k += 1
            continue
        e = k
        while e < len(results) and results[e] == "Meh":
            e += 1
        if e - k >= 3:
            strings.append(_explain(k, e, tap, judge, double, errors, verdict, fraction))
        k = e
    return {"verdict": verdict, "fraction": fraction, "tap": tap, "judge": judge,
            "double": double, "strings": strings, "pairs": pairs if verdict == "ok" else []}


CAUSES = ("dropped", "notelock", "stray")


def _explain(start, end, tap, judge, double, errors, verdict, fraction) -> dict:
    if verdict != "ok":
        cls = "phase-shift" if verdict == "phase-shift" else "unalignable"
        return {"start": start, "end": end, "class": cls, "fraction": fraction, "origin": "-"}
    votes = collections.Counter(judge[start:end])
    top = votes.most_common(1)[0][0]
    if top == "next" and "self" in judge[:start]:
        cls = "pushback"
    elif top == "next":
        # Shifted from the run's first circle: no on-grid tap anchors the alignment, and a
        # steady tap 0.7 of an interval late reads equally well as 0.3 early for the next
        # circle. A cascade needs a correctly-placed tap before the drop; without one this
        # is lateness on entry, which is what lazer judged it as.
        signs = [e for e in errors[start:end] if e is not None]
        cls = "own-late" if not signs or statistics.median(signs) > 0 else "own-early"
    elif top == "self":
        signs = [e for e in errors[start:end] if e is not None]
        cls = "own-late" if signs and statistics.median(signs) > 0 else "own-early"
    elif top == "prev" and "self" in judge[:start]:
        cls = "ahead"
    elif top == "prev":
        signs = [e for e in errors[start:end] if e is not None]
        cls = "own-early" if not signs or statistics.median(signs) < 0 else "own-late"
    else:
        cls = top
    # The cause sits where the shift began, which can be well before the 50s do: walk back
    # along the chain of circles judged by the same neighbour, then look at the taps at its
    # head. A chain reaching the run's first circle entered the run already shifted.
    origin = "none"
    if top in ("next", "prev"):
        q = start
        while q > 0 and judge[q - 1] == top:
            q -= 1
        if q == 0:
            origin = "entered shifted"
        elif top == "prev":
            # The circle before the chain decides it. Taken by an unmatched press: a double
            # tap pulled the stream a circle ahead. Taken by no press at all: its own tap went
            # on to the next circle because the cursor had already left it, and it missed.
            # (Every tap inside the chain lands on a neighbour by definition, so the taps
            # there are the symptom, not the cause.)
            if judge[q - 1] == "extra" or double[q - 1] or double[q]:
                origin = "double"
            elif tap[q - 1] in CAUSES:
                origin = tap[q - 1]
            elif judge[q - 1] == "none":
                origin = "cursor left early"
        else:
            for r in (q, q - 1):
                if tap[r] in CAUSES:
                    origin = tap[r]
                    break
                if double[r]:
                    origin = "double"
                    break
    else:
        for q in range(start, max(-1, start - 3), -1):
            if tap[q] in CAUSES:
                origin = tap[q]
                break
            if double[q]:
                origin = "double"
                break
    return {"start": start, "end": end, "class": cls, "fraction": None, "origin": origin}


# Chain origins by which hand started them. The cursor class is validated against the
# player's fingers (attribution_check.py, attribution_riding.py: 93-97% where they alternate);
# the button class, after the evidence-gated alignment, still mostly is not (about 90% of its
# remaining chains side with lazer's reading), so a correction should touch the cursor class
# only.
CURSOR = ("stray-nothing", "notelock", "cursor left early")
BUTTON = ("dropped", "double", "stray-window")


def chains(objects, window, r):
    """Yield (origin, [circle indices]) per anchored shifted chain in an aligned run, with
    stray split by whether the press hovered nothing or a circle outside its window."""
    judge, tap, double = r["judge"], r["tap"], r["double"]
    times = [p[0] for p in window]
    pairs = r["pairs"]
    meant_press = {i: j for i, j in pairs if i is not None and j is not None}

    def cause(i):
        t = tap[i]
        if t == "stray":
            return "stray-window" if window[meant_press[i]][1] == "window" else "stray-nothing"
        return t if t in CAUSES else None

    n = len(objects)
    k = 0
    while k < n:
        d = judge[k]
        if d not in ("next", "prev") or "self" not in judge[:k]:
            k += 1
            continue
        q = k
        while k < n and judge[k] == d:
            k += 1
        origin = "other"
        if d == "prev":
            if judge[q - 1] == "extra" or double[q - 1] or double[q]:
                origin = "double"
            elif cause(q - 1):
                origin = cause(q - 1)
            elif judge[q - 1] == "none":
                origin = "cursor left early"
        else:
            for x in (q, q - 1):
                if cause(x):
                    origin = cause(x)
                    break
                if double[x]:
                    origin = "double"
                    break
        yield origin, list(range(q, k))


# ------------------------------------------------------------------------------------------
# Corpus
# ------------------------------------------------------------------------------------------

def build_runs(rhythm_path: str, keep) -> list[tuple[str, list]]:
    """Tapped stream runs from a rhythm CSV, for the replays ``keep(name)`` accepts: maximal
    stretches of tapped objects (circles, slider heads) exactly one snap apart, snap of a third
    of a beat or finer, MIN_RUN or longer. Each event is (time, beatLength, snap, result,
    error)."""
    runs: dict[tuple[str, str], list] = collections.defaultdict(list)
    with open(rhythm_path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["tolerance"] != TOLERANCE or not keep(r["replay"]):
                continue
            if not r["snap"] or float(r["snap"]) > MAX_SNAP or r["kind"] not in ("circle", "sliderHead"):
                continue
            if int(r["runSize"]) < MIN_RUN:
                continue
            runs[(r["replay"], r["runId"])].append(
                (float(r["time"]), float(r["beatLength"]), float(r["snap"]), r["result"],
                 float(r["error"]) if r["error"] else None))
    out = []
    for (replay, _), events in runs.items():
        events.sort()
        # Maximal stretches of tapped objects exactly one snap apart.
        cur = [events[0]]
        for ev in events[1:]:
            gap = (ev[0] - cur[-1][0]) / ev[1]
            if abs(gap - ev[2]) <= 0.02:
                cur.append(ev)
            else:
                if len(cur) >= MIN_RUN:
                    out.append((replay, cur))
                cur = [ev]
        if len(cur) >= MIN_RUN:
            out.append((replay, cur))
    return out


def cache() -> None:
    """Tapped stream runs from rhythm.csv, the player's only. One pass over ~6GB."""
    out = build_runs(RHYTHM, lambda name: name.startswith(PLAYER))
    with open(CACHE, "wb") as fh:
        pickle.dump(out, fh)
    print(f"{len(out)} tapped stream runs across {len({r for r, _ in out})} replays")


def load_presses(replays: set[str]) -> dict[str, list[tuple[float, str, float | None]]]:
    out: dict[str, list] = collections.defaultdict(list)
    with open(PRESSES, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["replay"] in replays:
                out[r["replay"]].append((float(r["time"]), r["outcome"],
                                         float(r["target"]) if r["target"] else None))
    for v in out.values():
        v.sort()
    return out


def report(out_path: str | None) -> None:
    import bisect
    with open(CACHE, "rb") as fh:
        runs = pickle.load(fh)
    presses = load_presses({r for r, _ in runs})
    md5 = {os.path.basename(e["Path"]): e["BeatmapMd5"] for e in json.load(open("build/corpus.json"))}

    verdicts = collections.Counter()
    fractions = collections.Counter()
    strings = []          # (class, origin, bpm, run length, replay, time, md5)
    for replay, events in runs:
        ps = presses.get(replay)
        if ps is None:
            continue
        objects = [e[0] for e in events]
        beat, snap = events[0][1], events[0][2]
        interval = snap * beat
        lo = bisect.bisect_left(ps, (objects[0] - interval,))
        hi = bisect.bisect_right(ps, (objects[-1] + interval, "~"))
        result = classify(objects, [e[3] for e in events], [e[4] for e in events], ps[lo:hi], snap, beat)
        verdicts[result["verdict"]] += 1
        if result["fraction"]:
            fractions[(FRACTION_NAMES.get(snap, f"{snap:.3f}"), FRACTION_NAMES.get(result["fraction"]))] += 1
        bpm = 60000 / beat
        for s in result["strings"]:
            strings.append((s["class"], s["origin"], bpm, len(objects), replay, objects[s["start"]], md5.get(replay)))

    lines = []
    p = lines.append
    total = sum(verdicts.values())
    p(f"tapped stream runs: {total}  aligned {verdicts['ok']}  phase-shift {verdicts['phase-shift']}  "
      f"unalignable {verdicts['unalignable']} ({100 * verdicts['unalignable'] / max(1, total):.1f}%)")
    if fractions:
        p("phase shifts (map snap -> tapped as): " + ", ".join(f"{a}->{b} {n}" for (a, b), n in fractions.most_common()))
    p(f"\nstrings of >=3 consecutive 50s inside a stream run: {len(strings)}")
    cls = collections.Counter(s[0] for s in strings)
    for c, n in cls.most_common():
        p(f"  {c:<12} {n:6}  {100 * n / max(1, len(strings)):5.1f}%")
    for k in ("pushback", "ahead"):
        p(f"\norigin of {k} strings (the tap at the head of the shifted chain):")
        for o, n in collections.Counter(s[1] for s in strings if s[0] == k).most_common():
            p(f"  {o:<16} {n:6}")

    def split(key, edges, label):
        p(f"\nby {label}: share of each class")
        for lo_, hi_ in zip(edges, edges[1:]):
            sel = [s for s in strings if lo_ <= key(s) < hi_]
            if not sel:
                continue
            c = collections.Counter(s[0] for s in sel)
            p(f"  {lo_:>5}-{hi_:<5} n={len(sel):5}  " + "  ".join(
                f"{k} {100 * c[k] / len(sel):4.1f}%" for k in ("pushback", "own-late", "own-early", "dropped", "notelock", "phase-shift", "unalignable")))
    split(lambda s: s[2], (0, 150, 180, 200, 220, 250, 1000), "map BPM (DT/HT rate not applied)")
    split(lambda s: s[3], (5, 8, 12, 17, 25, 33, 1000), "run length (circles)")

    # One concrete example of the dominant class, the latest one, to go and look at.
    if strings:
        top = cls.most_common(1)[0][0]
        pool = [s for s in strings if s[0] == top and s[2] >= 150] or [s for s in strings if s[0] == top]
        ex = max(pool, key=lambda s: s[4][-21:])
        p(f"\nexample ({top}): {ex[4]} at {ex[5] / 1000:.1f}s (map time), {ex[2]:.0f} BPM, run of {ex[3]}")

    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "cache":
        cache()
    else:
        report(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/streams.txt")
