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
    """Yield (direction, end, taps_to_recover, lag_ms, lag_intervals, start_t, end_t) per
    shift in a run. Times are map time; the caller divides by the rate.

    Ends, read off the first circle after the chain (k) and the chain's last circle:
      pushback  "extra tap"      k was judged by an unmatched press: the panic tap
                "expired"        k was judged by nothing: its window ran out while the
                                 shifted tap was still on the circle before, it missed, and
                                 the next tap lands on its own circle. Re-sync bought with a miss.
      ahead     "dropped tap"    no press was meant for the last shifted circle or for k
                "expired"        k was judged by nothing
      both      "not re-synced"  the chain reached the end of the run
                "other"          anything else

    Start of a shift: pushback, the tap meant for the first shifted circle (it strayed or
    was refused), or that circle's time if no tap was meant for it; ahead, the circle
    before the chain, the one whose tap went to its successor. End: the recovery tap for
    "extra tap", the circle whose tap was left out for "dropped tap", the expiring circle's
    time for "expired"."""
    interval = snap * beat
    r = S.classify(objects, ["Great"] * len(objects), [None] * len(objects), presses, snap, beat)
    if r["verdict"] != "ok":
        return
    judge, tap = r["judge"], r["tap"]
    times = [p[0] for p in presses]
    meant = {i: times[j] for i, j in r["pairs"] if i is not None and j is not None}
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
        t0 = meant.get(q, objects[q]) if d == "next" else objects[q - 1]
        if k >= n:
            yield name, "not re-synced", count, None, None, t0, None
        elif judge[k] == "none":
            yield name, "expired", count, None, None, t0, objects[k]
        elif d == "next" and judge[k] == "extra":
            t = by_target.get(round(objects[k], 1))
            lag = None if t is None else t - objects[k]
            yield name, "extra tap", count, lag, None if lag is None else lag / interval, t0, t
        elif d == "prev" and (tap[last] == "dropped" or tap[k] == "dropped"):
            yield name, "dropped tap", count, None, None, t0, objects[last] if tap[last] == "dropped" else objects[k]
        else:
            yield name, "other", count, None, None, t0, None


def rates() -> dict[str, float]:
    """Replay -> playback rate, from onset.csv, which carries lazer's own value per row."""
    import csv
    out, last = {}, None
    with open("build/onset.csv", newline="") as fh:
        rows = csv.reader(fh)
        i = next(rows).index("rate")
        for row in rows:
            if row[0] != last:
                out[row[0]] = float(row[i] or 1)
                last = row[0]
    return out


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

    rate = rates()
    rows = []   # (direction, end, count, lag_ms, lag_int, era, regime, md5, interval_ms, ms, month)
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
        rr = rate.get(replay, 1.0)
        for d, end, count, lag, lagi, t0, t1 in shifts(objects, ps[lo:hi], snap, beat):
            ms = None if t1 is None else (t1 - t0) / rr
            rows.append((d, end, count, None if lag is None else lag / rr, lagi, hitsound_era(day),
                         input_regime(day), md5.get(replay), interval / rr, ms, day[:7]))

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
            ms = sorted(r[9] for r in rs if r[1] == recover[d] and r[9] is not None)
            if len(ms) >= 4:
                mq = statistics.quantiles(ms, n=4)
                line += f"  ms to recover {statistics.median(ms):.0f} (IQR {mq[0]:.0f}-{mq[2]:.0f})"
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

    # The reaction test. If re-syncing is a reaction to hearing the slip, the time from the
    # slip to the recovery tap is roughly fixed and the tap count grows as taps get faster.
    # If the tap count is fixed, the recovery is counted out in taps, not reacted to.
    p("\nreaction test: recoveries by real tap interval (ms per tap, rate applied)")
    edges = (0, 55, 65, 75, 85, 100, 120, 1000)
    for d in ("pushback", "ahead"):
        p(f"  {d} via {recover[d]}:")
        for lo_, hi_ in zip(edges, edges[1:]):
            sel = [r for r in rows if r[0] == d and r[1] == recover[d] and r[9] is not None and lo_ <= r[8] < hi_]
            if len(sel) < 20:
                continue
            taps = sorted(r[2] for r in sel)
            ms = sorted(r[9] for r in sel)
            p(f"    {lo_:>3}-{hi_:<4} ms/tap  n={len(sel):5}  taps {statistics.median(taps):.0f}  ms {statistics.median(ms):4.0f}"
              f"  (ms IQR {statistics.quantiles(ms, n=4)[0]:.0f}-{statistics.quantiles(ms, n=4)[2]:.0f})")
    for d in ("pushback", "ahead"):
        sel = [r for r in rows if r[0] == d and r[1] == recover[d] and r[9] is not None]
        if len(sel) > 50:
            xs = [r[8] for r in sel]
            for label, ys in (("taps", [r[2] for r in sel]), ("ms", [r[9] for r in sel])):
                mx, my = statistics.fmean(xs), statistics.fmean(ys)
                b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
                p(f"  {d} slope of {label}-to-recover on ms/tap: {b:+.3f}  "
                  f"(reaction predicts taps falling with ms/tap and ms flat; counting predicts taps flat)")

    # A deliberate change of policy shows up as a step by month, independent of the eras.
    p("\nby month: pushback recovery by extra tap, and how long the recoveries take")
    p("  month     era      n    extra-tap%   taps  ms     ahead n  dropped%")
    for month in sorted({r[10] for r in rows}):
        pb = [r for r in rows if r[10] == month and r[0] == "pushback"]
        ah = [r for r in rows if r[10] == month and r[0] == "ahead"]
        if len(pb) < 30:
            continue
        ok = [r for r in pb if r[1] == "extra tap"]
        ms = [r[9] for r in ok if r[9] is not None]
        p(f"  {month}  {hitsound_era(month + '-15'):<8} {len(pb):5}  {100 * len(ok) / len(pb):6.1f}%"
          f"    {statistics.median([r[2] for r in ok]) if ok else float('nan'):4.0f}  {statistics.median(ms) if ms else float('nan'):4.0f}"
          f"   {len(ah):6}  {100 * sum(r[1] == 'dropped tap' for r in ah) / max(1, len(ah)):6.1f}%")

    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/recovery.txt")
