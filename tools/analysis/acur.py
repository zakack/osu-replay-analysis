"""Attribution-corrected UR: does dropping shifted stream circles measure anything?

The proposal: inside a tapped stream, a circle judged by a tap meant for a neighbour
(streams.py's pushback/ahead, judge in next/prev/far/extra) carries a hit error of about one
tap interval that is not a timing measurement at all, so drop it from UR the way a miss is
dropped. Dropping the fattest tail lowers UR by construction, so that is not evidence. This
asks whether what is left and what is removed carry separate information.

Stream circles only (build/streams.pickle), the player's aligned runs. Per replay, UR =
10 * SD of hit error in ms (map time; the rate cancels inside a replay and in same-map pairs):
  raw        every judged circle, as lazer counts it
  drop       shifted circles removed (the proposal)
  realign    shifted circles re-scored against the circle their tap was meant for
  trim       |error| > 0.5 interval removed, attribution ignored (the winsorize control)
  trim-k     the same number of circles as `drop` removed, largest |error| first
Phase-shift and unalignable runs are left uncorrected and counted.

Checks: (1) rank preservation across replays; (2) drop against both trims, per replay;
(3) the dropped errors in units of interval, against the kept tail; (4) test-retest, the
Spearman between consecutive same-day plays of one beatmap, and the input-regime split.

    python3 tools/analysis/acur.py [--out build/acur.txt]
"""
from __future__ import annotations

import bisect
import collections
import json
import math
import os
import pickle
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
import streams as S                 # noqa: E402
from recovery import input_regime   # noqa: E402

SHIFTED = ("next", "prev", "far", "extra")
VARIANTS = ("raw", "drop", "realign", "trim", "trim-k")
ANCHORED = "--anchored" in sys.argv
MIN_N = 40          # judged stream circles a replay needs before its UR is used


def ur(xs):
    return 10 * statistics.pstdev(xs) if len(xs) >= 2 else None


def spearman(a, b):
    def rank(v):
        order = sorted(range(len(v)), key=v.__getitem__)
        r = [0.0] * len(v)
        for k, i in enumerate(order):
            r[i] = k
        return r
    ra, rb = rank(a), rank(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return num / math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))


