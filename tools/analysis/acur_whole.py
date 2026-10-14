"""Attribution-corrected UR over the whole map, correcting only shifts the cursor caused.

acur.py measured the correction on stream circles alone. The metric as proposed is a
whole-map UR: a button-hand statistic, kept meaningful on maps with streams by removing
the circles whose hit error is really a cursor-pathing event. In a stream a shifted circle
is judged by a neighbour's tap, an error of about one interval that says nothing about the
button hand. Whether it should go depends on what started the chain (streams.py's origin):
  cursor   stray onto nothing (the cursor was on no circle), notelock (the cursor was on a
           later circle while an earlier one was open), cursor left early (ahead chains)
  button   dropped tap, double tap, stray outside the window (hovered a circle, mistimed)
  other    no origin found
Only anchored chains (a correctly-placed tap earlier in the run) are corrected: a run that
starts shifted reads equally as steady lateness.

Per replay, UR = 10 * SD of hit error in ms, every judged circle and slider head
(build/clicks.csv, map time):
  raw           as lazer counts it
  drop-cursor   cursor-origin shifted circles removed (the proposal)
  realign-cur   those circles re-scored against the circle their tap was meant for
  drop-stray    only the firmest cursor calls (stray onto nothing, notelock): "cursor left
                early" knows the tap was nearer the earlier circle, not whether the cursor
                was fast or the tap slow
  drop-all      every anchored shifted circle removed, whatever the origin
  trim-k        as many circles as drop-cursor removed, largest |error| first (the control)
  add-miss      raw plus misses whose button tap is recovered by time (see `added` below)
  both          drop-cursor plus add-miss: the button hand with the cursor taken out both ways

    python3 tools/analysis/acur_whole.py [--out build/acur-whole.txt]
"""
from __future__ import annotations

import bisect
import collections
import csv
import json
import os
import pickle
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
import streams as S                 # noqa: E402
from acur import spearman, ur       # noqa: E402
from recovery import input_regime   # noqa: E402

VARIANTS = ("raw", "drop-cursor", "drop-stray", "realign-cur", "drop-all", "trim-k", "add-miss", "both")
CURSOR, BUTTON, chains = S.CURSOR, S.BUTTON, S.chains
MIN_N = 200


