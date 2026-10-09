"""How a shifted stream gets back on the grid, and whether hearing your own presses helps.

A shift (streams.py) is a stretch of circles each judged by a neighbour's tap: pushback, the
next circle's tap, after a stray or dropped one; ahead, the previous circle's tap, after a
double or a circle the cursor left early. It ends only when the count of taps and circles
comes back into step, and each direction has one way to do that:
  pushback  an extra tap, a press aligned to no circle: one tap more than the circles
  ahead     a dropped tap, a circle no press was meant for: one tap fewer
Or the shift can lapse on its own: a shifted circle's window runs out while its tap is still
on the circle before, so it misses and the next tap meets its own circle ("expired", a
re-sync bought with a miss). A shift that reaches the end of the run never re-synced.
Anything else is "other".

Per shift: how it ended, taps to recover (the shifted circles before the resync) and, for an
extra tap, its lag to the nearest circle in ms and in tap intervals.

The question behind it is the hitsound. Lazer plays a hit sound only on a judged press, so a
tap that judged nothing is silent and the player cannot hear that the stream slipped. A
press sound on every press (doubletap, later evclack) makes it audible. The eras alternate
(setup-timeline.csv rows 14-16), which is what guards against a skill trend:
  CUE-1     before 2026-05-10        lazer's hit sound only: an eaten tap is silent
  NOCUE-1   2026-05-10 .. 05-20      own press sound on every press
  CUE-2     2026-05-20 .. 07-24      lazer's sound back (alongside the press sound; see row
                                     15, it is CUE by the coordinator's definition)
  NOCUE-2   from 2026-07-24          own press sound only
Input regime is a second split (rows 13 and 18), because exclusive presses change what a
double tap even is: OVERLAP-1 before 2025-12-15, EXCLUSIVE 2026-01-03 .. 08-29, OVERLAP-2
from 2026-09-05, transitions dropped. Dates are the replay filename stamps, local time.

CIs are a bootstrap over beatmaps.

    python3 tools/analysis/recovery.py [--out build/recovery.txt]
"""
from __future__ import annotations

import bisect
import collections
import json
import os
import pickle
import random
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
import streams as S  # noqa: E402

STAMP = re.compile(r"\((\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})\)\.osr$")
BOOT = 500


def hitsound_era(day: str) -> str:
    if day < "2026-05-10":
        return "CUE-1"
    if day < "2026-05-20":
        return "NOCUE-1"
    if day < "2026-07-24":
        return "CUE-2"
    return "NOCUE-2"


def input_regime(day: str) -> str | None:
    if day < "2025-12-15":
        return "OVERLAP-1"
    if "2026-01-03" <= day < "2026-08-29":
        return "EXCLUSIVE"
    if day >= "2026-09-05":
        return "OVERLAP-2"
    return None


def shifts(objects, presses, snap, beat):
    """Yield (direction, end, taps_to_recover, lag_ms, lag_intervals) per shift in a run.

    Ends, read off the first circle after the chain (k) and the chain's last circle:
      pushback  "extra tap"      k was judged by an unmatched press: the panic tap
                "expired"        k was judged by nothing: its window ran out while the
                                 shifted tap was still on the circle before, it missed, and
                                 the next tap lands on its own circle. Re-sync bought with a miss.
      ahead     "dropped tap"    no press was meant for the last shifted circle or for k
                "expired"        k was judged by nothing
      both      "not re-synced"  the chain reached the end of the run
                "other"          anything else"""
    interval = snap * beat
    r = S.classify(objects, ["Great"] * len(objects), [None] * len(objects), presses, snap, beat)
    if r["verdict"] != "ok":
        return
    judge, tap = r["judge"], r["tap"]
    by_target = {}
    for t, outcome, target in presses:
        if outcome == "hit" and target is not None:
            by_target.setdefault(round(target, 1), t)
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
        last = k - 1
        count = last - q + 1
        name = "pushback" if d == "next" else "ahead"
        if k >= n:
            yield name, "not re-synced", count, None, None
        elif judge[k] == "none":
            yield name, "expired", count, None, None
        elif d == "next" and judge[k] == "extra":
            t = by_target.get(round(objects[k], 1))
            lag = None if t is None else t - objects[k]
            yield name, "extra tap", count, lag, None if lag is None else lag / interval
        elif d == "prev" and (tap[last] == "dropped" or tap[k] == "dropped"):
            yield name, "dropped tap", count, None, None
        else:
            yield name, "other", count, None, None


