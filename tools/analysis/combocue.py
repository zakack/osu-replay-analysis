"""Is pushback recovery cued by sound or by sight? The combo-break sound as the instrument.

Lazer shows a miss marker on every miss but plays the combo-break sound only when combo
drops to 0 from more than 20, or on the play's first break (AlwaysPlayFirstComboBreak;
ComboEffects.cs:59). build/combo.csv (`ora combo`) carries, for every tap miss, combo just
before it over the simulator's ordered judgements (slider nested objects included) and
whether the sound played.

Per pushback shift (recovery.py's definition), the first tap miss on a circle from the
first shifted circle through the circle after the chain:
  SOUND    that miss played the combo-break sound
  SILENT   it did not (combo 20 or less, and not the first break)
  NO-MISS  no tap miss there
Per class: the share recovered by an extra tap, the share that ended by the shifted
circle expiring, and for extra-tap recoveries after a miss, the time from the miss's
judgement to the recovery tap, in real ms. Split at 2026-05-10, where the recovery policy
changed (recovery.py's monthly series). CIs by bootstrap over beatmaps.

Read with care: inside a pushback chain every circle is hit by its neighbour's tap, so the
first miss is usually the expiring circle, and that miss *is* the re-sync. A miss and an
extra-tap recovery rarely share a shift by construction, which this prints rather than hides.

    python3 tools/analysis/combocue.py [--out build/combocue.txt]
"""
from __future__ import annotations

import bisect
import collections
import csv
import json
import os
import pickle
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
import recovery as R  # noqa: E402
import streams as S  # noqa: E402


def pushback_chains(objects, presses, snap, beat):
    """(q, k, end, t_recovery) per pushback shift; k is the circle after the chain or None."""
    r = S.classify(objects, ["Great"] * len(objects), [None] * len(objects), presses, snap, beat)
    if r["verdict"] != "ok":
        return
    judge = r["judge"]
    by_target = {}
    for t, outcome, target in presses:
        if outcome == "hit" and target is not None:
            by_target.setdefault(round(target, 1), t)
    n, k = len(objects), 0
    while k < n:
        if judge[k] != "next" or "self" not in judge[:k]:
            k += 1
            continue
        q = k
        while k < n and judge[k] == "next":
            k += 1
        if k >= n:
            yield q, None, "not re-synced", None
        elif judge[k] == "none":
            yield q, k, "expired", None
        elif judge[k] == "extra":
            yield q, k, "extra tap", by_target.get(round(objects[k], 1))
        else:
            yield q, k, "other", None


def main(out_path):
    with open(S.CACHE, "rb") as fh:
        runs = pickle.load(fh)
    presses = S.load_presses({r for r, _ in runs})
    rate = R.rates()
    md5 = {os.path.basename(e["Path"]): e["BeatmapMd5"] for e in json.load(open("build/corpus.json"))}
    misses = collections.defaultdict(list)    # replay -> sorted (objectStart, judgementTime, sound)
    with open("build/combo.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["result"] == "Miss" and r["kind"] in ("HitCircle", "SliderHeadCircle"):
                misses[r["replay"]].append((float(r["objectStart"]), float(r["judgementTime"]), r["sound"] == "1"))
    for v in misses.values():
        v.sort()

    rows = []   # (cls, end, latency_ms, md5, period)
    for replay, events in runs:
        ps = presses.get(replay)
        m = R.STAMP.search(replay)
        if ps is None or m is None:
            continue
        period = "pre 2026-05-10" if m.group(1) < "2026-05-10" else "from 2026-05-10"
        objects = [e[0] for e in events]
        beat, snap = events[0][1], events[0][2]
        interval = snap * beat
        lo = bisect.bisect_left(ps, (objects[0] - interval,))
        hi = bisect.bisect_right(ps, (objects[-1] + interval, "~"))
        ms = misses.get(replay, [])
        rr = rate.get(replay, 1.0)
        for q, k, end, t_rec in pushback_chains(objects, ps[lo:hi], snap, beat):
            last = objects[k] if k is not None else objects[-1]
            a = bisect.bisect_left(ms, (objects[q] - 0.5,))
            first = ms[a] if a < len(ms) and ms[a][0] <= last + 0.5 else None
            cls = "NO-MISS" if first is None else "SOUND" if first[2] else "SILENT"
            lat = (t_rec - first[1]) / rr if (first is not None and t_rec is not None) else None
            rows.append((cls, end, lat, md5.get(replay), period))

    lines = []
    p = lines.append
    for period in (None, "pre 2026-05-10", "from 2026-05-10"):
        p(f"\n{period or 'all'}")
        for cls in ("SOUND", "SILENT", "NO-MISS"):
            sel = [r for r in rows if r[0] == cls and (period is None or r[4] == period)]
            if not sel:
                continue
            rate_x, (lo_, hi_) = R.boot_rate([(r[3], r[1] == "extra tap") for r in sel], None)
            ends = collections.Counter(r[1] for r in sel)
            lats = sorted(r[2] for r in sel if r[2] is not None)
            lat = ""
            if len(lats) >= 4:
                qq = statistics.quantiles(lats, n=4)
                lat = f"  miss->recovery {statistics.median(lats):.0f} ms (IQR {qq[0]:.0f}-{qq[2]:.0f}, n={len(lats)})"
            elif lats:
                lat = f"  miss->recovery n={len(lats)}"
            p(f"  {cls:<8} n={len(sel):6}  extra tap {100 * rate_x:5.1f}% [{100 * lo_:.1f},{100 * hi_:.1f}]"
              f"  expired {100 * ends['expired'] / len(sel):5.1f}%  other {100 * ends['other'] / len(sel):4.1f}%{lat}")
    text = "\n".join(lines).lstrip("\n") + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/combocue.txt")
