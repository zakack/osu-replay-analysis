"""Timing in the traced replays, by tapping mode and by the doubletap thresholds in force.

sliderled.py finds 1/4 runs that start with a slider scattering more than circle-led ones. The
key traces say where: a press is tapped or ridden in the deep latch, and those behave apart.

A hit object's press is at startTime + hitError (map ms), put on CLOCK_MONOTONIC with the
session's align.json (attribution_riding.sessions). It is "deep" if it falls inside a deep span
(+MATCH) and is then labelled with the nearest floor crossing within MATCH ms (k1/k2 lift/land);
"plant" if it falls within PLANT ms before a span opens, the presses that arm the latch;
otherwise "tap". Periods are the thresholds recorded in each trace's `# analog` header, not
dates, because the 2026-10-22 change was found there before it was in setup-timeline.csv.

Sections: (1) circles by mode and riding event; (2) circles by order inside a deep span, with
the gap to the next press as a share of the gap between objects, the entry sequence the
thresholds shape (actuation for press 1 to 2, the backplate for 3 to 4); (3) slider-led against
circle-led 1/4 runs within (length, position, kind, tempo, spacing) cells, split by the run's mode.

    python3 tools/analysis/riding.py [--rebuild] [--out build/riding.txt]
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
import statistics
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from attribution_riding import sessions, MATCH, PLANT        # noqa: E402
from sliderled import RATE, runs, timing                      # noqa: E402

CACHE = "build/riding.json"
csv.field_size_limit(10**7)


def thresholds_by_replay():
    """replay -> its trace's recorded thresholds, as a short label."""
    out = {}
    for aj in sorted(glob.glob("build/traces/*.align.json")):
        stem = aj[:-len(".align.json")]
        head = subprocess.run(f"zstd -dc '{stem}.csv.zst' | head -8", shell=True,
                              capture_output=True, text=True).stdout
        line = next((l for l in head.splitlines() if l.startswith("# analog ")), "")
        kv = dict(p.split("=", 1) for p in line[9:].split() if "=" in p)
        label = (f"act {float(kv.get('actuation_mm', 'nan')):.2f} / "
                 f"bottom_out {float(kv.get('rapid_trigger.bottom_out_mm', 'nan')):.2f}")
        for a in json.load(open(aj)):
            out[a["replay"]] = label
    return out


def stat():
    return [0, 0.0, 0.0, 0, 0]      # n, sum, sumsq, nonGreat, timed


def add(c, he, res):
    c[0] += 1
    c[3] += res != "Great"
    if he is not None:
        c[1] += he
        c[2] += he * he
        c[4] += 1


def build():
    traced = sessions()
    period_of = thresholds_by_replay()
    corpus = {os.path.basename(r["Path"]): r for r in json.load(open("build/corpus.json")) if r["Paired"]}
    scores = {s["Filename"] + ".osr": s for s in json.load(open("build/score-index.json"))}

    by_event = collections.defaultdict(stat)
    by_order = collections.defaultdict(stat)
    order_gap = collections.defaultdict(list)
    run_cells = collections.defaultdict(lambda: {"S": stat(), "C": stat()})
    used = collections.Counter()

    def process(name, rows):
        if name not in corpus or name not in scores or any(m in scores[name]["Mods"] for m in RATE):
            return
        period = period_of.get(name, "unknown")
        used[period] += 1
        to_mono, spans, crossings = traced[name]
        ctimes, sstarts = [c[0] for c in crossings], [s[0] for s in spans]
        starts, beats = timing(corpus[name]["BeatmapPath"])

        def beat(t):
            return beats[max(bisect.bisect_right(starts, t + 1) - 1, 0)]

        o = sorted((float(r[0]), r[1], float(r[2] or 0), float(r[3]) if r[3] else None, r[4], float(r[5] or 0))
                   for r in rows)
        o = [x for x in o if x[1] in ("circle", "slider", "spinner")]

        info = []                       # (mode, riding event, span index) per object
        for t, kind, _, he, _, _ in o:
            mode, ev, span = "miss", None, None
            if he is not None and kind != "spinner":
                m = to_mono(t + he)
                k = bisect.bisect_right(sstarts, m) - 1
                if k >= 0 and spans[k][0] <= m <= spans[k][1] + MATCH:
                    mode, span = "deep", k
                    q = bisect.bisect_left(ctimes, m)
                    near = min((x for x in (q - 1, q) if 0 <= x < len(ctimes)),
                               key=lambda x: abs(ctimes[x] - m), default=None)
                    if near is not None and abs(ctimes[near] - m) <= MATCH:
                        ev = " ".join(crossings[near][1:])
                elif k + 1 < len(spans) and 0 <= spans[k + 1][0] - m <= PLANT:
                    mode, span = "plant", k + 1
                else:
                    mode = "tap"
            info.append((mode, ev, span))

        for i, (mode, ev, _) in enumerate(info):
            if o[i][1] == "circle":
                label = {"tap": "tap", "plant": "plant (pre-latch)", "miss": "miss"}.get(mode, ev or "deep, no crossing")
                add(by_event[(period, label)], o[i][3], o[i][4])

        # Order inside a span: the plant presses count up to 0, the ridden ones from 1.
        per_span = collections.defaultdict(list)
        for i, (mode, _, span) in enumerate(info):
            if span is not None and o[i][1] == "circle":
                per_span[span].append(i)
        for idx in per_span.values():
            plants = [i for i in idx if info[i][0] == "plant"]
            deep = [i for i in idx if info[i][0] == "deep"]
            seq = [(j + 1 - len(plants), i) for j, i in enumerate(plants)] + [(j + 1, i) for j, i in enumerate(deep)]
            for (k, i), nxt in zip(seq, seq[1:] + [None]):
                k = max(min(k, 8), -2)
                add(by_order[(period, k)], o[i][3], o[i][4])
                if nxt is not None and nxt[1] == i + 1 and o[i + 1][3] is not None and o[i + 1][0] > o[i][0]:
                    order_gap[(period, k)].append(
                        ((o[i + 1][0] + o[i + 1][3]) - (o[i][0] + o[i][3])) / (o[i + 1][0] - o[i][0]))

        for kind, members in runs(o, beat):
            g = "S" if kind == "slider" else "C"
            modes = {info[x][0] for x in members[1:]} - {"miss"}
            rmode = ("deep" if modes and modes <= {"deep", "plant"} else
                     "tap" if modes == {"tap"} else "mixed" if modes else "none")
            ivl = round(beat(o[members[0]][0]) / 4 / 20) * 20
            for pos, x in enumerate(members, 1):
                if g == "S" and pos == 1:
                    continue
                sp = o[x][5]
                sb = "<1.5" if sp < 1.5 else "1.5-3" if sp < 3 else "3+"
                add(run_cells[(period, rmode, len(members), pos, o[x][1], ivl, sb)][g], o[x][3], o[x][4])

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
            if r[0] in traced:
                rows.append([r[i] for i in ix])
        if rows:
            process(cur, rows)

    ser = lambda d: {"|".join(map(str, k)): v for k, v in d.items()}
    data = {"used": used, "by_event": ser(by_event), "by_order": ser(by_order),
            "order_gap": ser(order_gap), "runs": ser(run_cells)}
    json.dump(data, open(CACHE, "w"))
    return data