def boot_rate(items, key_ok):
    """Recovery share with a bootstrap over beatmaps. items: list of (md5, ok bool)."""
    by = collections.defaultdict(lambda: [0, 0])
    for m, ok in items:
        by[m][0] += ok
        by[m][1] += 1
    maps = list(by.values())
    if not maps:
        return float("nan"), (float("nan"), float("nan"))
    rng = random.Random(0)
    draws = []
    for _ in range(BOOT):
        s = c = 0
        for a, b in rng.choices(maps, k=len(maps)):
            s += a
            c += b
        draws.append(s / c)
    draws.sort()
    total = sum(a for a, _ in maps) / sum(b for _, b in maps)
    return total, (draws[int(0.025 * BOOT)], draws[int(0.975 * BOOT) - 1])


def main(out_path: str | None) -> None:
    with open(S.CACHE, "rb") as fh:
        runs = pickle.load(fh)
    presses = S.load_presses({r for r, _ in runs})
    md5 = {os.path.basename(e["Path"]): e["BeatmapMd5"] for e in json.load(open("build/corpus.json"))}

    rows = []   # (direction, end, count, lag_ms, lag_int, era, regime, md5)
    for replay, events in runs:
        ps = presses.get(replay)
        m = STAMP.search(replay)
        if ps is None or m is None:
            continue
        day = m.group(1)
        objects = [e[0] for e in events]
        beat, snap = events[0][1], events[0][2]
        interval = snap * beat
        lo = bisect.bisect_left(ps, (objects[0] - interval,))
        hi = bisect.bisect_right(ps, (objects[-1] + interval, "~"))
        for s in shifts(objects, ps[lo:hi], snap, beat):
            rows.append(s + (hitsound_era(day), input_regime(day), md5.get(replay)))

    lines = []
    p = lines.append
    recover = {"pushback": "extra tap", "ahead": "dropped tap"}

    def block(title, sel):
        p(title)
        for d in ("pushback", "ahead"):
            rs = [r for r in sel if r[0] == d]
            if not rs:
                continue
            ends = collections.Counter(r[1] for r in rs)
            rate, (lo, hi) = boot_rate([(r[7], r[1] == recover[d]) for r in rs], None)
            counts = sorted(r[2] for r in rs if r[1] == recover[d])
            q = statistics.quantiles(counts, n=4) if len(counts) >= 4 else [float("nan")] * 3
            line = (f"  {d:<9} n={len(rs):6}  by {recover[d]} {100 * rate:5.1f}% [{100 * lo:.1f},{100 * hi:.1f}]"
                    f"  expired {100 * ends['expired'] / len(rs):4.1f}%  never {100 * ends['not re-synced'] / len(rs):4.1f}%"
                    f"  other {100 * ends['other'] / len(rs):4.1f}%"
                    f"  taps to recover {statistics.median(counts) if counts else float('nan'):.0f} (IQR {q[0]:.0f}-{q[2]:.0f})")
            if d == "pushback":
                lags = sorted(r[3] for r in rs if r[3] is not None)
                ints = sorted(r[4] for r in rs if r[4] is not None)
                if lags:
                    line += f"  extra-tap lag {statistics.median(lags):+.0f} ms ({statistics.median(ints):+.2f} int)"
            p(line)

    block("all tapped stream runs", rows)
    p("\nby hitsound era (CUE: hit sound only on judged presses; NOCUE: own sound on every press)")
    for era in ("CUE-1", "NOCUE-1", "CUE-2", "NOCUE-2"):
        block(f"{era}", [r for r in rows if r[5] == era])
    p("\nby input regime")
    for reg in ("OVERLAP-1", "EXCLUSIVE", "OVERLAP-2"):
        block(f"{reg}", [r for r in rows if r[6] == reg])
    p("\nhitsound era within EXCLUSIVE only (input held fixed)")
    for era in ("CUE-1", "NOCUE-1", "CUE-2", "NOCUE-2"):
        block(f"EXCLUSIVE x {era}", [r for r in rows if r[6] == "EXCLUSIVE" and r[5] == era])

    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/recovery.txt")