def main(out_path):
    runs = pickle.load(open(S.CACHE, "rb"))
    presses = S.load_presses({r for r, _ in runs})
    md5 = {os.path.basename(e["Path"]): e["BeatmapMd5"] for e in json.load(open("build/corpus.json"))}

    # Per replay: lists of (error, interval, shifted, realigned error or None).
    per = collections.defaultdict(list)
    verdicts = collections.Counter()
    ends = collections.Counter()
    for replay, events in runs:
        ps = presses.get(replay)
        if ps is None:
            continue
        objects = [e[0] for e in events]
        beat, snap = events[0][1], events[0][2]
        interval = snap * beat
        lo = bisect.bisect_left(ps, (objects[0] - interval,))
        hi = bisect.bisect_right(ps, (objects[-1] + interval, "~"))
        window = ps[lo:hi]
        results, errors = [e[3] for e in events], [e[4] for e in events]
        r = S.classify(objects, results, errors, window, snap, beat)
        verdicts[r["verdict"]] += 1
        meant = {}
        if r["verdict"] == "ok":
            times = [p[0] for p in window]
            meant = {i: times[j] - objects[i] for i, j in r["pairs"]
                     if i is not None and j is not None}
            # How the shift chains end, for the miss-vs-Meh consistency argument.
            j = r["judge"]
            k = 0
            while k < len(objects):
                d = j[k]
                if d not in ("next", "prev") or "self" not in j[:k]:
                    k += 1
                    continue
                while k < len(objects) and j[k] == d:
                    k += 1
                if k >= len(objects):
                    ends["not re-synced"] += 1
                elif j[k] == "none":
                    ends["expired (miss, outside UR)"] += 1
                elif j[k] == "extra":
                    ends["extra tap (Mehs, inside UR)"] += 1
                else:
                    ends["other"] += 1
        for i, (res, err) in enumerate(zip(results, errors)):
            if res == "Miss" or err is None:
                continue
            shifted = r["verdict"] == "ok" and r["judge"][i] in SHIFTED
            if ANCHORED and shifted:
                # Only chains with a correctly-placed tap before them in the run: a run that
                # starts on a neighbour's tap reads equally as steady lateness (streams.py).
                shifted = "self" in r["judge"][:i]
            per[replay].append((err, interval, shifted, meant.get(i)))

    rows = []
    dropped_iv, kept_iv = [], []
    for replay, xs in per.items():
        if len(xs) < MIN_N:
            continue
        errs = [x[0] for x in xs]
        keep = [x[0] for x in xs if not x[2]]
        k = sum(x[2] for x in xs)
        realigned = [x[3] if x[2] and x[3] is not None else x[0] for x in xs if not (x[2] and x[3] is None)]
        trim = [x[0] for x in xs if abs(x[0]) <= 0.5 * x[1]]
        trimk = sorted(errs, key=abs)[:len(errs) - k]
        for x in xs:
            (dropped_iv if x[2] else kept_iv).append(x[0] / x[1])
        rows.append({"replay": replay, "md5": md5.get(replay), "n": len(xs), "k": k,
                     "day": replay[-21:-11], "stamp": replay[-21:-5],
                     "raw": ur(errs), "drop": ur(keep), "realign": ur(realigned),
                     "trim": ur(trim), "trim-k": ur(trimk)})
    rows = [r for r in rows if all(r[v] is not None for v in VARIANTS)]

    lines = []
    p = lines.append
    tot = sum(verdicts.values())
    p(f"stream runs {tot}: aligned {verdicts['ok']}, phase-shift {verdicts['phase-shift']}, "
      f"unalignable {verdicts['unalignable']} (left uncorrected)")
    p(f"replays with >= {MIN_N} judged stream circles: {len(rows)}; "
      f"with any shifted circle: {sum(r['k'] > 0 for r in rows)}; "
      f"shifted share of circles {100 * sum(r['k'] for r in rows) / sum(r['n'] for r in rows):.2f}%")

    p("\nshift chain endings (pushback/ahead, anchored):")
    for e, n in ends.most_common():
        p(f"  {e:<30} {n:7}")

    p("\nstream UR, median over replays (all / replays with a shift):")
    hit = [r for r in rows if r["k"] > 0]
    for v in VARIANTS:
        p(f"  {v:<8} {statistics.median(r[v] for r in rows):7.1f}   {statistics.median(r[v] for r in hit):7.1f}")

    p("\n(1) Spearman against raw across replays, and (2) against drop:")
    for v in VARIANTS[1:]:
        p(f"  {v:<8} vs raw {spearman([r['raw'] for r in rows], [r[v] for r in rows]):.3f}"
          f"   vs drop {spearman([r['drop'] for r in rows], [r[v] for r in rows]):.3f}")
    p("  on shifted replays only:")
    for v in VARIANTS[1:]:
        p(f"  {v:<8} vs raw {spearman([r['raw'] for r in hit], [r[v] for r in hit]):.3f}"
          f"   vs drop {spearman([r['drop'] for r in hit], [r[v] for r in hit]):.3f}")
    diff = [abs(r["drop"] - r["trim"]) for r in hit]
    p(f"  |drop - trim| on shifted replays: median {statistics.median(diff):.1f}, "
      f"p90 {sorted(diff)[int(0.9 * len(diff))]:.1f} UR")

    p("\n(3) error / interval: dropped circles vs kept circles")
    edges = (-1.5, -1.0, -0.75, -0.5, -0.25, 0.25, 0.5, 0.75, 1.0, 1.5)
    def hist(v):
        c = collections.Counter(bisect.bisect(edges, x) for x in v)
        return [100 * c[b] / max(1, len(v)) for b in range(len(edges) + 1)]
    labels = ["<-1.5"] + [f"{a}..{b}" for a, b in zip(edges, edges[1:])] + [">1.5"]
    hd, hk = hist(dropped_iv), hist(kept_iv)
    p(f"  {'bin':<14}{'dropped %':>10}{'kept %':>10}   (n {len(dropped_iv)} / {len(kept_iv)})")
    for lab, a, b in zip(labels, hd, hk):
        p(f"  {lab:<14}{a:10.2f}{b:10.3f}")

    p("\n(4a) test-retest: consecutive same-day plays of one beatmap, Spearman of UR")
    by = collections.defaultdict(list)
    for r in rows:
        if r["md5"]:
            by[(r["md5"], r["day"])].append(r)
    pairs = []
    for plays in by.values():
        plays.sort(key=lambda r: r["stamp"])
        pairs += list(zip(plays, plays[1:]))
    p(f"  pairs {len(pairs)}")
    rng = random.Random(1)
    for v in VARIANTS:
        rho = spearman([a[v] for a, _ in pairs], [b[v] for _, b in pairs])
        boots = []
        for _ in range(200):
            s = [rng.choice(pairs) for _ in pairs]
            boots.append(spearman([a[v] for a, _ in s], [b[v] for _, b in s]))
        boots.sort()
        p(f"  {v:<8} rho {rho:.3f}  [{boots[5]:.3f}, {boots[194]:.3f}]")
    sp = [(a, b) for a, b in pairs if a["k"] or b["k"]]
    p(f"  pairs where either play shifted: {len(sp)}")
    for v in VARIANTS:
        p(f"  {v:<8} rho {spearman([a[v] for a, _ in sp], [b[v] for _, b in sp]):.3f}")
    p("  shift count itself, play to play (is slipping a trait?): "
      f"rho {spearman([a['k'] / a['n'] for a, _ in pairs], [b['k'] / b['n'] for _, b in pairs]):.3f}")

    p("\n(4b) input regime: median stream UR and shifted share")
    for era in ("OVERLAP-1", "EXCLUSIVE", "OVERLAP-2"):
        sel = [r for r in rows if input_regime(r["day"]) == era]
        if not sel:
            continue
        share = 100 * sum(r["k"] for r in sel) / sum(r["n"] for r in sel)
        p(f"  {era:<10} n {len(sel):5}  shifted {share:5.2f}%  " +
          "  ".join(f"{v} {statistics.median(r[v] for r in sel):6.1f}" for v in VARIANTS))

    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv
     else "build/acur-anchored.txt" if ANCHORED else "build/acur.txt")