def main(out_path):
    runs = pickle.load(open(S.CACHE, "rb"))
    presses = S.load_presses({r for r, _ in runs})
    md5 = {os.path.basename(e["Path"]): e["BeatmapMd5"] for e in json.load(open("build/corpus.json"))}

    # replay -> {round(start time): (origin class, realigned error or None)}
    marked = collections.defaultdict(dict)
    origins = collections.Counter()
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
        r = S.classify(objects, [e[3] for e in events], [e[4] for e in events], window, snap, beat)
        if r["verdict"] != "ok":
            continue
        r["interval"] = interval
        times = [p[0] for p in window]
        meant = {i: times[j] - objects[i] for i, j in r["pairs"]
                 if i is not None and j is not None}
        for origin, idx in chains(objects, window, r):
            cls = "cursor" if origin in CURSOR else "button" if origin in BUTTON else "other"
            if origin == "cursor left early":
                cls = "cursor-left"
            for i in idx:
                if events[i][3] == "Miss" or events[i][4] is None:
                    continue
                origins[origin] += 1
                marked[replay][round(objects[i])] = (cls, meant.get(i))

    # Every judged circle and slider head of the player's replays that have stream runs.
    want = {r for r, _ in runs}
    per = collections.defaultdict(list)     # replay -> [(error, class or None, realigned)]
    every = collections.defaultdict(list)   # replay -> [(start, meh, missed)], every clickable
    with open("build/clicks.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            replay = row["replay"]
            if replay not in want or row["kind"] not in ("circle", "slider"):
                continue
            missed = row["result"] == "Miss"
            every[replay].append((float(row["startTime"]), float(row["meh"]), missed))
            if missed or not row["pressTime"]:
                continue
            t = float(row["startTime"])
            cls, re_ = marked[replay].get(round(t), (None, None)) if replay in marked else (None, None)
            per[replay].append((float(row["pressTime"]) - t, cls, re_, float(row["meh"])))

    # Misses added back. A press that judged nothing (hovered nothing, hovered a circle
    # outside its window, refused by notelock) is the button hand's tap for the object it is
    # nearest in time, if that object missed and the press is inside its meh window. Time,
    # not cursor: Clicks.cs's stray attribution follows the cursor's last circle, which is
    # the hand this is trying to factor out. One press per miss, the nearest.
    added = {}                              # replay -> [(error, meh, outcome)]
    for replay, objs in every.items():
        objs.sort()
        starts = [o[0] for o in objs]
        best = {}
        for pt, outcome, _ in presses.get(replay, ()):
            if outcome == "hit":
                continue
            k = bisect.bisect_left(starts, pt)
            near = min((q for q in (k - 1, k) if 0 <= q < len(objs)), key=lambda q: abs(starts[q] - pt))
            t, meh, missed = objs[near]
            if missed and abs(pt - t) <= meh and (near not in best or abs(pt - t) < abs(best[near][0])):
                best[near] = (pt - t, meh, outcome)
        added[replay] = list(best.values())

    rows = []
    for replay, xs in per.items():
        if len(xs) < MIN_N:
            continue
        errs = [x[0] for x in xs]
        back = [a[0] for a in added.get(replay, ())]
        misses = sum(o[2] for o in every[replay])
        cur = ("cursor", "cursor-left")
        kc = sum(x[1] in cur for x in xs)
        rows.append({
            "replay": replay, "md5": md5.get(replay), "day": replay[-21:-11], "stamp": replay[-21:-5],
            "n": len(xs), "kc": kc, "ka": sum(x[1] is not None for x in xs),
            "raw": ur(errs),
            "drop-cursor": ur([x[0] for x in xs if x[1] not in cur]),
            "drop-stray": ur([x[0] for x in xs if x[1] != "cursor"]),
            "realign-cur": ur([x[2] if x[1] in cur else x[0] for x in xs
                               if not (x[1] in cur and x[2] is None)]),
            "drop-all": ur([x[0] for x in xs if x[1] is None]),
            "trim-k": ur(sorted(errs, key=abs)[:len(errs) - kc]),
            "add-miss": ur(errs + back),
            "both": ur([x[0] for x in xs if x[1] not in cur] + back),
            "kb": len(back), "misses": misses,
        })

    lines = []
    p = lines.append
    tot = sum(origins.values())
    p(f"shifted circles in anchored chains, by origin of the chain ({tot}):")
    for o, n in origins.most_common():
        cls = "cursor" if o in CURSOR else "button" if o in BUTTON else "other"
        p(f"  {o:<18} {cls:<7} {n:7}  {100 * n / tot:5.1f}%")

    p(f"\nreplays with >= {MIN_N} judged objects: {len(rows)}; cursor-shifted share of all "
      f"judged objects {100 * sum(r['kc'] for r in rows) / sum(r['n'] for r in rows):.2f}%")
    hit = [r for r in rows if r["kc"]]
    p(f"replays with any cursor-origin shift: {len(hit)}")

    p("\nwhole-map UR, median: all replays / replays with a cursor shift / top decile by share")
    top = sorted(rows, key=lambda r: r["kc"] / r["n"])[-len(rows) // 10:]
    for v in VARIANTS:
        p(f"  {v:<12} {statistics.median(r[v] for r in rows):7.1f}"
          f"{statistics.median(r[v] for r in hit):9.1f}{statistics.median(r[v] for r in top):9.1f}")
    dl = [r["raw"] - r["drop-cursor"] for r in hit]
    dl.sort()
    p(f"  raw - drop-cursor on shifted replays: median {dl[len(dl) // 2]:.1f}, "
      f"p90 {dl[int(.9 * len(dl))]:.1f}, max {dl[-1]:.1f} UR "
      f"(top-decile share {100 * min(r['kc'] / r['n'] for r in top):.1f}%+ of objects)")

    p("\nSpearman across replays: against raw, and against trim-k (the count-matched trim)")
    for v in VARIANTS[1:]:
        p(f"  {v:<12} vs raw {spearman([r['raw'] for r in rows], [r[v] for r in rows]):.3f}"
          f"   vs trim-k {spearman([r['trim-k'] for r in rows], [r[v] for r in rows]):.3f}")

    def pairs_of(same_map):
        out = []
        if same_map:
            by = collections.defaultdict(list)
            for r in rows:
                if r["md5"]:
                    by[(r["md5"], r["day"])].append(r)
            for plays in by.values():
                plays.sort(key=lambda r: r["stamp"])
                out += list(zip(plays, plays[1:]))
        else:
            by = collections.defaultdict(list)
            for r in rows:
                by[r["day"]].append(r)
            for plays in by.values():
                plays.sort(key=lambda r: r["stamp"])
                out += [(a, b) for a, b in zip(plays, plays[1:]) if a["md5"] != b["md5"]]
        return out

    rng = random.Random(1)
    for label, same in (("same map, same day, consecutive", True),
                        ("different maps, consecutive in a day", False)):
        pairs = pairs_of(same)
        p(f"\ntest-retest, {label}: {len(pairs)} pairs")
        for v in VARIANTS:
            rho = spearman([a[v] for a, _ in pairs], [b[v] for _, b in pairs])
            boots = sorted(spearman([a[v] for a, _ in s], [b[v] for _, b in s])
                           for s in ([rng.choice(pairs) for _ in pairs] for _ in range(200)))
            p(f"  {v:<12} rho {rho:.3f}  [{boots[5]:.3f}, {boots[194]:.3f}]")
        p(f"  cursor-shift share  rho {spearman([a['kc'] / a['n'] for a, _ in pairs], [b['kc'] / b['n'] for _, b in pairs]):.3f}")

    # The direct test of the intent. Two plays of one map differ in how many cursor shifts
    # they had; a button-hand statistic should not move with that difference. Raw will;
    # a correction that removes the contamination and nothing else should not; a blind
    # trim that also removes genuine button error should overshoot into the negative.
    pairs = [(a, b) for a, b in pairs_of(True) if a["kc"] != b["kc"]]
    dk = [b["kc"] / b["n"] - a["kc"] / a["n"] for a, b in pairs]
    p(f"\ncontamination: same-map pairs differing in cursor shifts ({len(pairs)}), "
      "Spearman of the UR change against the change in cursor-shifted share")
    for v in VARIANTS:
        p(f"  {v:<12} {spearman(dk, [b[v] - a[v] for a, b in pairs]):+.3f}")
    p("  and across replays, Spearman of UR against cursor-shifted share")
    for v in VARIANTS:
        p(f"  {v:<12} {spearman([r['kc'] / r['n'] for r in rows], [r[v] for r in rows]):+.3f}")

    # Are the recovered presses taps at all? Hits sit in a peak around the player's mean;
    # flailing presses would spread flat across the window (SD = meh / sqrt 3 = 0.577 meh).
    used = {r["replay"] for r in rows}
    back = [a for r in used for a in added.get(r, ())]
    p(f"\nmisses added back: {len(back)} of {sum(r['misses'] for r in rows)} "
      f"({100 * len(back) / max(1, sum(r['misses'] for r in rows)):.1f}%), by what the press did: "
      + ", ".join(f"{o} {n}" for o, n in collections.Counter(a[2] for a in back).most_common()))
    edges = (-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0)
    rel = [a[0] / a[1] for a in back]
    c = collections.Counter(bisect.bisect(edges, x) for x in rel)
    p("  error / meh window, added-back presses:")
    for b in range(1, len(edges)):
        p(f"    {edges[b - 1]:+.2f}..{edges[b]:+.2f}  {100 * c[b] / max(1, len(rel)):5.1f}%")
    p(f"  added-back: mean {statistics.fmean(a[0] for a in back):+.1f} ms, SD {statistics.pstdev(a[0] for a in back):.1f} ms, "
      f"SD/meh {statistics.pstdev(rel):.3f} (flat would be 0.577)")
    hits = [x for r in used for x in per[r]]
    p(f"  hits, same replays: mean {statistics.fmean(x[0] for x in hits):+.1f} ms, SD "
      f"{statistics.pstdev(x[0] for x in hits):.1f} ms, SD/meh {statistics.pstdev(x[0] / x[3] for x in hits):.3f}")
    hc = collections.Counter(bisect.bisect(edges, x[0] / x[3]) for x in hits)
    p("  error / meh window, side by side (added-back % / hits %):  " + "  ".join(
        f"{100 * c[b] / max(1, len(rel)):.1f}/{100 * hc[b] / len(hits):.1f}" for b in range(1, len(edges))))

    pairs = [(a, b) for a, b in pairs_of(True) if a["kb"] / a["n"] != b["kb"] / b["n"]]
    db = [b["kb"] / b["n"] - a["kb"] / a["n"] for a, b in pairs]
    p(f"\nmiss contamination: same-map pairs differing in recovered misses ({len(pairs)}), "
      "Spearman of the UR change against the change in recovered-miss share")
    for v in ("raw", "add-miss", "drop-cursor", "both"):
        p(f"  {v:<12} {spearman(db, [b[v] - a[v] for a, b in pairs]):+.3f}")

    p("\ninput regime: median whole-map UR")
    for era in ("OVERLAP-1", "EXCLUSIVE", "OVERLAP-2"):
        sel = [r for r in rows if input_regime(r["day"]) == era]
        if sel:
            p(f"  {era:<10} n {len(sel):5}  cursor-shifted {100 * sum(r['kc'] for r in sel) / sum(r['n'] for r in sel):4.2f}%  "
              + "  ".join(f"{v} {statistics.median(r[v] for r in sel):6.1f}" for v in VARIANTS))

    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/acur-whole.txt")