def row(c):
    n, s, ss, ng, t = c
    m = s / t if t else float("nan")
    sd = math.sqrt(max(ss / t - m * m, 0)) if t else float("nan")
    return f"n={n:6,}  mean {m:+6.2f}  SD {sd:5.1f}  non-Great {ng / n:5.1%}"


def main():
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/riding.txt"
    d = json.load(open(CACHE)) if os.path.exists(CACHE) and "--rebuild" not in sys.argv else build()
    out = io.StringIO()
    p = lambda *a: print(*a, file=out)
    p("traced replays used, by recorded thresholds:", dict(d["used"]))

    p("\n(1) circles by mode and riding event")
    for k in sorted(d["by_event"]):
        if not k.endswith("|miss"):
            p(f"  {k:52}", row(d["by_event"][k]))

    p("\n(2) circles by order in a deep span (<=0: plant presses before the latch arms)")
    p("    gap = next press interval / next object interval")
    for k in sorted(d["by_order"], key=lambda k: (k.split("|")[0], int(k.split("|")[1]))):
        g = d["order_gap"].get(k, [])
        gs = f"  gap median {statistics.median(g):.3f} SD {statistics.pstdev(g):.3f}" if len(g) > 5 else ""
        p(f"  {k:30}", row(d["by_order"][k]), gs)

    p("\n(3) slider-led vs circle-led 1/4 runs, within (length, position, kind, tempo, spacing) cells")
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    for k, g in d["runs"].items():
        f = k.split("|")
        a = agg[(f[0], f[1], "odd" if int(f[2]) % 2 else "even")]
        S, C = g["S"], g["C"]
        a["nS"] += S[0]
        a["nC"] += C[0]
        if S[0] and C[0]:
            wr = S[0] * C[0] / (S[0] + C[0])
            a["wr"] += wr
            a["ngS"] += wr * S[3] / S[0]
            a["ngC"] += wr * C[3] / C[0]
        if S[4] >= 2 and C[4] >= 2:
            mS, mC = S[1] / S[4], C[1] / C[4]
            w = S[4] * C[4] / (S[4] + C[4])
            a["w"] += w
            a["dm"] += w * (mS - mC)
            a["sdS"] += w * math.sqrt(max(S[2] / S[4] - mS * mS, 0))
            a["sdC"] += w * math.sqrt(max(C[2] / C[4] - mC * mC, 0))
    for k in sorted(agg):
        a = agg[k]
        if a["w"] and a["wr"]:
            p(f"  {' | '.join(k):48} S n={int(a['nS']):5,} C n={int(a['nC']):6,}  Δmean {a['dm'] / a['w']:+6.2f}"
              f"  SD {a['sdS'] / a['w']:4.1f}/{a['sdC'] / a['w']:<4.1f}"
              f"  non-Great {a['ngS'] / a['wr']:5.1%}/{a['ngC'] / a['wr']:5.1%}")
    text = out.getvalue()
    print(text)
    with open(out_path, "w") as f:
        f.write(text)


if __name__ == "__main__":
    main()
