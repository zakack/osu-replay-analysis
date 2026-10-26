"""Timing on 1/4 runs that start with a slider, against runs that start with a circle.

A run is consecutive objects 1/4 beat apart (+-0.02 beat, the tolerance streams.py settled on),
with the gap out of a slider measured from its tail (lazer's travelTime plus the 36 ms tail
leniency it stops short of). A slider inside a run ends it at its head. Slider-led: the first
object is a slider, and its members are the objects after it (positions 2..L). Circle-led: the
first object is a circle not itself reached on 1/4 from the one before (positions 1..L).

The two are compared within cells of (input era, run length, position, kind of member, 1/4
interval in 10 ms, spacing of the member), then rolled up with weights n_S n_C / (n_S + n_C).
Spacing has to be in the cell: the object after a slider tail is nearly always a 1.5-5 radii
jump, the second circle of a circle-led triple nearly always stacked, and without it a +9 ms
"late after the tail" appears that is the jump, not the timing.

Even lengths are printed but not worth reading: circle-led 1/4 runs of 4 or 6 are rare in
mapping (a few hundred against tens of thousands), so those cells rest on a handful of runs.

Rate-changing mods and Guest replays are dropped. The deep latch vs tapping split, which is
where this effect actually lives, needs the key traces: riding.py.

    python3 tools/analysis/sliderled.py [--rebuild] [--out build/sliderled.txt]
"""
from __future__ import annotations

import bisect
import collections
import csv
import io
import json
import math
import os
import sys

CACHE = "build/sliderled.json"
TOL = 0.02          # beats
LENIENCY = 36       # ms lazer's travelTime stops short of the slider's end
RATE = ("DT", "NC", "HT", "DC")   # acronyms run together in the score index, e.g. "DTHD"

csv.field_size_limit(10**7)


def era(date):
    if date < "2025-02-08": return "1 QMK toggle (exclusive)"
    if date < "2025-12-15": return "2 plain buttons (overlapping)"
    if date < "2026-09-05": return "3 filter, exclusive"
    return "4 analog doubletap"


def spacing_bin(sp):
    return ("0 stacked <0.5" if sp < 0.5 else "1 0.5-1.5" if sp < 1.5 else
            "2 1.5-3" if sp < 3 else "3 3-5" if sp < 5 else "4 5+")


_reds = {}
def timing(path):
    """Uninherited timing points of a .osu: (start times, beat lengths)."""
    if path not in _reds:
        sec, red = None, []
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line.startswith("["):
                sec = line
                continue
            p = line.split(",")
            if sec == "[TimingPoints]" and len(p) >= 2 and (len(p) < 7 or p[6] == "1"):
                try:
                    if float(p[1]) > 0:
                        red.append((float(p[0]), float(p[1])))
                except ValueError:
                    pass
        red.sort()
        _reds[path] = ([s for s, _ in red], [b for _, b in red])
    return _reds[path]


def runs(objects, beat):
    """Quarter-snapped runs of 3-6 objects: yields (lead kind, member indices)."""
    def end(x):
        return x[0] + (x[2] + LENIENCY if x[1] == "slider" else 0)

    def quarter(a, b):
        if "spinner" in (a[1], b[1]):
            return False
        return abs((b[0] - end(a)) / beat(a[0]) - 0.25) <= TOL

    n = len(objects)
    for i in range(n - 1):
        lead = objects[i]
        if lead[1] == "spinner":
            continue
        if lead[1] == "circle" and i > 0 and quarter(objects[i - 1], lead):
            continue
        members, j = [i], i
        while j + 1 < n and quarter(objects[j], objects[j + 1]):
            j += 1
            members.append(j)
            if objects[j][1] != "circle":
                break
        if 3 <= len(members) <= 6:
            yield lead[1], members


def build():
    corpus = {os.path.basename(r["Path"]): r for r in json.load(open("build/corpus.json")) if r["Paired"]}
    scores = {s["Filename"] + ".osr": s for s in json.load(open("build/score-index.json"))}
    # cell -> group -> [n, sum, sumsq, nonGreat, miss, timed]
    cells = collections.defaultdict(lambda: {"S": [0, 0.0, 0.0, 0, 0, 0], "C": [0, 0.0, 0.0, 0, 0, 0]})

    def process(name, rows):
        rec, sc = corpus.get(name), scores.get(name)
        if not rec or not sc or name.startswith("Guest") or any(m in sc["Mods"] for m in RATE):
            return
        starts, beats = timing(rec["BeatmapPath"])
        if not starts:
            return
        e = era(sc["Date"][:10])
        o = sorted((float(r[0]), r[1], float(r[2] or 0), r[3], r[4], float(r[5] or 0)) for r in rows)

        def beat(t):
            return beats[max(bisect.bisect_right(starts, t + 1) - 1, 0)]

        for kind, members in runs(o, beat):
            g = "S" if kind == "slider" else "C"
            ivl = round(beat(o[members[0]][0]) / 4 / 10) * 10
            for pos, x in enumerate(members, 1):
                m = o[x]
                if (g == "S" and pos == 1) or m[1] not in ("circle", "slider"):
                    continue
                c = cells["|".join(map(str, (e, len(members), pos, m[1], ivl, spacing_bin(m[5]))))][g]
                c[0] += 1
                c[3] += m[4] != "Great"
                c[4] += m[4] == "Miss"
                if m[3]:
                    h = float(m[3])
                    c[1] += h
                    c[2] += h * h
                    c[5] += 1

    keep = ("startTime", "kind", "travelTime", "hitError", "result", "spacingRadii")
    with open("build/geometry.csv", newline="") as f:
        rd = csv.reader(f)
        hdr = next(rd)
        ix = [hdr.index(k) for k in keep]
        cur, rows = None, []
        for r in rd:
            if r[0] != cur:
                if rows:
                    process(cur, rows)
                cur, rows = r[0], []
            rows.append([r[i] for i in ix])
        if rows:
            process(cur, rows)

    json.dump(cells, open(CACHE, "w"))
    return cells


def roll(cells, keyfn, keep=lambda key: True):
    """Slider-led minus circle-led per cell, rolled up by keyfn(cell fields)."""
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    for k, g in cells.items():
        f = k.split("|")
        key = keyfn(f)
        if not keep(key):
            continue
        S, C, a = g["S"], g["C"], agg[key]
        a["nS"] += S[0]
        a["nC"] += C[0]
        if S[0] and C[0]:
            wr = S[0] * C[0] / (S[0] + C[0])
            a["wr"] += wr
            a["ngS"] += wr * S[3] / S[0]
            a["ngC"] += wr * C[3] / C[0]
            a["dmiss"] += wr * (S[4] / S[0] - C[4] / C[0])
        if S[5] >= 2 and C[5] >= 2:
            mS, mC = S[1] / S[5], C[1] / C[5]
            vS, vC = max(S[2] / S[5] - mS ** 2, 0), max(C[2] / C[5] - mC ** 2, 0)
            w = S[5] * C[5] / (S[5] + C[5])
            a["w"] += w
            a["dm"] += w * (mS - mC)
            a["var"] += w * w * (vS / S[5] + vC / C[5])
            a["sdS"] += w * math.sqrt(vS)
            a["sdC"] += w * math.sqrt(vC)
    return {k: a for k, a in agg.items() if a["w"] and a["wr"]}


def show(out, title, res):
    print(f"\n{title}", file=out)
    print(f"  {'':34}{'slider-led':>11}{'circle-led':>11}{'Δ mean ms':>15}{'SD S/C ms':>13}"
          f"{'non-Great S/C':>16}{'Δ miss':>8}", file=out)
    for key in sorted(res):
        a = res[key]
        se = math.sqrt(a["var"]) / a["w"]
        dm = a["dm"] / a["w"]
        print(f"  {' '.join(map(str, key)):34}{int(a['nS']):>11,}{int(a['nC']):>11,}"
              f"{dm:>+7.2f} ({dm / se if se else 0:+.1f}z)"
              f"{a['sdS'] / a['w']:>7.1f}/{a['sdC'] / a['w']:<5.1f}"
              f"{a['ngS'] / a['wr']:>9.1%}/{a['ngC'] / a['wr']:<6.1%}{a['dmiss'] / a['wr']:>+8.2%}", file=out)


def main():
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/sliderled.txt"
    cells = json.load(open(CACHE)) if os.path.exists(CACHE) and "--rebuild" not in sys.argv else build()
    out = io.StringIO()
    parity = lambda f: "odd" if int(f[1]) % 2 else "even"
    show(out, "by era, odd vs even length", roll(cells, lambda f: (f[0], parity(f))))
    show(out, "odd lengths, by era and spacing of the member",
         roll(cells, lambda f: (f[0][:1], parity(f), f[5]), lambda k: k[1] == "odd"))
    show(out, "by era and length", roll(cells, lambda f: (f[0], "L=" + f[1])))
    show(out, "analog era, L=3 and 5 by position",
         roll(cells, lambda f: (f[0][:1], "L=" + f[1], "pos " + f[2], f[3]),
              lambda k: k[0] == "4" and k[1] in ("L=3", "L=5")))
    show(out, "all eras pooled, by length and position",
         roll(cells, lambda f: ("L=" + f[1], "pos " + f[2], f[3])))
    text = out.getvalue()
    print(text)
    with open(out_path, "w") as f:
        f.write(text)


if __name__ == "__main__":
    main()
